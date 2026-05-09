"""Deterministic preprocessing — adds metrics and flags to atomic pieces.

Spec: AtomicChunkingPhase1.md §PreprocessedPiece. Preprocessing never
splits, merges, or moves pieces; it only annotates them. Pure function:
same atomic pieces + same `ReadingRules` produce identical preprocessed
pieces. Phase 1 keeps preprocessing in-memory (not persisted).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from parsem.domain.atomic import AtomicPiece


@dataclass(frozen=True)
class ReadingRules:
    """Reading-time and budget settings used by preprocessing and the
    time-based planner.

    `heading_cost="normal"` matches today's chunker, which counts heading
    text against the budget at prose WPM. `"zero"` is available for
    strategies that want headings to be free.
    """

    prose_wpm: int = 220
    code_wpm: int = 110
    budget_seconds: float = 30.0
    heading_cost: Literal["normal", "zero"] = "normal"
    wpm_user_scaling: float = 1.0


_STRUCTURAL_ATOMIC_KINDS = frozenset({
    "code_block", "list_run", "list_item", "blockquote", "table", "horizontal_rule",
})


@dataclass(frozen=True)
class PreprocessedPiece:
    """An atomic piece plus deterministic flags and read-time metrics."""

    piece: AtomicPiece
    word_count: int
    estimated_read_seconds: float
    is_heading: bool
    is_code: bool
    is_table: bool
    is_blockquote: bool
    is_list: bool
    is_list_run: bool
    is_horizontal_rule: bool
    is_structural_atomic: bool
    is_colon_terminated: bool


def preprocess_pieces(
    pieces: list[AtomicPiece],
    rules: ReadingRules,
) -> list[PreprocessedPiece]:
    """Annotate each piece with read-time + flags using the given rules."""
    return [_preprocess(p, rules) for p in pieces]


def _preprocess(piece: AtomicPiece, rules: ReadingRules) -> PreprocessedPiece:
    word_count = len(piece.text_snapshot.split())
    is_code = piece.kind == "code_block"
    is_heading = piece.kind == "heading"
    is_table = piece.kind == "table"
    is_blockquote = piece.kind == "blockquote"
    is_list_run = piece.kind == "list_run"
    is_list_item = piece.kind == "list_item"
    is_list = is_list_run or is_list_item
    is_horizontal_rule = piece.kind == "horizontal_rule"
    is_structural_atomic = piece.kind in _STRUCTURAL_ATOMIC_KINDS
    # Don't ask "does '---' end with ':'?" — HR is purely visual.
    is_colon_terminated = (
        not is_horizontal_rule and piece.text_snapshot.rstrip().endswith(":")
    )

    if is_heading and rules.heading_cost == "zero":
        read_seconds = 0.0
    elif is_horizontal_rule:
        # HR is a visual break with no prose to read. Zero cost so it
        # never consumes the prose budget.
        read_seconds = 0.0
    else:
        wpm = rules.code_wpm if is_code else rules.prose_wpm
        effective_wpm = wpm * rules.wpm_user_scaling
        read_seconds = 0.0 if effective_wpm <= 0 else word_count / effective_wpm * 60

    return PreprocessedPiece(
        piece=piece,
        word_count=word_count,
        estimated_read_seconds=read_seconds,
        is_heading=is_heading,
        is_code=is_code,
        is_table=is_table,
        is_blockquote=is_blockquote,
        is_list=is_list,
        is_list_run=is_list_run,
        is_horizontal_rule=is_horizontal_rule,
        is_structural_atomic=is_structural_atomic,
        is_colon_terminated=is_colon_terminated,
    )
