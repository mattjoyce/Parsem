"""Strategy registry behaviour (claude-axx.9).

The registry is the seam that lets a second strategy (e.g. claude-1u3
structural) drop in without app churn. These tests pin the contract:
named lookup, unknown-name fallback, and the boot-time
`set_default_strategy()` knob driven by config.
"""

from __future__ import annotations

import pytest

from parsem.domain.chunking import (
    DEFAULT_STRATEGY_NAME,
    STRATEGIES,
    ChunkingStrategy,
    get_strategy,
    is_known_strategy,
    set_default_strategy,
)
from parsem.domain.chunking.current_reading_time import CurrentReadingTimeStrategy


@pytest.fixture(autouse=True)
def _restore_default() -> object:
    """Each test gets a fresh module default so set_default_strategy()
    mutations don't leak across cases."""
    import parsem.domain.chunking as chunking

    original = chunking.DEFAULT_STRATEGY_NAME
    yield
    chunking.DEFAULT_STRATEGY_NAME = original


def test_current_reading_time_is_registered() -> None:
    """`current_reading_time` resolves to the cursor-based composition
    (claude-axx.10); the legacy imperative class is still available
    under `current_reading_time_legacy` for equivalence comparison."""
    assert "current_reading_time" in STRATEGIES
    assert STRATEGIES["current_reading_time"].name == "current_reading_time"


def test_current_reading_time_legacy_is_registered() -> None:
    assert "current_reading_time_legacy" in STRATEGIES
    assert isinstance(
        STRATEGIES["current_reading_time_legacy"], CurrentReadingTimeStrategy
    )


def test_default_strategy_name_is_current_reading_time() -> None:
    assert DEFAULT_STRATEGY_NAME == "current_reading_time"


def test_get_strategy_returns_default_for_none() -> None:
    assert get_strategy(None).name == "current_reading_time"


def test_get_strategy_returns_named_entry() -> None:
    assert get_strategy("current_reading_time").name == "current_reading_time"


def test_get_strategy_falls_back_to_default_for_unknown() -> None:
    fallback = get_strategy("nonsense_strategy_zzz")
    assert fallback.name == DEFAULT_STRATEGY_NAME


def test_is_known_strategy() -> None:
    assert is_known_strategy("current_reading_time")
    assert not is_known_strategy("nonsense_strategy_zzz")


def test_set_default_strategy_accepts_known_name() -> None:
    import parsem.domain.chunking as chunking

    in_effect = set_default_strategy("current_reading_time")
    assert in_effect == "current_reading_time"
    assert chunking.DEFAULT_STRATEGY_NAME == "current_reading_time"


def test_set_default_strategy_ignores_unknown_and_reports_current() -> None:
    """Unknown name must not crash boot — the function leaves the
    default alone and reports what's actually in effect so the caller
    can log the divergence."""
    import parsem.domain.chunking as chunking

    chunking.DEFAULT_STRATEGY_NAME = "current_reading_time"
    in_effect = set_default_strategy("nonsense_strategy_zzz")
    assert in_effect == "current_reading_time"
    assert chunking.DEFAULT_STRATEGY_NAME == "current_reading_time"


def test_get_strategy_with_no_args_uses_module_default() -> None:
    """A second strategy registered into STRATEGIES becomes selectable
    by mutating DEFAULT_STRATEGY_NAME — proves the seam without
    requiring a real second strategy to ship."""

    class _DummyStrategy:
        name = "dummy_for_test"
        version = "0.0.0"

        def plan(self, preprocessed, rules):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    STRATEGIES["dummy_for_test"] = _DummyStrategy()  # type: ignore[assignment]
    try:
        in_effect = set_default_strategy("dummy_for_test")
        assert in_effect == "dummy_for_test"
        assert get_strategy().name == "dummy_for_test"
        assert get_strategy(None).name == "dummy_for_test"
    finally:
        del STRATEGIES["dummy_for_test"]


def test_missing_annotation_raises_at_strategy_compose_time() -> None:
    """A cursor rule whose `requires` names an annotation no configured
    annotator produces must fail loudly at `compose_strategy()` — not
    silently no-op partway through chunking. Names both the rule and
    the missing key so the error message points at the fix."""
    import pytest

    from parsem.domain.chunking.annotators import MissingAnnotationError
    from parsem.domain.chunking.cursor import (
        PASS,
        CursorContext,
        Rule,
        RuleDecision,
        compose_strategy,
    )

    class _RuleNeedingTopic:
        name = "needs_topic"
        priority = 50
        requires = ("topic_id",)

        def consult(self, piece, bucket, chunks_so_far, ctx):
            return PASS

    # Sanity: the protocol-shaped object is a Rule by structural checks.
    _ = Rule, RuleDecision, CursorContext

    with pytest.raises(MissingAnnotationError) as exc_info:
        compose_strategy(
            name="broken",
            version="0.0.0",
            rules=(_RuleNeedingTopic(),),
            annotator_names=(),
        )
    assert exc_info.value.rule_name == "needs_topic"
    assert exc_info.value.missing_key == "topic_id"
    assert "needs_topic" in str(exc_info.value)
    assert "topic_id" in str(exc_info.value)


def test_registered_strategies_satisfy_protocol() -> None:
    """Each registered entry must look like a ChunkingStrategy — has
    name, version, plan(). Cheap structural check; the Protocol itself
    isn't runtime-checkable without @runtime_checkable."""
    for name, strategy in STRATEGIES.items():
        assert isinstance(strategy.name, str) and strategy.name == name
        assert isinstance(strategy.version, str)
        assert callable(strategy.plan)


# Silences unused-import warning — ChunkingStrategy is part of the
# public API we're pinning, even if no test references it directly.
_ = ChunkingStrategy
