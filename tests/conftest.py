"""Shared test constants and substrate-pipeline helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from parsem.domain.atomic import build_atomic_pieces
from parsem.domain.chunking import ChunkingRuleset
from parsem.domain.chunking.current_reading_time import CurrentReadingTimeStrategy
from parsem.domain.materialize import Chunk, Section, derive_sections, materialize_chunks
from parsem.domain.preprocessed import preprocess_pieces
from parsem.parse.line_index import LineIndex
from parsem.parse.markdown_parse import parse
from parsem.store.revisions import DocumentRevision

# Anchor timestamp used across domain/store tests for deterministic time math.
# Tests build relative timestamps via T0 + timedelta(...).
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def chunk_via_substrate(text: str) -> tuple[list[Chunk], list[Section]]:
    """Run the full atomic-chunking substrate pipeline on `text` and
    return (chunks, sections). Replaces the legacy `parsem.domain.chunking
    .chunk()` function for test fixtures that just want "parse this
    text into chunks" — claude-axx.1.

    Pure helper, no IO. The DocumentRevision is stub-shaped because
    materialize_chunks only reads `full_text` and `line_index` from it."""
    rules = ChunkingRuleset()
    blocks = parse(text)
    line_index = LineIndex.from_text(text)
    pieces = build_atomic_pieces(blocks, rules.atomic_rules, text, line_index)
    preprocessed = preprocess_pieces(pieces, rules.reading_rules)
    plan = CurrentReadingTimeStrategy().plan(preprocessed, rules)
    revision = DocumentRevision(
        id=0,
        document_id=0,
        full_text=text,
        content_hash="test-stub",
        line_index=line_index,
        created_at=T0,
    )
    chunks = materialize_chunks(plan, revision, pieces, rules)
    sections = derive_sections(chunks)
    return chunks, sections
