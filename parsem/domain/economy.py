"""Reading-economy transport helpers. Spec: parsem-spec.md §12.4, §13.1.

Pure functions used by the web layer to keep route handlers transport-only.
No IO, no clock reads, no global state.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from parsem.domain.bucket import BucketConfig, tokens_now

RevealReason = Literal["advanced_paid", "advanced_free", "bucket_empty", "end_of_document"]


@dataclass(frozen=True)
class RevealOutcome:
    """Boundary value between economy logic and route transport."""

    advanced: bool
    new_position: int
    paid: bool
    tokens_after: int
    reason: RevealReason


def try_reveal(
    *,
    current_position: int,
    high_water_position: int,
    chunks_total: int,
    paid_reveal_times: Sequence[datetime],
    bucket_config: BucketConfig,
    now: datetime,
) -> RevealOutcome:
    """Decide whether the next reveal advances, and at what cost.

    Re-revealing a chunk at position ≤ high_water_position is free
    (spec §12.4). Spending a token only occurs when advancing into new
    territory (current_position+1 > high_water_position). Computes the
    outcome from the input event-times list — does not mutate, does not
    append. The caller is responsible for appending the reveal event
    after a paid advance so that the freshly-appended reveal does NOT
    self-count in the bucket regen math used here.
    """
    target = current_position + 1
    tokens = tokens_now(paid_reveal_times, bucket_config, now)
    if target >= chunks_total:
        return RevealOutcome(
            advanced=False,
            new_position=current_position,
            paid=False,
            tokens_after=tokens,
            reason="end_of_document",
        )
    if target <= high_water_position:
        return RevealOutcome(
            advanced=True,
            new_position=target,
            paid=False,
            tokens_after=tokens,
            reason="advanced_free",
        )
    if tokens == 0:
        return RevealOutcome(
            advanced=False,
            new_position=current_position,
            paid=False,
            tokens_after=0,
            reason="bucket_empty",
        )
    return RevealOutcome(
        advanced=True,
        new_position=target,
        paid=True,
        tokens_after=tokens - 1,
        reason="advanced_paid",
    )


def cycle_pin(current: int | None) -> int | None:
    """Cycle the pin colour: none → 1 → 2 → 3 → 4 → 5 → none."""
    if current is None:
        return 1
    if current == 5:
        return None
    return current + 1
