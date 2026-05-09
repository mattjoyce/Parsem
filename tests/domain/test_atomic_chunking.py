"""Golden + invariant tests for the Phase 1 atomic chunking substrate.

Spec: AtomicChunkingPhase1.md §Test Strategy. Every test runs the full
pipeline (ParsedBlock → AtomicPiece → PreprocessedPiece → ChunkPlan →
ChunkRecord) and asserts on substrate invariants — not just the visible
chunk shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest

from parsem.domain.atomic import (
    AtomicPiece,
    AtomicRules,
    build_atomic_pieces,
    validate_pieces,
)
from parsem.domain.materialize import (
    ChunkRecord,
    derive_sections,
    materialize_chunks,
)
from parsem.domain.preprocessed import PreprocessedPiece, preprocess_pieces
from parsem.domain.strategies import ChunkingRuleset, ChunkPlan, validate_chunk_plan
from parsem.domain.strategies.current_reading_time import CurrentReadingTimeStrategy
from parsem.parse.line_index import LineIndex
from parsem.parse.markdown_parse import parse
from parsem.store.revisions import DocumentRevision, compute_content_hash

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
WELCOME = Path(__file__).resolve().parents[2] / "data" / "welcome.md"


def _welcome_text() -> str:
    return WELCOME.read_text(encoding="utf-8")


def _run(text: str, rules: ChunkingRuleset | None = None) -> tuple[
    list[AtomicPiece],
    list[PreprocessedPiece],
    ChunkPlan,
    list[ChunkRecord],
    DocumentRevision,
]:
    rules = rules or ChunkingRuleset()
    line_index = LineIndex.from_text(text)
    revision = DocumentRevision(
        id=1,
        document_id=1,
        full_text=text,
        content_hash=compute_content_hash(text),
        line_index=line_index,
        created_at=T0,
    )
    blocks = parse(text)
    pieces = build_atomic_pieces(blocks, rules.atomic_rules, text, line_index)
    validate_pieces(pieces, text)
    preprocessed = preprocess_pieces(pieces, rules.reading_rules)
    plan = CurrentReadingTimeStrategy().plan(preprocessed, rules)
    validate_chunk_plan(plan, preprocessed)
    chunks = materialize_chunks(plan, revision, pieces, rules)
    return pieces, preprocessed, plan, chunks, revision


# --- atomic piece builder ---------------------------------------------------


def test_paragraph_atomic_at_sentence_grain() -> None:
    pieces, _, _, _, _ = _run("Foo bar. Baz qux.\n")
    assert [p.kind for p in pieces] == ["sentence", "sentence"]
    assert [p.text_snapshot for p in pieces] == ["Foo bar. ", "Baz qux.\n"]


def test_paragraph_atomic_at_paragraph_grain_when_configured() -> None:
    rules = ChunkingRuleset(
        atomic_rules=AtomicRules(paragraph_atomicity="paragraph"),
    )
    pieces, _, _, _, _ = _run("Foo bar. Baz qux.\n", rules)
    assert [p.kind for p in pieces] == ["paragraph"]


def test_consecutive_list_items_fuse_into_list_run() -> None:
    pieces, _, _, _, _ = _run("- one\n- two\n- three\n")
    assert [p.kind for p in pieces] == ["list_run"]
    assert pieces[0].text_snapshot.startswith("- one")
    assert pieces[0].text_snapshot.rstrip().endswith("- three")


def test_list_atomicity_item_keeps_each_item_as_its_own_piece() -> None:
    rules = ChunkingRuleset(atomic_rules=AtomicRules(list_atomicity="item"))
    pieces, _, _, _, _ = _run("- one\n- two\n", rules)
    assert [p.kind for p in pieces] == ["list_item", "list_item"]


def test_code_block_is_one_piece() -> None:
    pieces, _, _, _, _ = _run("```\nprint(1)\nprint(2)\n```\n")
    assert [p.kind for p in pieces] == ["code_block"]


def test_blockquote_is_one_piece() -> None:
    pieces, _, _, _, _ = _run("> a quote\n> spanning lines\n")
    assert [p.kind for p in pieces] == ["blockquote"]


def test_heading_is_one_piece_with_level() -> None:
    pieces, _, _, _, _ = _run("## Subheading\n")
    assert [p.kind for p in pieces] == ["heading"]
    assert pieces[0].heading_level == 2


def test_validate_pieces_rejects_offset_drift() -> None:
    text = "Foo bar.\n"
    line_index = LineIndex.from_text(text)
    pieces = build_atomic_pieces(parse(text), AtomicRules(), text, line_index)
    # Tamper with the snapshot to simulate drift between snapshot and revision.
    bad = type(pieces[0])(**{**pieces[0].__dict__, "text_snapshot": "tampered"})
    with pytest.raises(AssertionError, match="snapshot mismatch"):
        validate_pieces([bad], text)


# --- planner: deterministic time-based chunking ----------------------------


def test_paragraph_packs_within_budget() -> None:
    text = "First sentence. Second sentence. Third sentence.\n"
    _, _, _, chunks, _ = _run(text)
    # All three fit easily in 30s default budget — one chunk.
    assert len(chunks) == 1
    assert chunks[0].text == text


def test_oversized_run_splits_at_budget_boundary() -> None:
    sentence = "word " * 80 + "end. "  # ~22s at 220 wpm
    text = (sentence * 3).rstrip() + "\n"
    _, _, _, chunks, _ = _run(text)
    assert len(chunks) >= 2, "oversized prose must split"
    for c in chunks:
        # Each chunk should respect (roughly) the 30s budget — single
        # sentences may exceed it but never two-+ sentence packs.
        if c.text.count(". ") > 1:
            assert c.estimated_read_seconds <= 30.0 + 1.0


def test_consecutive_paragraphs_pack_across_boundaries() -> None:
    text = "First.\n\nSecond.\n\nThird.\n"
    _, _, _, chunks, _ = _run(text)
    # All three short paragraphs should pack into one chunk under default budget.
    assert len(chunks) == 1
    assert "First" in chunks[0].text
    assert "Third" in chunks[0].text


def test_heading_attaches_forward_into_following_prose() -> None:
    text = "## Title\n\nFollow-up sentence.\n"
    _, _, _, chunks, _ = _run(text)
    assert len(chunks) == 1
    assert chunks[0].lead_token_type == "heading"
    assert chunks[0].lead_heading_level == 2
    assert "Follow-up" in chunks[0].text


def test_code_block_is_one_chunk_even_after_heading() -> None:
    text = "## Code\n\n```\nprint('hi')\n```\n"
    _, _, _, chunks, _ = _run(text)
    kinds = [c.lead_token_type for c in chunks]
    assert "heading" in kinds
    assert "code" in kinds
    code_chunks = [c for c in chunks if c.lead_token_type == "code"]
    assert len(code_chunks) == 1
    assert "print('hi')" in code_chunks[0].text


def test_list_run_is_one_chunk() -> None:
    text = "- alpha\n- beta\n- gamma\n"
    _, _, _, chunks, _ = _run(text)
    assert len(chunks) == 1
    assert chunks[0].lead_token_type == "list_item"
    assert "alpha" in chunks[0].text
    assert "gamma" in chunks[0].text


def test_colon_lead_in_absorbs_previous_paragraph_into_list() -> None:
    text = "Here are three items:\n\n- one\n- two\n- three\n"
    _, _, _, chunks, _ = _run(text)
    assert len(chunks) == 1
    assert "Here are three items:" in chunks[0].text
    assert "- one" in chunks[0].text


def test_non_colon_paragraph_does_not_absorb_into_list() -> None:
    text = "Not a lead-in.\n\n- one\n- two\n"
    _, _, _, chunks, _ = _run(text)
    assert len(chunks) == 2
    assert chunks[0].lead_token_type == "paragraph"
    assert chunks[1].lead_token_type == "list_item"


def test_blockquote_is_its_own_chunk() -> None:
    text = "Some prose.\n\n> a quote.\n\nMore prose.\n"
    _, _, _, chunks, _ = _run(text)
    bq = [c for c in chunks if c.lead_token_type == "blockquote"]
    assert len(bq) == 1
    assert "a quote" in bq[0].text


# --- materialization invariants --------------------------------------------


def test_chunk_text_equals_revision_slice_for_every_chunk() -> None:
    text = _welcome_text()
    _, _, _, chunks, revision = _run(text)
    for c in chunks:
        slice_text = revision.full_text[c.source_offset_start:c.source_offset_end]
        assert c.text == slice_text, f"chunk {c.position} text != revision slice"


def test_chunk_offsets_are_strictly_ordered() -> None:
    text = _welcome_text()
    _, _, _, chunks, _ = _run(text)
    for prev, cur in pairwise(chunks):
        assert prev.source_offset_end <= cur.source_offset_start, (
            f"chunk {prev.position}→{cur.position} overlaps"
        )


def test_chunk_positions_are_dense_zero_indexed() -> None:
    text = _welcome_text()
    _, _, _, chunks, _ = _run(text)
    assert [c.position for c in chunks] == list(range(len(chunks)))


def test_every_revealable_piece_appears_in_exactly_one_chunk() -> None:
    text = _welcome_text()
    pieces, _, _, chunks, _ = _run(text)
    seen: set[int] = set()
    for c in chunks:
        for ord_ in c.piece_ordinals:
            assert ord_ not in seen, f"piece {ord_} in two chunks"
            seen.add(ord_)
    assert seen == {p.ordinal for p in pieces}


# --- determinism -----------------------------------------------------------


def test_same_revision_and_rules_produce_identical_pieces() -> None:
    text = "## Title\n\nFoo. Bar.\n\n- one\n- two\n"
    rules = ChunkingRuleset()
    p1 = build_atomic_pieces(parse(text), rules.atomic_rules, text, LineIndex.from_text(text))
    p2 = build_atomic_pieces(parse(text), rules.atomic_rules, text, LineIndex.from_text(text))
    assert p1 == p2


def test_same_revision_and_rules_produce_identical_chunks() -> None:
    text = _welcome_text()
    _, _, _, c1, _ = _run(text)
    _, _, _, c2, _ = _run(text)
    assert [(c.position, c.text_hash) for c in c1] == [
        (c.position, c.text_hash) for c in c2
    ]


def test_rules_hash_changes_when_a_rule_changes() -> None:
    a = ChunkingRuleset()
    b = ChunkingRuleset(atomic_rules=AtomicRules(list_atomicity="item"))
    assert a.rules_hash() != b.rules_hash()


# --- section derivation ---------------------------------------------------


def test_sections_cover_every_chunk() -> None:
    text = _welcome_text()
    _, _, _, chunks, _ = _run(text)
    sections = derive_sections(chunks)
    covered = set()
    for s in sections:
        for pos in range(s.start_chunk_position, s.end_chunk_position + 1):
            assert pos not in covered, f"chunk {pos} in two sections"
            covered.add(pos)
    assert covered == {c.position for c in chunks}


def test_first_heading_starts_a_section() -> None:
    text = "Prologue paragraph.\n\n## Heading\n\nBody.\n"
    _, _, _, chunks, _ = _run(text)
    sections = derive_sections(chunks)
    # Prologue (no heading) + heading-bounded section.
    assert len(sections) == 2
    assert sections[0].heading_chunk_position is None
    assert sections[1].heading_level == 2


# --- plan validation -------------------------------------------------------


def test_validate_chunk_plan_rejects_missing_piece() -> None:
    """A plan that loses a piece must fail validation rather than silently
    producing a chunk set with gaps."""
    text = "Foo. Bar.\n"
    rules = ChunkingRuleset()
    pieces = build_atomic_pieces(parse(text), rules.atomic_rules, text, LineIndex.from_text(text))
    preprocessed = preprocess_pieces(pieces, rules.reading_rules)
    # Build a plan that drops the second piece.
    from parsem.domain.strategies import ChunkPlan, PlannedChunk
    bad_plan = ChunkPlan(planned_chunks=[
        PlannedChunk(
            ordinal=0,
            piece_ordinals=[pieces[0].ordinal],
            estimated_read_seconds=1.0,
            lead_piece_ordinal=pieces[0].ordinal,
            reason="prose_budget",
        ),
    ])
    with pytest.raises(AssertionError, match="missing pieces"):
        validate_chunk_plan(bad_plan, preprocessed)
