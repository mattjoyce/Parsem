"""Filesystem-watcher on `inbound/raw/` — the unifying mechanism that
turns drops (web upload, manual NAS drop, URL fetch, CLI add) into
ingested documents.

Spec: ADR docs/adr/0001-nas-ingest-pipeline.md.

Cycle 1: handles `.md` only — read text, call `parse_and_persist`,
move the file to `originals/<doc_id>.md`. Anything else (e.g. `.pdf`)
is logged and left in place; cycle 2 routes those to Marker.

Lifecycle:
- `start()` runs an initial sweep (catches files dropped while the
  server was down) then begins live monitoring via watchdog.
- `stop()` halts the observer cleanly. Both are wired to FastAPI's
  lifespan context manager.

Test seam: `process_file(path)` is the synchronous core. Tests call
it directly to avoid threading/timing issues; the watcher's only job
in production is to drive `process_file` from filesystem events.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from parsem.store.documents import (
    insert_document,
    mark_document_failed,
    update_document_original_path,
)
from parsem.web.ingest import parse_and_persist

_LOG = logging.getLogger(__name__)

# Cycle 2 wires .pdf to Marker; until then we leave PDFs in place
# (don't fail-row them — the cycle 2 watcher will route them).
_DEFER_TO_CYCLE_2 = {".pdf"}
_INGESTABLE = {".md"}

# Pluggable for tests — production wires the SQLite-backed connection.
ConnFactory = Callable[[], sqlite3.Connection]


def process_file(
    path: Path,
    *,
    conn: sqlite3.Connection,
    originals_dir: Path,
    now_factory: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int | None:
    """Synchronous core of the watcher: ingest one file from
    inbound/raw/ and move it to originals/<doc_id>.md.

    Returns the document id on success or recoverable failure (parse
    errors mark the row 'failed' but the doc id is returned so the
    library can show it). Returns None when the file was skipped
    (missing, deferred to cycle 2, or extension unrecognized).
    Unknown extensions get a failed row + reason so they're visible
    in the library rather than vanishing.
    """
    if not path.exists():
        return None
    suffix = path.suffix.lower()
    if suffix in _DEFER_TO_CYCLE_2:
        _LOG.info("watcher: deferring %s to cycle 2 (Marker)", path)
        return None
    if suffix not in _INGESTABLE:
        # Unknown type — surface as a failed row instead of silently
        # ignoring so the user sees their drop landed and didn't take.
        return _record_unsupported(
            path, conn=conn, originals_dir=originals_dir, now=now_factory()
        )

    title = path.stem
    now = now_factory()
    document_id = insert_document(
        conn,
        title=title,
        original_path=str(path),
        status="processing",
        now=now,
    )
    # Anything from here on must reconcile the doc row on failure —
    # an unhandled exception (read_text decode error, rename across
    # filesystems, parse_and_persist crash) would otherwise strand
    # the row in 'processing'. The library would never show the
    # failure, and the user would never get a chance to retry.
    try:
        text = path.read_text(encoding="utf-8")
        target = originals_dir / f"{document_id}.md"
        path.rename(target)
        update_document_original_path(
            conn, document_id, original_path=str(target), now=now
        )
        parse_and_persist(conn, document_id=document_id, text=text, now=now)
    except Exception as exc:
        _LOG.exception("watcher: ingest failed for %s", path)
        mark_document_failed(
            conn, document_id, reason=f"Ingest failed: {exc}", now=now
        )
    return document_id


def _record_unsupported(
    path: Path,
    *,
    conn: sqlite3.Connection,
    originals_dir: Path,
    now: datetime,
) -> int:
    """Insert a failed doc row for an unknown-extension drop and move
    the file into originals/ so it isn't re-processed every sweep."""
    title = path.stem
    document_id = insert_document(
        conn,
        title=title,
        original_path=str(path),
        status="processing",
        now=now,
    )
    target = originals_dir / f"{document_id}{path.suffix}"
    path.rename(target)
    update_document_original_path(
        conn, document_id, original_path=str(target), now=now
    )
    mark_document_failed(
        conn,
        document_id,
        reason=f"Unsupported file type: {path.suffix or '(none)'}",
        now=now,
    )
    return document_id


def sweep(
    inbound_raw_dir: Path,
    *,
    conn: sqlite3.Connection,
    originals_dir: Path,
) -> int:
    """One-shot pass over `inbound/raw/` — used at startup to catch
    files dropped while the server was down. Returns the number of
    files successfully ingested."""
    if not inbound_raw_dir.exists():
        return 0
    count = 0
    for entry in sorted(inbound_raw_dir.iterdir()):
        if not entry.is_file():
            continue
        if process_file(entry, conn=conn, originals_dir=originals_dir) is not None:
            count += 1
    return count


class _RawDirHandler(FileSystemEventHandler):
    """Watchdog handler — bridges filesystem events to `process_file`.
    Only `on_created` + `on_moved` matter; we ignore modifications
    because the inbound contract is write-once-then-rename."""

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        originals_dir: Path,
    ) -> None:
        self._conn = conn
        self._originals_dir = originals_dir

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._handle(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # Files copied to a network share often arrive as a temp + rename;
        # the rename target is what we want to ingest.
        dest = getattr(event, "dest_path", None)
        if dest:
            self._handle(Path(dest))

    def _handle(self, path: Path) -> None:
        try:
            process_file(path, conn=self._conn, originals_dir=self._originals_dir)
        except Exception:
            _LOG.exception("watcher: failed to process %s", path)


def start(
    inbound_raw_dir: Path,
    *,
    conn: sqlite3.Connection,
    originals_dir: Path,
) -> Observer:
    """Run the startup sweep and return a started Observer. Caller
    owns shutdown — call `observer.stop()` + `observer.join()` on
    teardown (FastAPI lifespan handles this)."""
    sweep(inbound_raw_dir, conn=conn, originals_dir=originals_dir)
    handler = _RawDirHandler(conn=conn, originals_dir=originals_dir)
    observer: Observer = Observer()
    observer.schedule(handler, str(inbound_raw_dir), recursive=False)
    observer.start()
    _LOG.info("watcher: monitoring %s", inbound_raw_dir)
    return observer
