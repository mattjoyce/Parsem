"""Tests for parsem.domain.bucket. Spec: parsem-spec.md §12."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from parsem.domain.bucket import BucketConfig, tokens_now
from tests.conftest import T0


def burst(start: datetime, n: int, step_seconds: float = 0.1) -> list[datetime]:
    """Build a list of n reveal timestamps spaced step_seconds apart."""
    return [start + timedelta(seconds=i * step_seconds) for i in range(n)]


@pytest.fixture
def wide_config() -> BucketConfig:
    """A config where capacity > fresh_session_idle_multiplier, so the
    fresh-session credit is observably distinct from regen-clamping."""
    return BucketConfig(capacity=10, regen_seconds=12, fresh_session_idle_multiplier=5)


def test_empty_reveal_list_with_start_full_returns_capacity() -> None:
    config = BucketConfig()
    assert tokens_now([], config, T0) == config.capacity


def test_empty_reveal_list_without_start_full_returns_zero() -> None:
    config = BucketConfig(start_full=False)
    assert tokens_now([], config, T0) == 0


def test_one_reveal_now_equals_reveal_time_returns_capacity_minus_one() -> None:
    config = BucketConfig()
    assert tokens_now([T0], config, T0) == config.capacity - 1


def test_one_reveal_then_full_regen_interval_returns_capacity() -> None:
    config = BucketConfig()
    now = T0 + timedelta(seconds=config.regen_seconds)
    assert tokens_now([T0], config, now) == config.capacity


def test_one_reveal_then_just_under_regen_interval_returns_capacity_minus_one() -> None:
    config = BucketConfig()
    now = T0 + timedelta(seconds=config.regen_seconds - 1)
    assert tokens_now([T0], config, now) == config.capacity - 1


def test_three_rapid_reveals_at_capacity_three_drains_to_zero() -> None:
    config = BucketConfig()
    reveals = burst(T0, 3, step_seconds=1)
    assert tokens_now(reveals, config, reveals[-1]) == 0


def test_three_rapid_reveals_then_one_regen_interval_returns_one() -> None:
    config = BucketConfig()
    reveals = burst(T0, 3, step_seconds=1)
    now = reveals[-1] + timedelta(seconds=config.regen_seconds)
    assert tokens_now(reveals, config, now) == 1


def test_regen_caps_at_capacity_even_after_long_idle() -> None:
    config = BucketConfig()
    reveals = burst(T0, 3, step_seconds=1)
    # 3 regen intervals would yield 3 tokens; cap stops it from overflowing.
    # Use just under fresh-session threshold to isolate cap-vs-fresh-session.
    now = reveals[-1] + timedelta(seconds=3 * config.regen_seconds)
    assert tokens_now(reveals, config, now) == config.capacity


def test_fresh_session_credit_restores_full_bucket_past_threshold(
    wide_config: BucketConfig,
) -> None:
    # After 10 drains, regen alone over 60s would yield only 5 tokens.
    reveals = burst(T0, 10)
    threshold = wide_config.fresh_session_idle_multiplier * wide_config.regen_seconds
    now = reveals[-1] + timedelta(seconds=threshold + 1)
    assert tokens_now(reveals, wide_config, now) == wide_config.capacity


def test_idle_at_threshold_exactly_does_not_trigger_fresh_session(
    wide_config: BucketConfig,
) -> None:
    # Spec §12.6: "more than" — strictly greater, not ≥.
    reveals = burst(T0, 10)
    threshold = wide_config.fresh_session_idle_multiplier * wide_config.regen_seconds
    now = reveals[-1] + timedelta(seconds=threshold)
    # Regen-only: 60s / 12s = 5 regens, applied to a drained bucket.
    assert tokens_now(reveals, wide_config, now) == 5


def test_fresh_session_reset_between_two_bursts_in_one_event_list(
    wide_config: BucketConfig,
) -> None:
    threshold = wide_config.fresh_session_idle_multiplier * wide_config.regen_seconds
    burst_one = burst(T0, 10)
    burst_two_start = burst_one[-1] + timedelta(seconds=threshold + 1)
    burst_two = burst(burst_two_start, 3)
    reveals = burst_one + burst_two
    # After the second burst, with now=last reveal: 10 - 3 = 7 tokens.
    assert tokens_now(reveals, wide_config, reveals[-1]) == 7


def test_called_twice_with_same_inputs_returns_same_output() -> None:
    config = BucketConfig()
    reveals = [T0, T0 + timedelta(seconds=5), T0 + timedelta(seconds=10)]
    now = reveals[-1] + timedelta(seconds=7)
    first = tokens_now(reveals, config, now)
    second = tokens_now(reveals, config, now)
    assert first == second


def test_does_not_mutate_input_reveal_list() -> None:
    config = BucketConfig()
    reveals = [T0, T0 + timedelta(seconds=5), T0 + timedelta(seconds=10)]
    snapshot = list(reveals)
    tokens_now(reveals, config, reveals[-1])
    assert reveals == snapshot
