"""Arrivals — the pure-core decisions behind the ductile-driven ingest
pipeline (ADR 0002).

Two functions, one per direction of the seam:

- `process_raw_arrival(path, ...)` — ductile knocks here when something
  lands in `inbound/raw/`. Returns a `RawArrivalResult` whose `action`
  tells the caller (the ductile DSL) what to do next:

    * `ingested`         — `.md`; we parsed and persisted in place
    * `submit_to_marker` — `.pdf`; we inserted a converting row, moved
                            the PDF to originals/<id>.pdf for a stable
                            bind-mount source, and want ductile to call
                            Marker with the returned doc_id + source_path
    * `duplicate`        — content hash already in the library; no-op
    * `unsupported`      — anything else; recorded as a fail-row so the
                            user sees their drop landed and didn't take

- `process_converted_arrival(path, ...)` — ductile knocks here when
  Marker drops a `<doc_id>.md` into `inbound/converted/`. We load the
  existing converting row (filename carries the doc_id), parse the
  markdown, link it via parse_and_persist, and persist the sidecar
  metadata as an extraction_runs row.

Both functions are pure-shaped: filesystem and SQL effects only,
deterministic given inputs. The route layer is a thin adapter — auth,
JSON parsing, HTTP status mapping. That keeps these testable without a
TestClient, and lets the route be reshaped without touching decisions.

Idempotency: under ductile retry, raw arrivals dedup by content hash;
converted arrivals dedup by checking the doc row's status (already
ready → no-op return).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from parsem.store.documents import (
    find_document_id_by_source_hash,
    insert_document,
    load_document,
    mark_document_failed,
    update_document_original_path,
)
from parsem.store.extraction_runs import insert_extraction_run
from parsem.web.ingest import parse_and_persist

_LOG = logging.getLogger(__name__)

# What we accept on the raw side. PDF goes to Marker; MD ingests in
# place. Anything else is unsupported. Kept as sets for cheap membership.
_MD_EXTS = {".md"}
_PDF_EXTS = {".pdf"}

RawAction = Literal["ingested", "submit_to_marker", "duplicate", "unsupported"]
ConvertedAction = Literal["ingested", "duplicate", "missing_doc", "failed"]


@dataclass(frozen=True)
class RawArrivalResult:
    """Outcome of `/ingest/raw-arrived`. The route serializes this to
    JSON; the ductile DSL branches on `action`."""

    action: RawAction
    document_id: int | None
    # Only set when action == "submit_to_marker": ductile uses these
    # two fields to call the Marker plugin's submit endpoint.
    doc_id: str | None = None
    source_path: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ConvertedArrivalResult:
    """Outcome of `/ingest/converted-arrived`."""

    action: ConvertedAction
    document_id: int | None
    reason: str | None = None


def _now_factory() -> datetime:
    return datetime.now(UTC)


def _sha256_file(path: Path) -> str:
    """Streaming SHA-256 — small constant memory regardless of file
    size. Used as the dedup key for both .md and .pdf arrivals."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def process_raw_arrival(
    path: Path,
    *,
    conn: sqlite3.Connection,
    originals_dir: Path,
    now_factory: Callable[[], datetime] = _now_factory,
) -> RawArrivalResult:
    """Synchronous core for `/ingest/raw-arrived`. See module docstring
    for the action vocabulary. Caller (the route) handles auth + HTTP.
    """
    if not path.exists():
        # Ductile fired the knock but the file vanished. Could be a
        # racy double-event; treat as a no-op rather than 500.
        return RawArrivalResult(
            action="unsupported", document_id=None, reason="file not found"
        )

    suffix = path.suffix.lower()
    source_hash = _sha256_file(path)

    # Dedup BEFORE any side effect so retries are cheap and safe.
    existing = find_document_id_by_source_hash(conn, source_hash)
    if existing is not None:
        # Leave the file alone — could be a manual re-drop. The library
        # already shows the existing doc; we just confirm.
        return RawArrivalResult(action="duplicate", document_id=existing)

    if suffix in _MD_EXTS:
        return _ingest_markdown(
            path, conn=conn, originals_dir=originals_dir,
            source_hash=source_hash, now=now_factory(),
        )
    if suffix in _PDF_EXTS:
        return _stage_pdf_for_marker(
            path, conn=conn, originals_dir=originals_dir,
            source_hash=source_hash, now=now_factory(),
        )
    return _record_unsupported(
        path, conn=conn, originals_dir=originals_dir,
        source_hash=source_hash, now=now_factory(),
    )


def _ingest_markdown(
    path: Path,
    *,
    conn: sqlite3.Connection,
    originals_dir: Path,
    source_hash: str,
    now: datetime,
) -> RawArrivalResult:
    """Cycle-1 path, now reached via the ductile knock instead of a
    watcher event. Insert row → move file → parse. Failure modes
    reconcile the doc row so partial state never strands."""
    title = path.stem
    document_id = insert_document(
        conn,
        title=title,
        original_path=str(path),
        status="processing",
        source_type="markdown",
        source_hash=source_hash,
        now=now,
    )
    try:
        text = path.read_text(encoding="utf-8")
        target = originals_dir / f"{document_id}.md"
        path.rename(target)
        update_document_original_path(
            conn, document_id, original_path=str(target), now=now
        )
        parse_and_persist(conn, document_id=document_id, text=text, now=now)
    except Exception as exc:
        _LOG.exception("arrivals: md ingest failed for %s", path)
        mark_document_failed(
            conn, document_id, reason=f"Ingest failed: {exc}", now=now
        )
    return RawArrivalResult(action="ingested", document_id=document_id)


