"""Deterministic preprocessing — adds metrics and flags to atomic pieces.

Spec: AtomicChunkingPhase1.md §PreprocessedPiece. Preprocessing never
splits, merges, or moves pieces; it only annotates them. Pure function:
same atomic pieces + same `ReadingRules` produce identical preprocessed
pieces. Phase 1 keeps preprocessing in-memory (not persisted).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from parsem.domain.atomic import AtomicPiece

_EMPTY_ANNOTATIONS: Mapping[str, object] = MappingProxyType({})


@dataclass(frozen=True)
class ReadingRules:
    """Reading-time and budget settings used by preprocessing and the
    time-based planner.

    `heading_cost="normal"` matches today's chunker, which counts heading
    text against the budget at prose WPM. `"zero"` is available for
    strategies that want headings to be free.

    `image_seconds` is the read cost of a block-level image (claude-axx.6):
    a float gives every image a fixed cost (default 6s — long enough to
    register, short enough not to dominate the budget); `None` derives the
    cost from the alt text's word count at prose WPM (so a richly-captioned
    image costs more than a bare one, and `![](url)` costs ~0s).
    """

    prose_wpm: int = 220
    code_wpm: int = 110
    budget_seconds: float = 30.0
    heading_cost: Literal["normal", "zero"] = "normal"
    wpm_user_scaling: float = 1.0
    image_seconds: float | None = 6.0


_STRUCTURAL_ATOMIC_KINDS = frozenset({
    "code_block", "list_run", "list_item", "blockquote", "table",
    "horizontal_rule", "image",
})

# Captures the alt text from `![alt](url)`. Used only when
# ReadingRules.image_seconds is None (derive cost from alt words).
_IMAGE_ALT_RE = re.compile(r"!\[([^\]]*)\]")


@dataclass(frozen=True)
class PreprocessedPiece:
    """An atomic piece plus deterministic flags and read-time metrics.

    `annotations` is a read-only mapping populated at construction time
    by registered annotators (claude-axx.10). It carries per-piece
    values that cursor rules consult — e.g. a `transition_edge: float`
    score from a lexical annotator. Construction-time-only by
    convention; the mapping is `MappingProxyType` so attempts to mutate
    after the fact raise `TypeError` loudly."""

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
    is_image: bool
    is_structural_atomic: bool
    is_colon_terminated: bool
    annotations: Mapping[str, object] = field(default_factory=lambda: _EMPTY_ANNOTATIONS)


def preprocess_pieces(
    pieces: list[AtomicPiece],
    rules: ReadingRules,
    *,
    annotator_names: tuple[str, ...] = (),
) -> list[PreprocessedPiece]:
    """Annotate each piece with read-time + flags using the given rules.

    `annotator_names` (claude-axx.10) names registered annotators whose
    per-piece outputs are merged into each PreprocessedPiece's
    `annotations` map. Empty tuple = today's behaviour. Annotator
    lookup raises `UnknownAnnotatorError` if a name isn't registered —
    fail loudly at construction, not mid-chunking."""
    if annotator_names:
        from parsem.domain.chunking.annotators import get_annotator

        per_piece: dict[int, dict[str, object]] = {p.ordinal: {} for p in pieces}
        for name in annotator_names:
            annotator = get_annotator(name)
            output = annotator.annotate(pieces)
            for ordinal, values in output.items():
                per_piece.setdefault(ordinal, {}).update(values)
        return [
            _preprocess(p, rules, annotations=MappingProxyType(per_piece[p.ordinal]))
            for p in pieces
        ]
    return [_preprocess(p, rules) for p in pieces]


def _preprocess(
    piece: AtomicPiece,
    rules: ReadingRules,
    *,
    annotations: Mapping[str, object] = _EMPTY_ANNOTATIONS,
) -> PreprocessedPiece:
    word_count = len(piece.text_snapshot.split())
    is_code = piece.kind == "code_block"
    is_heading = piece.kind == "heading"
    is_table = piece.kind == "table"
    is_blockquote = piece.kind == "blockquote"
    is_list_run = piece.kind == "list_run"
    is_list_item = piece.kind == "list_item"
    is_list = is_list_run or is_list_item
    is_horizontal_rule = piece.kind == "horizontal_rule"
    is_image = piece.kind == "image"
    is_structural_atomic = piece.kind in _STRUCTURAL_ATOMIC_KINDS
    # Don't ask "does '---' end with ':'?" — HR and image are purely
    # visual; neither participates in colon-lead-in detection on its
    # own side (a colon-terminated *previous* paragraph can still
    # absorb an image — that's the previous chunk's flag, not this one).
    is_colon_terminated = (
        not is_horizontal_rule
        and not is_image
        and piece.text_snapshot.rstrip().endswith(":")
    )

    if is_heading and rules.heading_cost == "zero":
        read_seconds = 0.0
    elif is_horizontal_rule:
        # HR is a visual break with no prose to read. Zero cost so it
        # never consumes the prose budget.
        read_seconds = 0.0
    elif is_image:
        read_seconds = _image_read_seconds(piece.text_snapshot, rules)
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
        is_image=is_image,
        is_structural_atomic=is_structural_atomic,
        is_colon_terminated=is_colon_terminated,
        annotations=annotations,
    )


def _image_read_seconds(text_snapshot: str, rules: ReadingRules) -> float:
    """Read cost for a block-level image. Fixed when
    `rules.image_seconds` is a float; otherwise the alt text's words at
    prose WPM (a captionless `![](url)` costs ~0s, a described one more)."""
    if rules.image_seconds is not None:
        return rules.image_seconds
    match = _IMAGE_ALT_RE.search(text_snapshot)
    alt_words = len(match.group(1).split()) if match else 0
    effective_wpm = rules.prose_wpm * rules.wpm_user_scaling
    return 0.0 if effective_wpm <= 0 else alt_words / effective_wpm * 60
