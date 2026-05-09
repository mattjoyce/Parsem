"""Parse-and-persist pipeline shared by upload and retry-parse.

Spec: parsem-spec.md §17.1, §17.2; AtomicChunkingPhase1.md §Implementation
Sequence. Tries to ingest a markdown payload through the atomic substrate;
on parse / build / plan / materialize exception or empty input, marks the
document `failed` with a human-readable reason and returns False.

The full pipeline (claude-axx):
  text -> DocumentRevision -> ParsedBlock[] -> AtomicPiece[]
       -> PreprocessedPiece[] -> ChunkPlan -> Chunk[] -> Section[]
       -> persist (revision, pieces, run, chunks+chunk_pieces, sections)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from parsem.domain.atomic import build_atomic_pieces, validate_pieces
from parsem.domain.materialize import derive_sections, materialize_chunks
from parsem.domain.preprocessed import preprocess_pieces
from parsem.domain.strategies import ChunkingRuleset, validate_chunk_plan
from parsem.domain.strategies.current_reading_time import CurrentReadingTimeStrategy
from parsem.parse.markdown_parse import parse
from parsem.store.documents import (
    insert_chunking_artifacts,
    mark_document_failed,
    mark_document_ready,
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
        strategy = CurrentReadingTimeStrategy()
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
