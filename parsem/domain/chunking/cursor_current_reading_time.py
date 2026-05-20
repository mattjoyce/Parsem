"""Cursor-based `current_reading_time` (claude-axx.10).

Composes the shipped rule values over the cursor engine. Equivalent
with the legacy imperative `CurrentReadingTimeStrategy` on all
non-heading paths (see `tests/domain/test_cursor_equivalence.py`);
intentionally diverges on heading sequences per the no-orphan-heading
policy (claude-axx.10.2, see `tests/domain/test_cursor_heading_glue.py`).

Versioning:
  - `2.0.0` — engine change (cursor vs imperative); same outputs.
  - `2.1.0` — no-orphan-heading policy; heading-only chunks are
              deferred and glued to the next chunk's lead pieces.
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
        version="2.1.0",
        rules=(
            HorizontalRuleSkip(),
            ColonLeadInAbsorb(),
            StructuralAtomicEmitAlone(),
            HeadingAttachForward(),
            BudgetSplit(),
        ),
        annotator_names=(),
    )
