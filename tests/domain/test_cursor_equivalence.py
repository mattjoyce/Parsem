"""Equivalence: cursor `current_reading_time` ≡ legacy on non-heading paths.

Spec: claude-axx.10 phase 1 acceptance, narrowed by claude-axx.10.2.
The cursor strategy intentionally diverges from legacy on heading
sequences (no-orphan-heading policy: `heading -> code` is one chunk,
`heading -> heading -> prose` is one chunk). The diverging cases live
in `test_cursor_heading_glue.py`. This file pins agreement on every
*other* path — pure prose, lists, blockquotes, tables, HR, colon
lead-in, atomicity overrides, edge cases.

Any divergence on the cases in this file is a regression.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from parsem.domain.atomic import AtomicRules, build_atomic_pieces
from parsem.domain.chunking import ChunkingRuleset, get_strategy
from parsem.domain.chunking.current_reading_time import CurrentReadingTimeStrategy
from parsem.domain.preprocessed import ReadingRules, preprocess_pieces
from parsem.parse.line_index import LineIndex
from parsem.parse.markdown_parse import parse

WELCOME = Path(__file__).resolve().parents[2] / "data" / "welcome.md"

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _plans_for(text: str, rules: ChunkingRuleset | None = None):
    """Run both planners on the same input and return their ChunkPlans
    paired up. Pure helper, no IO beyond the markdown parse."""
    rules = rules or ChunkingRuleset()
    line_index = LineIndex.from_text(text)
    blocks = parse(text)
    pieces = build_atomic_pieces(blocks, rules.atomic_rules, text, line_index)
    preprocessed = preprocess_pieces(pieces, rules.reading_rules)
    legacy_plan = CurrentReadingTimeStrategy().plan(preprocessed, rules)
    cursor_plan = get_strategy("current_reading_time").plan(preprocessed, rules)
    return legacy_plan, cursor_plan


def _assert_plans_equal(legacy, cursor, *, context: str) -> None:
    """Plan-level equality with a useful failure message."""
    assert len(legacy.planned_chunks) == len(cursor.planned_chunks), (
        f"[{context}] chunk count diverges: legacy={len(legacy.planned_chunks)}"
        f" cursor={len(cursor.planned_chunks)}"
    )
    for i, (lc, cc) in enumerate(
        zip(legacy.planned_chunks, cursor.planned_chunks, strict=True)
    ):
        assert lc == cc, (
            f"[{context}] chunk[{i}] diverges:\n  legacy: {lc}\n  cursor: {cc}"
        )


# Welcome corpus is no longer a clean equivalence fixture — its
# headings hit the no-orphan-heading policy. See
# `test_cursor_heading_glue.test_welcome_corpus_has_no_heading_only_chunk`.


# ---------------------------------------------------------------------------
# Fixtures matching the cases in test_atomic_chunking.py
# ---------------------------------------------------------------------------


def test_equivalence_paragraph_packs_within_budget() -> None:
    text = "First sentence. Second sentence. Third sentence.\n"
    _assert_plans_equal(*_plans_for(text), context="prose-fits-budget")


def test_equivalence_oversized_prose_splits_at_budget() -> None:
    sentence = "word " * 80 + "end. "
    text = (sentence * 3).rstrip() + "\n"
    _assert_plans_equal(*_plans_for(text), context="oversized-prose")


def test_equivalence_consecutive_paragraphs_pack_across_boundaries() -> None:
    text = "First.\n\nSecond.\n\nThird.\n"
    _assert_plans_equal(*_plans_for(text), context="paragraph-packing")


def test_equivalence_heading_attaches_forward() -> None:
    text = "## Title\n\nFollow-up sentence.\n"
    _assert_plans_equal(*_plans_for(text), context="heading-attach-forward")


# `heading -> code` is intentionally divergent — see
# `test_cursor_heading_glue.test_heading_glues_to_following_code`.


def test_equivalence_list_run() -> None:
    text = "- alpha\n- beta\n- gamma\n"
    _assert_plans_equal(*_plans_for(text), context="list-run")


def test_equivalence_colon_lead_in_absorbs_into_list() -> None:
    text = "Here are three items:\n\n- one\n- two\n- three\n"
    _assert_plans_equal(*_plans_for(text), context="colon-lead-in")


def test_equivalence_non_colon_paragraph_does_not_absorb() -> None:
    text = "Not a lead-in.\n\n- one\n- two\n"
    _assert_plans_equal(*_plans_for(text), context="no-absorb")


def test_equivalence_blockquote_is_its_own_chunk() -> None:
    text = "Some prose.\n\n> a quote.\n\nMore prose.\n"
    _assert_plans_equal(*_plans_for(text), context="blockquote")


def test_equivalence_table_is_its_own_chunk() -> None:
    text = (
        "Some prose.\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "More prose.\n"
    )
    _assert_plans_equal(*_plans_for(text), context="table")


def test_equivalence_horizontal_rule_separates_prose() -> None:
    text = "First paragraph.\n\n---\n\nSecond paragraph.\n"
    _assert_plans_equal(*_plans_for(text), context="hr-separates")


# `heading -> code -> prose` and `heading -> heading -> prose` are
# intentionally divergent — see `test_cursor_heading_glue` for the
# pinned cursor behaviour.


def test_equivalence_colon_then_code() -> None:
    text = "Like this:\n\n```\nx = 1\n```\n"
    _assert_plans_equal(*_plans_for(text), context="colon-then-code")


def test_equivalence_colon_then_blockquote() -> None:
    text = "He said:\n\n> something profound\n"
    _assert_plans_equal(*_plans_for(text), context="colon-then-blockquote")


def test_equivalence_with_paragraph_atomicity() -> None:
    rules = ChunkingRuleset(
        atomic_rules=AtomicRules(paragraph_atomicity="paragraph"),
    )
    text = "First sentence. Second sentence.\n\nNext paragraph here.\n"
    _assert_plans_equal(
        *_plans_for(text, rules), context="paragraph-atomicity"
    )


def test_equivalence_with_zero_heading_cost() -> None:
    rules = ChunkingRuleset(reading_rules=ReadingRules(heading_cost="zero"))
    text = "## Free heading\n\nSome body.\n"
    _assert_plans_equal(*_plans_for(text, rules), context="zero-heading-cost")


def test_equivalence_with_smaller_budget() -> None:
    rules = ChunkingRuleset(reading_rules=ReadingRules(budget_seconds=5.0))
    text = "First sentence. Second sentence. Third sentence. Fourth sentence.\n"
    _assert_plans_equal(*_plans_for(text, rules), context="smaller-budget")


def test_equivalence_empty_document() -> None:
    text = ""
    _assert_plans_equal(*_plans_for(text), context="empty")


def test_equivalence_single_sentence() -> None:
    text = "Just one.\n"
    _assert_plans_equal(*_plans_for(text), context="single-sentence")
