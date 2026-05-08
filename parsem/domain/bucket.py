"""Pure-function token-bucket math for Parsem's reading economy.

Spec: parsem-spec.md §12. The bucket paces a reader's advancement through a
document. Tokens are spent on Reveal (advancing into new territory). Re-reveals
of paid territory are free and must be filtered out by the caller before the
timestamps reach this module.

This module is pure: time is injected, configuration is passed in, and no
external state is touched.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BucketConfig:
    """Reading-economy bucket configuration. Defaults match spec §20.

    ``capacity`` is fixed at 5 by product decision (spec §12.1) — five is the
    upper edge of glanceable subitization, and a higher cap would weaken the
    deliberate-friction thesis. Tests that need to exercise small-capacity
    edge cases pin ``BucketConfig(capacity=N)`` explicitly; production
    construction takes the default.
    """

    capacity: int = 5
    regen_seconds: int = 12
    start_full: bool = True
    fresh_session_idle_multiplier: int = 5


def tokens_now(
    reveal_times: Sequence[datetime],
    config: BucketConfig,
    now: datetime,
) -> int:
    """Compute the current bucket token count.

    Args:
        reveal_times: Chronologically ordered timestamps of *paid* reveals.
            The caller is responsible for excluding free re-reveals
            (re-reveals of chunks at or below the current high-water position).
        config: Bucket configuration.
        now: The current time. Must be timezone-aware to match reveal_times.

    Returns:
        The current token count, clamped to ``[0, config.capacity]``.
    """
    if not reveal_times:
        return config.capacity if config.start_full else 0

    tokens = config.capacity if config.start_full else 0
    prev_t = reveal_times[0]
    tokens = _spend(tokens)

    for t in reveal_times[1:]:
        tokens = _advance(tokens, (t - prev_t).total_seconds(), config)
        tokens = _spend(tokens)
        prev_t = t

    return _advance(tokens, (now - prev_t).total_seconds(), config)


def _advance(tokens: int, elapsed_seconds: float, config: BucketConfig) -> int:
    """Apply elapsed time to the bucket: fresh-session credit or regen + cap."""
    if elapsed_seconds > config.fresh_session_idle_multiplier * config.regen_seconds:
        return config.capacity
    regen = int(elapsed_seconds // config.regen_seconds)
    return min(config.capacity, tokens + regen)


def _spend(tokens: int) -> int:
    return max(0, tokens - 1)
