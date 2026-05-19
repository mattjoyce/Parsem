"""Parse-and-persist pipeline shared by upload, retry-parse, re-chunk.

Spec: parsem-spec.md §17.1, §17.2; AtomicChunkingPhase1.md §Implementation
Sequence. `parse_and_persist` ingests a markdown payload through the
atomic substrate; on parse / build / plan / materialize exception or
empty input it marks the document `failed` and returns False.
`reparse_document` re-runs that pipeline on a document's *stored*
markdown (claude-m4l) — used by both the `retry-parse` route (the
library "Retry" / "Re-chunk" buttons) and `parsem rechunk`.

The full pipeline (claude-axx):
  text -> DocumentRevision -> ParsedBlock[] -> AtomicPiece[]
       -> PreprocessedPiece[] -> ChunkPlan -> Chunk[] -> Section[]
       -> persist (revision, pieces, run, chunks+chunk_pieces, sections)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from parsem.config import PROJECT_ROOT
from parsem.domain.atomic import build_atomic_pieces, validate_pieces
from parsem.domain.chunking import (
    ChunkingRuleset,
    get_strategy,
    validate_chunk_plan,
)
from parsem.domain.materialize import derive_sections, materialize_chunks
from parsem.domain.preprocessed import preprocess_pieces
from parsem.ingest import layout
from parsem.parse.markdown_parse import parse
from parsem.store.documents import (
    delete_document_chunks_and_sections,
    insert_chunking_artifacts,
    load_document,
    mark_document_failed,
    mark_document_ready,
)
from parsem.store.projections_cache import (
    get_chunk_piece_hashes_for_document,
    reanchor_reading_state,
)
from parsem.store.revisions import insert_revision


def parse_and_persist(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    text: str,
    now: datetime,
) -> bool:
    """Run the full atomic-chunking pipeline and persist the artifacts.
    Returns True on success, False after recording a failure reason."""
    try:
        revision = insert_revision(
            conn, document_id=document_id, full_text=text, now=now
        )
        rules = ChunkingRuleset()
        blocks = parse(text)
        pieces = build_atomic_pieces(
            blocks, rules.atomic_rules, text, revision.line_index
        )
        validate_pieces(pieces, text)
        preprocessed = preprocess_pieces(pieces, rules.reading_rules)
        strategy = get_strategy()
        plan = strategy.plan(preprocessed, rules)
        validate_chunk_plan(plan, preprocessed)
        chunk_records = materialize_chunks(plan, revision, pieces, rules)
        section_records = derive_sections(chunk_records)
    except Exception as exc:
        mark_document_failed(
            conn, document_id, reason=f"Parse failed: {exc}", now=now
        )
        return False

    if not chunk_records:
        mark_document_failed(
            conn, document_id, reason="Document is empty.", now=now
        )
        return False

    insert_chunking_artifacts(
        conn,
        document_id=document_id,
        revision_id=revision.id,
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        rules_hash=rules.rules_hash(),
        pieces=pieces,
        chunk_records=chunk_records,
        section_records=section_records,
        now=now,
    )
    mark_document_ready(
        conn, document_id, total_chunks=len(chunk_records), now=now
    )
    return True


def _stored_markdown_path(
    originals_dir: Path, doc_original_path: str, document_id: int
) -> Path | None:
    """Where a document's source markdown lives on disk, or None if it's
    gone. Normal case: `originals/<id>/document.md`. Fallback: the
    recorded `original_path` (covers the welcome doc, whose source is the
    repo's `data/welcome.md` and has no `originals/` directory) — resolved
    against PROJECT_ROOT when it's relative."""
    primary = layout.markdown_path(originals_dir, document_id)
    if primary.exists():
        return primary
    recorded = Path(doc_original_path)
    if not recorded.is_absolute():
        recorded = PROJECT_ROOT / recorded
    return recorded if recorded.exists() else None


def reparse_document(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    originals_dir: Path,
    now: datetime,
) -> bool:
    """Re-run the parse/chunk pipeline on a document's stored markdown:
    capture the old chunks' piece hashes, wipe chunks/sections, re-parse,
    then re-anchor the reading position onto the new chunks. Returns True
    on a successful re-chunk; False (with the document marked `failed`)
    when the markdown is missing or the parse fails. Commits before
    returning — `parsem rechunk` runs this in a short-lived process, so
    the writes must be persisted here. Caller verifies the document
    exists. claude-m4l."""
    doc = load_document(conn, document_id)
    if doc is None:
        return False
    md_path = _stored_markdown_path(originals_dir, doc.original_path, document_id)
    if md_path is None:
        mark_document_failed(
            conn, document_id, reason="Original file missing.", now=now
        )
        conn.commit()
        return False
    text = md_path.read_text(encoding="utf-8")
    # Snapshot the OLD chunks' piece-hash sets before the wipe — needed to
    # re-anchor reading_state onto the new chunking_run (claude-jtu).
    old_chunk_piece_hashes = get_chunk_piece_hashes_for_document(conn, document_id)
    delete_document_chunks_and_sections(conn, document_id)
    success = parse_and_persist(conn, document_id=document_id, text=text, now=now)
    if success and old_chunk_piece_hashes:
        reanchor_reading_state(
            conn,
            document_id=document_id,
            old_chunks_piece_hashes=old_chunk_piece_hashes,
            now=now,
        )
    conn.commit()
    return success
