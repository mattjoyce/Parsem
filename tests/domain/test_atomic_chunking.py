"""Golden + invariant tests for the Phase 1 atomic chunking substrate.

Spec: AtomicChunkingPhase1.md §Test Strategy. Every test runs the full
pipeline (ParsedBlock → AtomicPiece → PreprocessedPiece → ChunkPlan →
Chunk) and asserts on substrate invariants — not just the visible
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
from parsem.domain.chunking import ChunkingRuleset, ChunkPlan, validate_chunk_plan
from parsem.domain.chunking.current_reading_time import CurrentReadingTimeStrategy
from parsem.domain.materialize import (
    Chunk,
    derive_sections,
    materialize_chunks,
)
from parsem.domain.preprocessed import (
    PreprocessedPiece,
    ReadingRules,
    preprocess_pieces,
)
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
    list[Chunk],
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


# --- horizontal rules (claude-axx.5, revised in claude-jvs.3) -------------


def test_horizontal_rule_is_atomic_piece_but_not_a_chunk() -> None:
    """HR remains an atomic piece (the parser sees it) but the chunker
    no longer emits a standalone HR chunk — UAT feedback (claude-jvs.3):
    an HR-only chunk forced the reader to spend a token to reveal what
    visually reads as a blank line."""
    text = "Before.\n\n---\n\nAfter.\n"
    pieces, _, _, chunks, _ = _run(text)
    hr_pieces = [p for p in pieces if p.kind == "horizontal_rule"]
    assert len(hr_pieces) == 1
    hr_chunks = [c for c in chunks if c.lead_token_type == "horizontal_rule"]
    assert hr_chunks == []


def test_horizontal_rule_skip_preserves_neighbouring_prose() -> None:
    """The HR is a thematic break — skipping the chunk must not fuse
    the surrounding prose into one chunk. 'Before.' and 'After.' must
    land in DIFFERENT chunks (regression: an early simplify pass
    omitted the flush and produced one fused chunk)."""
    text = "Before.\n\n---\n\nAfter.\n"
    _, _, _, chunks, _ = _run(text)
    assert len(chunks) == 2
    assert "Before." in chunks[0].text
    assert "After." not in chunks[0].text
    assert "After." in chunks[1].text
    assert "Before." not in chunks[1].text


def test_horizontal_rule_zero_read_time() -> None:
    text = "---\n"
    _, preprocessed, _, _, _ = _run(text)
    assert all(p.estimated_read_seconds == 0.0 for p in preprocessed)


def test_horizontal_rule_only_document_produces_no_chunks() -> None:
    """Pathological case: a doc that's nothing but an HR. With HR
    skipped from chunking, the chunker emits zero chunks. Reader-state
    layer guards on this elsewhere."""
    text = "---\n"
    _, _, _, chunks, _ = _run(text)
    assert chunks == []


def test_horizontal_rule_does_not_start_a_section() -> None:
    text = "Prologue.\n\n---\n\nMore prose.\n"
    _, _, _, chunks, _ = _run(text)
    sections = derive_sections(chunks)
    # No headings -> exactly one prologue section covering all chunks.
    assert len(sections) == 1
    assert sections[0].heading_chunk_position is None


# --- generalised colon lead-in (claude-axx.2) -----------------------------


def test_colon_lead_in_absorbs_into_code_block() -> None:
    text = "Run this:\n\n```\nprint(1)\n```\n"
    _, _, _, chunks, _ = _run(text)
    assert len(chunks) == 1
    assert "Run this:" in chunks[0].text
    assert "print(1)" in chunks[0].text


def test_colon_lead_in_absorbs_into_blockquote() -> None:
    text = "Aristotle wrote:\n\n> a famous line.\n"
    _, _, _, chunks, _ = _run(text)
    assert len(chunks) == 1
    assert "Aristotle wrote:" in chunks[0].text
    assert "a famous line" in chunks[0].text


def test_colon_lead_in_absorbs_into_table() -> None:
    text = (
        "As shown below:\n"
        "\n"
        "| col | data |\n"
        "| --- | ---- |\n"
        "| a   | 1    |\n"
    )
    _, _, _, chunks, _ = _run(text)
    assert len(chunks) == 1
    assert "As shown below:" in chunks[0].text
    assert "| col" in chunks[0].text


def test_pipe_table_is_one_atomic_piece_never_sliced_across_chunks() -> None:
    """A pipe-table is one whole-block atomic piece (table_atomicity=
    "block", claude-l51) — even a long table stays in a single chunk
    rather than being cut row-by-row by reading-time packing."""
    text = (
        "Intro paragraph that sets up the table.\n\n"
        "| Finding | Status | Reasoning |\n"
        "| --- | --- | --- |\n"
        "| A | High | because of a fairly long explanation that runs on a bit. |\n"
        "| B | Medium | another lengthy explanation padding this row out further. |\n"
        "| C | Low | yet more cell text so the table comfortably exceeds a budget. |\n\n"
        "Outro paragraph after the table.\n"
    )
    pieces, _, _, chunks, _ = _run(text)
    assert sum(1 for p in pieces if p.kind == "table") == 1
    table_chunk = next(c for c in chunks if "| Finding |" in c.text)
    # First and last rows live in the SAME chunk — the table wasn't split.
    assert "| C | Low |" in table_chunk.text


def test_colon_lead_in_does_not_absorb_into_horizontal_rule() -> None:
    """HR is now skipped entirely from chunking (claude-jvs.3), so a
    colon-terminated paragraph followed by an HR cannot pull the HR
    into a list-with-colon-lead-in chunk — there's no HR chunk to
    pull. The colon paragraph stays prose; the HR is gone; the
    following prose is its own chunk."""
    text = "End of section:\n\n---\n\nNext section.\n"
    _, _, _, chunks, _ = _run(text)
    leads = [c.lead_token_type for c in chunks]
    assert "horizontal_rule" not in leads


# --- block-level images (claude-axx.6) ------------------------------------


def test_block_image_is_its_own_piece_and_chunk() -> None:
    text = "Some prose.\n\n![a figure](fig.png)\n\nMore prose.\n"
    pieces, _, _, chunks, _ = _run(text)
    assert [p.kind for p in pieces if p.kind == "image"] == ["image"]
    img_chunks = [c for c in chunks if c.lead_token_type == "image"]
    assert len(img_chunks) == 1
    assert "![a figure](fig.png)" in img_chunks[0].text
    # The image chunk stands alone — prose on either side stays separate.
    assert len(chunks) == 3
    assert "Some prose." in chunks[0].text
    assert "More prose." in chunks[2].text


def test_inline_image_in_prose_stays_a_prose_chunk() -> None:
    text = "Look at ![this](pic.png) carefully.\n"
    _, _, _, chunks, _ = _run(text)
    assert len(chunks) == 1
    assert chunks[0].lead_token_type == "paragraph"
    assert "![this](pic.png)" in chunks[0].text


def test_consecutive_block_images_are_not_bundled() -> None:
    text = "![one](a.png)\n\n![two](b.png)\n\n![three](c.png)\n"
    _, _, _, chunks, _ = _run(text)
    assert [c.lead_token_type for c in chunks] == ["image", "image", "image"]
    assert "a.png" in chunks[0].text
    assert "b.png" in chunks[1].text
    assert "c.png" in chunks[2].text


def test_colon_lead_in_absorbs_into_block_image() -> None:
    """Consistent with code/list/blockquote/table (claude-axx.2): a
    colon-terminated paragraph pulls the following block image into one
    chunk so the lead-in and the figure it introduces read together."""
    text = "See the diagram below:\n\n![architecture](arch.png)\n"
    _, _, plan, chunks, _ = _run(text)
    assert len(chunks) == 1
    assert "See the diagram below:" in chunks[0].text
    assert "![architecture](arch.png)" in chunks[0].text
    assert plan.planned_chunks[0].reason == "list_with_colon_lead_in"


def test_block_image_default_cost_is_fixed_six_seconds() -> None:
    text = "![fig](fig.png)\n"
    _, preprocessed, _, chunks, _ = _run(text)
    img = next(p for p in preprocessed if p.is_image)
    assert img.estimated_read_seconds == 6.0
    assert chunks[0].estimated_read_seconds == 6.0


def test_block_image_cost_can_derive_from_alt_text_words() -> None:
    rules = ChunkingRuleset(reading_rules=ReadingRules(image_seconds=None))
    # 4 alt words at 220 wpm = 4/220*60 ≈ 1.09s
    text = "![a four word caption](fig.png)\n"
    _, preprocessed, _, _, _ = _run(text, rules)
    img = next(p for p in preprocessed if p.is_image)
    assert img.estimated_read_seconds == pytest.approx(4 / 220 * 60)


def test_captionless_block_image_with_derived_cost_is_zero() -> None:
    rules = ChunkingRuleset(reading_rules=ReadingRules(image_seconds=None))
    text = "![](fig.png)\n"
    _, preprocessed, _, _, _ = _run(text, rules)
    img = next(p for p in preprocessed if p.is_image)
    assert img.estimated_read_seconds == 0.0


def test_block_image_renders_an_img_element() -> None:
    from parsem.web.view import render_chunk_html
    html = str(render_chunk_html("![alt words](pic.png)\n"))
    assert "<img" in html
    assert 'alt="alt words"' in html
    assert 'src="pic.png"' in html


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
    from parsem.domain.chunking import ChunkPlan, PlannedChunk
    bad_plan = ChunkPlan(planned_chunks=[
        PlannedChunk(
            ordinal=0,
            piece_ordinals=[pieces[0].ordinal],
            estimated_read_seconds=1.0,
            lead_piece_ordinal=pieces[0].ordinal,
            reason="prose_budget",
        ),
    ])
    with pytest.raises(AssertionError, match="missing revealable pieces"):
        validate_chunk_plan(bad_plan, preprocessed)
