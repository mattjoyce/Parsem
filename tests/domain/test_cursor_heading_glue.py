"""No-orphan-heading policy (claude-axx.10.2).

Pin the cursor strategy's tuned behaviour: a chunk never consists of
only heading pieces (except as a graceful degrade when the document
ends on a heading and there is no following content to attach to).

These cases all intentionally diverge from the legacy strategy. The
legacy chunks are preserved at `current_reading_time_legacy` for any
side-by-side comparison the user wants to do at the reader.
"""

from __future__ import annotations

from pathlib import Path

from parsem.domain.atomic import build_atomic_pieces
from parsem.domain.chunking import ChunkingRuleset, get_strategy
from parsem.domain.preprocessed import preprocess_pieces
from parsem.parse.line_index import LineIndex
from parsem.parse.markdown_parse import parse

WELCOME = Path(__file__).resolve().parents[2] / "data" / "welcome.md"


def _chunks_for(text: str, rules: ChunkingRuleset | None = None):
    rules = rules or ChunkingRuleset()
    line_index = LineIndex.from_text(text)
    pieces = build_atomic_pieces(parse(text), rules.atomic_rules, text, line_index)
    preprocessed = preprocess_pieces(pieces, rules.reading_rules)
    plan = get_strategy("current_reading_time").plan(preprocessed, rules)
    return preprocessed, plan.planned_chunks


def _is_heading_only(chunk, pieces) -> bool:
    return all(pieces[ord_].is_heading for ord_ in chunk.piece_ordinals)


# ---------------------------------------------------------------------------
# Core invariant: no chunk is heading-only (except trailing degrade)
# ---------------------------------------------------------------------------


def test_welcome_corpus_has_no_heading_only_chunk() -> None:
    text = WELCOME.read_text(encoding="utf-8")
    pieces, chunks = _chunks_for(text)
    heading_only = [
        (i, c) for i, c in enumerate(chunks) if _is_heading_only(c, pieces)
    ]
    # If any heading-only chunks exist, they must be the trailing chunk
    # (graceful degrade — doc literally ends on a heading).
    for i, c in heading_only:
        assert i == len(chunks) - 1, (
            f"heading-only chunk at position {i} (not trailing); ordinals={c.piece_ordinals}"
        )


# ---------------------------------------------------------------------------
# Specific glue cases
# ---------------------------------------------------------------------------


def test_heading_glues_to_following_code() -> None:
    """`## Title\\n\\n```code```` -> one chunk containing both."""
    text = "## Code\n\n```\nprint('hi')\n```\n"
    pieces, chunks = _chunks_for(text)
    assert len(chunks) == 1, f"expected 1 chunk, got {len(chunks)}"
    chunk = chunks[0]
    assert pieces[chunk.piece_ordinals[0]].is_heading
    # Code block piece kind is `code_block`.
    assert pieces[chunk.piece_ordinals[-1]].piece.kind == "code_block"


def test_consecutive_headings_glue_with_following_prose() -> None:
    """`# H1` + `## H2` + prose -> one chunk containing all three."""
    text = "# H1\n\n## H2\n\nBody sentence.\n"
    pieces, chunks = _chunks_for(text)
    assert len(chunks) == 1
    kinds = [pieces[o].piece.kind for o in chunks[0].piece_ordinals]
    assert kinds.count("heading") == 2
    assert "sentence" in kinds


def test_heading_then_code_then_prose() -> None:
    """`## Heading` + code + prose -> 2 chunks: [heading, code], [prose].
    The trailing prose is a separate chunk because the code already
    satisfied the heading's attach-forward need."""
    text = (
        "## Section\n\n"
        "```\nx = 1\n```\n\n"
        "Trailing prose sentence.\n"
    )
    pieces, chunks = _chunks_for(text)
    assert len(chunks) == 2
    first_kinds = [pieces[o].piece.kind for o in chunks[0].piece_ordinals]
    assert "heading" in first_kinds
    assert "code_block" in first_kinds


def test_heading_glues_across_horizontal_rule() -> None:
    """`## Heading\\n---\\nprose` -> one chunk: [heading, prose] (HR is
    skipped per legacy; with no-orphan-heading the heading attaches
    forward across the dropped HR)."""
    text = "## Section\n\n---\n\nFollowing prose.\n"
    pieces, chunks = _chunks_for(text)
    assert len(chunks) == 1
    kinds = [pieces[o].piece.kind for o in chunks[0].piece_ordinals]
    assert "heading" in kinds
    assert "sentence" in kinds


def test_heading_glues_to_blockquote() -> None:
    """`## Heading` + blockquote -> one chunk."""
    text = "## Quote\n\n> Wisdom inside.\n"
    pieces, chunks = _chunks_for(text)
    assert len(chunks) == 1
    kinds = [pieces[o].piece.kind for o in chunks[0].piece_ordinals]
    assert "heading" in kinds
    assert "blockquote" in kinds


def test_heading_glues_to_list_run() -> None:
    """`## Heading` + list -> one chunk."""
    text = "## Items\n\n- one\n- two\n"
    pieces, chunks = _chunks_for(text)
    assert len(chunks) == 1
    kinds = [pieces[o].piece.kind for o in chunks[0].piece_ordinals]
    assert "heading" in kinds
    assert "list_run" in kinds


# ---------------------------------------------------------------------------
# Edge cases — graceful degrade
# ---------------------------------------------------------------------------


def test_heading_only_document_emits_alone_as_degrade() -> None:
    """Doc consisting of nothing but a heading: emit as a single chunk
    (can't satisfy the no-orphan constraint without losing content)."""
    text = "# Just a heading\n"
    pieces, chunks = _chunks_for(text)
    assert len(chunks) == 1
    assert pieces[chunks[0].piece_ordinals[0]].is_heading


def test_trailing_heading_at_end_of_doc_emits_alone_as_degrade() -> None:
    """`prose\\n\\n## Trailing`  -> 2 chunks; the trailing heading
    has no following content so it degrades to a solo chunk."""
    text = "Some prose sentence.\n\n## Trailing heading\n"
    pieces, chunks = _chunks_for(text)
    assert len(chunks) == 2
    # Last chunk is the heading-only degrade.
    last = chunks[-1]
    assert all(pieces[o].is_heading for o in last.piece_ordinals)


def test_multiple_trailing_headings_emit_together_as_degrade() -> None:
    """`prose\\n## H1\\n## H2` -> 2 chunks; both trailing headings glue
    together in the degrade chunk."""
    text = "Some prose.\n\n## H1\n\n## H2\n"
    pieces, chunks = _chunks_for(text)
    assert len(chunks) == 2
    last_kinds = [pieces[o].piece.kind for o in chunks[-1].piece_ordinals]
    assert last_kinds == ["heading", "heading"]


# ---------------------------------------------------------------------------
# Read-seconds preservation
# ---------------------------------------------------------------------------


def test_glued_chunk_carries_summed_seconds() -> None:
    """Read time of glued chunk = sum of constituent pieces' seconds."""
    text = "## Heading\n\nBody sentence here.\n"
    pieces, chunks = _chunks_for(text)
    assert len(chunks) == 1
    total = sum(pieces[o].estimated_read_seconds for o in chunks[0].piece_ordinals)
    assert abs(chunks[0].estimated_read_seconds - total) < 1e-9
