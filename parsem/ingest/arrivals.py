"""Arrivals — the pure-core decisions behind the ductile-driven ingest
pipeline (ADR 0002).

Two functions, one per direction of the seam:

- `process_raw_arrival(path, ...)` — ductile knocks here when something
  lands in `inbound/raw/`. Returns a `RawArrivalResult` whose `action`
  tells the caller (the ductile DSL) what to do next:

    * `ingested`         — `.md`; we parsed and persisted in place
    * `submit_to_marker` — `.pdf`; we inserted a converting row, moved
                            the PDF to originals/<id>/source.pdf for a
                            stable bind-mount source, and want ductile
                            to call Marker with doc_id + source_path
    * `duplicate`        — content hash already in the library; no-op
    * `unsupported`      — anything else; recorded as a fail-row so the
                            user sees their drop landed and didn't take

- `process_converted_arrival(path, ...)` — ductile knocks here when
  Marker drops a `<doc_id>.md` into `inbound/converted/`. We load the
  existing converting row (filename carries the doc_id), relocate
  Marker's cluster (`<doc_id>.md`, `<doc_id>.json`, `<doc_id>_images/`)
  into `originals/<doc_id>/` as `document.md` / `extraction.json` /
  `images/` — rewriting the markdown's image refs from `<doc_id>_images/`
  to `images/` — then parse it via parse_and_persist and record the
  sidecar metadata as an extraction_runs row.

A stored document is a directory (`originals/<doc_id>/`), not a
file-prefix cluster — see `parsem.ingest.layout`.

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

from parsem.ingest import layout
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
        layout.document_dir(originals_dir, document_id).mkdir(
            parents=True, exist_ok=True
        )
        target = layout.markdown_path(originals_dir, document_id)
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
    """Insert a converting row and move the PDF into the document's
    directory as `source.pdf` — a stable path Marker can bind-mount and
    the provenance copy cycle 3's re-ingest button will re-convert from.
    The converted markdown lands alongside it as `document.md` once
    `process_converted_arrival` fires."""
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
        layout.document_dir(originals_dir, document_id).mkdir(
            parents=True, exist_ok=True
        )
        target = layout.source_path(originals_dir, document_id, path.suffix)
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
    layout.document_dir(originals_dir, document_id).mkdir(parents=True, exist_ok=True)
    target = layout.source_path(originals_dir, document_id, path.suffix)
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
    ductile filewatch fires this knock the `.md`, sidecar `.json`, and
    `<doc_id>_images/` directory are all in place.

    We relocate that cluster into `originals/<doc_id>/` — `document.md`,
    `extraction.json`, `images/` — rewriting the markdown's image refs
    from `<doc_id>_images/` to `images/` so a renderer resolves them
    against the reader page URL (served by GET /documents/{id}/images/).
    """
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
        # Retry under contention (ductile filewatch fired twice). The
        # doc is already ingested — and we may have already moved the
        # staging .md out from under this knock — so this check has to
        # come before the file-existence one.
        return ConvertedArrivalResult(action="duplicate", document_id=document_id)
    if not path.exists():
        return ConvertedArrivalResult(
            action="failed", document_id=document_id, reason="file not found"
        )

    now = now_factory()
    try:
        raw_text = path.read_text(encoding="utf-8")
        # Marker emits image refs as `<doc_id>_images/<file>`; once the
        # dir lands at `originals/<doc_id>/images/` the refs need to say
        # `images/<file>` so the reader's <img src> resolves under
        # /documents/{id}/images/. Specific enough a string that a blind
        # replace is safe.
        markdown_text = raw_text.replace(
            f"{document_id}_{layout.IMAGES_DIRNAME}/", f"{layout.IMAGES_DIRNAME}/"
        )
        # Marker's atomic-write contract: <doc_id>.md is renamed last,
        # so by here the sidecar is already in place. Tolerate a missing
        # sidecar though — Marker is allowed to win without one.
        sidecar = _read_sidecar(path)
        _relocate_marker_cluster(path, originals_dir, document_id, markdown_text)
        new_md_path = layout.markdown_path(originals_dir, document_id)
        update_document_original_path(
            conn, document_id, original_path=str(new_md_path), now=now
        )
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
        parse_and_persist(
            conn, document_id=document_id, text=markdown_text, now=now
        )
    except Exception as exc:
        _LOG.exception("arrivals: converted ingest failed for %s", path)
        mark_document_failed(
            conn, document_id, reason=f"Converted ingest failed: {exc}", now=now
        )
        return ConvertedArrivalResult(
            action="failed", document_id=document_id, reason=str(exc)
        )

    return ConvertedArrivalResult(action="ingested", document_id=document_id)


def _relocate_marker_cluster(
    md_path: Path,
    originals_dir: Path,
    document_id: int,
    markdown_text: str,
) -> None:
    """Move Marker's `inbound/converted/` output into the document's
    directory: `<doc_id>_images/` → `images/`, `<doc_id>.json` →
    `extraction.json`, and the (ref-rewritten) markdown → `document.md`.
    The staging `.md` is removed last. `originals/<doc_id>/` already
    exists from the PDF-staging step (it holds `source.pdf`)."""
    doc_dir = layout.document_dir(originals_dir, document_id)
    doc_dir.mkdir(parents=True, exist_ok=True)

    staged_images = md_path.parent / f"{document_id}_{layout.IMAGES_DIRNAME}"
    if staged_images.is_dir():
        staged_images.rename(layout.images_dir(originals_dir, document_id))

    staged_sidecar = md_path.with_suffix(".json")
    if staged_sidecar.exists():
        staged_sidecar.rename(layout.extraction_json_path(originals_dir, document_id))

    layout.markdown_path(originals_dir, document_id).write_text(
        markdown_text, encoding="utf-8"
    )
    md_path.unlink(missing_ok=True)


def _doc_id_from_filename(path: Path) -> int | None:
    """Marker writes `<doc_id>.md` where doc_id is the integer Parsem
    handed it at submit. Anything else is a sign the filewatch caught
    a file that isn't ours — return None and let the caller fail."""
    try:
        return int(path.stem)
    except ValueError:
        return None


def _read_sidecar(md_path: Path) -> dict[str, object] | None:
    """Look for `<doc_id>.json` next to the .md in `inbound/converted/`.
    Returns the parsed dict or None if absent / unreadable. Sidecar is
    metadata only; its absence is not fatal."""
    sidecar_path = md_path.with_suffix(".json")
    if not sidecar_path.exists():
        return None
    try:
        return json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _LOG.warning("arrivals: sidecar unreadable: %s", sidecar_path)
        return None