def _stage_pdf_for_marker(
    path: Path,
    *,
    conn: sqlite3.Connection,
    originals_dir: Path,
    source_hash: str,
    now: datetime,
) -> RawArrivalResult:
    """Insert a converting row and move the PDF to a stable path Marker
    can bind-mount. Cycle 3 will formalize this as 'provenance' for the
    re-ingest UI; cycle 2 already needs the stable path so doing both at
    once costs nothing extra."""
    title = path.stem
    document_id = insert_document(
        conn,
        title=title,
        original_path=str(path),
        status="converting",
        source_type="pdf",
        source_hash=source_hash,
        now=now,
    )
    try:
        target = originals_dir / f"{document_id}.pdf"
        path.rename(target)
        update_document_original_path(
            conn, document_id, original_path=str(target), now=now
        )
    except Exception as exc:
        _LOG.exception("arrivals: pdf staging failed for %s", path)
        mark_document_failed(
            conn, document_id, reason=f"PDF staging failed: {exc}", now=now
        )
        return RawArrivalResult(
            action="unsupported", document_id=document_id, reason=str(exc)
        )
    return RawArrivalResult(
        action="submit_to_marker",
        document_id=document_id,
        doc_id=str(document_id),
        source_path=str(target),
    )


def _record_unsupported(
    path: Path,
    *,
    conn: sqlite3.Connection,
    originals_dir: Path,
    source_hash: str,
    now: datetime,
) -> RawArrivalResult:
    """Surface unknown-extension drops as fail-rows so the user sees
    them in the library rather than having them vanish silently. The
    file is moved out of inbound/raw/ so the next arrival event for the
    same path can't re-fire."""
    title = path.stem
    document_id = insert_document(
        conn,
        title=title,
        original_path=str(path),
        status="processing",
        source_hash=source_hash,
        now=now,
    )
    target = originals_dir / f"{document_id}{path.suffix}"
    path.rename(target)
    update_document_original_path(
        conn, document_id, original_path=str(target), now=now
    )
    reason = f"Unsupported file type: {path.suffix or '(none)'}"
    mark_document_failed(conn, document_id, reason=reason, now=now)
    return RawArrivalResult(
        action="unsupported", document_id=document_id, reason=reason
    )


def process_converted_arrival(
    path: Path,
    *,
    conn: sqlite3.Connection,
    originals_dir: Path,
    now_factory: Callable[[], datetime] = _now_factory,
) -> ConvertedArrivalResult:
    """Synchronous core for `/ingest/converted-arrived`. Marker writes
    `<doc_id>.md` last under its atomic-write contract, so by the time
    ductile filewatch fires this knock the .md, sidecar JSON, and
    images dir are all in place."""
    if not path.exists():
        return ConvertedArrivalResult(
            action="failed", document_id=None, reason="file not found"
        )
    if path.suffix.lower() != ".md":
        return ConvertedArrivalResult(
            action="failed", document_id=None,
            reason=f"expected .md, got {path.suffix or '(none)'}",
        )

    document_id = _doc_id_from_filename(path)
    if document_id is None:
        return ConvertedArrivalResult(
            action="failed", document_id=None,
            reason=f"filename is not <doc_id>.md: {path.name}",
        )

    doc = load_document(conn, document_id)
    if doc is None:
        return ConvertedArrivalResult(
            action="missing_doc", document_id=document_id,
            reason="no document row for this doc_id",
        )
    if doc.status == "ready":
        # Retry under contention (ductile filewatch fired twice).
        # The doc is already ingested; nothing to do.
        return ConvertedArrivalResult(action="duplicate", document_id=document_id)

    now = now_factory()
    try:
        text = path.read_text(encoding="utf-8")
        # Marker's atomic-write contract: <doc_id>.md is renamed last,
        # so by here the sidecar is already in place. Tolerate missing
        # sidecar though — Marker is still allowed to win without one.
        sidecar = _read_sidecar(path)
        if sidecar is not None:
            insert_extraction_run(
                conn,
                document_id=document_id,
                source_type="pdf",
                extractor_name="marker",
                extractor_version=str(sidecar.get("marker_version", "unknown")),
                source_path=str(sidecar.get("source", "")),
                params={
                    "duration_seconds": sidecar.get("duration_seconds"),
                    "image_count": sidecar.get("image_count"),
                    "completed_at": sidecar.get("completed_at"),
                },
                now=now,
            )
        parse_and_persist(conn, document_id=document_id, text=text, now=now)
    except Exception as exc:
        _LOG.exception("arrivals: converted ingest failed for %s", path)
        mark_document_failed(
            conn, document_id, reason=f"Converted ingest failed: {exc}", now=now
        )
        return ConvertedArrivalResult(
            action="failed", document_id=document_id, reason=str(exc)
        )

    return ConvertedArrivalResult(action="ingested", document_id=document_id)


def _doc_id_from_filename(path: Path) -> int | None:
    """Marker writes `<doc_id>.md` where doc_id is the integer Parsem
    handed it at submit. Anything else is a sign the filewatch caught
    a file that isn't ours — return None and let the caller fail."""
    try:
        return int(path.stem)
    except ValueError:
        return None


def _read_sidecar(md_path: Path) -> dict[str, object] | None:
    """Look for `<doc_id>.json` next to the .md. Returns the parsed
    dict or None if absent / unreadable. Sidecar is metadata only;
    its absence is not fatal."""
    sidecar_path = md_path.with_suffix(".json")
    if not sidecar_path.exists():
        return None
    try:
        return json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _LOG.warning("arrivals: sidecar unreadable: %s", sidecar_path)
        return None
