"""Tests for parsem.domain.economy. Spec: parsem-spec.md §12.4, §13.1."""

from __future__ import annotations

from datetime import timedelta

from parsem.domain.bucket import BucketConfig
from parsem.domain.economy import cycle_pin, try_reveal
from tests.conftest import T0


def test_cycle_pin_from_none_returns_one() -> None:
    assert cycle_pin(None) == 1


def test_cycle_pin_advances_through_colours() -> None:
    assert cycle_pin(1) == 2
    assert cycle_pin(2) == 3
    assert cycle_pin(3) == 4
    assert cycle_pin(4) == 5


def test_cycle_pin_wraps_from_five_to_none() -> None:
    assert cycle_pin(5) is None


def test_try_reveal_advances_and_spends_token_on_new_territory() -> None:
    outcome = try_reveal(
        current_position=0,
        high_water_position=0,
        chunks_total=10,
        paid_reveal_times=[],
        bucket_config=BucketConfig(),
        now=T0,
    )
    assert outcome.advanced is True
    assert outcome.new_position == 1
    assert outcome.paid is True
    assert outcome.tokens_after == BucketConfig().capacity - 1


def test_try_reveal_into_paid_territory_is_free() -> None:
    # high_water already at 5; we're at 2 → moving to 3 is paid territory, free.
    outcome = try_reveal(
        current_position=2,
        high_water_position=5,
        chunks_total=10,
        paid_reveal_times=[T0, T0 + timedelta(seconds=1), T0 + timedelta(seconds=2)],
        bucket_config=BucketConfig(),
        now=T0 + timedelta(seconds=2),
    )
    assert outcome.advanced is True
    assert outcome.new_position == 3
    assert outcome.paid is False


def test_try_reveal_does_not_advance_when_bucket_empty_and_new_territory() -> None:
    # Pinned to capacity=3 so 3 reveals exhaust the bucket. Production
    # default is 5 (spec §12.1); this test verifies the empty-bucket branch
    # of try_reveal regardless of capacity.
    times = [T0, T0 + timedelta(seconds=1), T0 + timedelta(seconds=2)]
    outcome = try_reveal(
        current_position=2,
        high_water_position=2,
        chunks_total=10,
        paid_reveal_times=times,
        bucket_config=BucketConfig(capacity=3),
        now=T0 + timedelta(seconds=2),
    )
    assert outcome.advanced is False
    assert outcome.new_position == 2
    assert outcome.paid is False
    assert outcome.tokens_after == 0


def test_try_reveal_at_end_of_document_does_not_advance() -> None:
    outcome = try_reveal(
        current_position=9,
        high_water_position=9,
        chunks_total=10,
        paid_reveal_times=[],
        bucket_config=BucketConfig(),
        now=T0,
    )
    assert outcome.advanced is False
    assert outcome.new_position == 9
