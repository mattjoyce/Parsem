"""Cursor-based reproduction of `current_reading_time` (claude-axx.10).

Composes the shipped rule values over the cursor engine to produce a
strategy that is byte-identical with the legacy imperative
`CurrentReadingTimeStrategy` on every existing fixture (see
`tests/domain/test_cursor_equivalence.py`).

The version bump from `1.0.0` (legacy) to `2.0.0` (cursor) signals an
engine change: same outputs, different mechanism. A persisted
`ChunkingRun` keyed on the version invalidates cleanly when the cursor
strategy ships.
"""

from __future__ import annotations

from .cursor import ComposedStrategy, compose_strategy
from .rules import (
    BudgetSplit,
    ColonLeadInAbsorb,
    HeadingAttachForward,
    HorizontalRuleSkip,
    StructuralAtomicEmitAlone,
)


def build_cursor_current_reading_time() -> ComposedStrategy:
    """Build the cursor-based current_reading_time strategy. Called
    once at registry init; the result is a frozen, hashable value
    safe to stash in `STRATEGIES`."""
    return compose_strategy(
        name="current_reading_time",
        version="2.0.0",
        rules=(
            HorizontalRuleSkip(),
            ColonLeadInAbsorb(),
            StructuralAtomicEmitAlone(),
            HeadingAttachForward(),
            BudgetSplit(),
        ),
        annotator_names=(),
    )
