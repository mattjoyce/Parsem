"""Tests for parsem.domain.reanchor. Spec: bead claude-z99 + brief
"Re-Anchoring Across Revisions"."""

from __future__ import annotations

from parsem.domain.reanchor import (
    best_chunk_by_jaccard,
    chunk_containing_offset_range,
    jaccard,
    reanchor_chunk_positions,
)

# ─── jaccard ──────────────────────────────────────────────────────────


def test_jaccard_identical_sets_is_one() -> None:
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets_is_zero() -> None:
    assert jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial_overlap() -> None:
    # |{a,b} & {b,c}| = 1, |{a,b} | {b,c}| = 3 -> 1/3
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3


def test_jaccard_subset() -> None:
    # |{a} & {a,b,c}| = 1, |{a} | {a,b,c}| = 3 -> 1/3
    assert jaccard({"a"}, {"a", "b", "c"}) == 1 / 3


def test_jaccard_two_empty_sets_is_zero_by_convention() -> None:
    """Mathematicians leave 0/0 undefined; we return 0.0 because the
    consumer reads 0.0 as 'no signal' / 'no anchor available'."""
    assert jaccard(set(), set()) == 0.0


def test_jaccard_one_empty_set_is_zero() -> None:
    assert jaccard({"a"}, set()) == 0.0
    assert jaccard(set(), {"a"}) == 0.0


def test_jaccard_handles_frozenset() -> None:
    assert jaccard(frozenset({1, 2}), frozenset({2, 3})) == 1 / 3


# ─── best_chunk_by_jaccard ────────────────────────────────────────────


def test_best_chunk_picks_perfect_match() -> None:
    """Exact-content match wins over partial overlap."""
    old = {"p1", "p2", "p3"}
    new_chunks = [
        {"p1", "p2"},          # 2/3 overlap
        {"p1", "p2", "p3"},    # full overlap → wins
        {"p4", "p5"},          # 0
    ]
    assert best_chunk_by_jaccard(old, new_chunks) == 1


def test_best_chunk_picks_highest_partial() -> None:
    old = {"p1", "p2", "p3", "p4"}
    new_chunks = [
        {"p1"},                # 1/4
        {"p1", "p2"},          # 2/4
        {"p1", "p2", "p3"},    # 3/4 → wins
        {"p4"},                # 1/4
    ]
    assert best_chunk_by_jaccard(old, new_chunks) == 2


def test_best_chunk_returns_none_when_no_overlap() -> None:
    """Old chunk's content vanished — no anchor exists. Caller treats
    None as 'drop the pin / rating' rather than 'pick something'."""
    old = {"p1", "p2"}
    new_chunks = [{"p3"}, {"p4"}, {"p5"}]
    assert best_chunk_by_jaccard(old, new_chunks) is None


def test_best_chunk_breaks_ties_by_lowest_index() -> None:
    """Two new chunks tied at the same Jaccard. Prefer the earlier
    one in document order — deterministic so re-anchor is reproducible
    across runs."""
    old = {"p1", "p2"}
    new_chunks = [
        {"p1"},  # 1/2
        {"p2"},  # 1/2
    ]
    assert best_chunk_by_jaccard(old, new_chunks) == 0


def test_best_chunk_handles_empty_old() -> None:
    """An old chunk with no pieces can't anchor anywhere — None."""
    assert best_chunk_by_jaccard(set(), [{"p1"}, {"p2"}]) is None


def test_best_chunk_handles_empty_new_list() -> None:
    """No candidates → no anchor."""
    assert best_chunk_by_jaccard({"p1"}, []) is None


# ─── chunk_containing_offset_range ────────────────────────────────────


def test_offset_range_picks_fully_containing_chunk() -> None:
    """Old word range is entirely inside one new chunk."""
    new_chunks = [
        (0, 100),
        (100, 200),  # contains [120, 180)
        (200, 300),
    ]
    assert chunk_containing_offset_range(new_chunks, 120, 180) == 1


def test_offset_range_picks_max_overlap_when_split() -> None:
    """Old range straddles two new chunks; the one with more bytes
    overlapping wins. Half-open intervals: chunk [100,200) and old
    [180,210) share bytes [180,200) = 20 bytes, vs chunk [200,300)
    sharing [200,210) = 10 bytes. Chunk 1 wins."""
    new_chunks = [(0, 100), (100, 200), (200, 300)]
    assert chunk_containing_offset_range(new_chunks, 180, 210) == 1


def test_offset_range_picks_max_overlap_other_side() -> None:
    """Mirror of the previous: more overlap with the second chunk."""
    new_chunks = [(0, 100), (100, 200), (200, 300)]
    assert chunk_containing_offset_range(new_chunks, 195, 250) == 2


def test_offset_range_no_overlap_returns_none() -> None:
    """The old range falls in a gap or past every new chunk —
    deletion. Caller drops the pin."""
    new_chunks = [(0, 100), (200, 300)]
    assert chunk_containing_offset_range(new_chunks, 120, 180) is None


def test_offset_range_zero_length_returns_none() -> None:
    """A zero-width pin range is nonsensical — defensive None."""
    new_chunks = [(0, 100)]
    assert chunk_containing_offset_range(new_chunks, 50, 50) is None


def test_offset_range_breaks_ties_by_lowest_index() -> None:
    """Two new chunks share equal byte overlap with the old range —
    prefer the earlier one for determinism."""
    new_chunks = [(0, 100), (100, 200)]
    # Range [80, 120) overlaps each by 20 bytes.
    assert chunk_containing_offset_range(new_chunks, 80, 120) == 0


def test_offset_range_adjacent_intervals_do_not_count() -> None:
    """Half-open intervals: chunk ends at 100 (exclusive), pin starts
    at 100 — zero overlap, not a match."""
    new_chunks = [(0, 100), (100, 200)]
    assert chunk_containing_offset_range(new_chunks, 100, 100) is None


# ─── reanchor_chunk_positions ─────────────────────────────────────────


def test_reanchor_batch_each_independent() -> None:
    """Multiple old chunks may map to the same new chunk (collapse)
    or to None (vanished). Each old chunk is anchored independently —
    no global assignment / matching."""
    old_chunks = [
        {"p1", "p2"},   # → fully matches new[0]
        {"p3"},         # → matches new[1]
        {"p4", "p5"},   # → no overlap, None
    ]
    new_chunks = [
        {"p1", "p2"},
        {"p3"},
    ]
    assert reanchor_chunk_positions(old_chunks, new_chunks) == [0, 1, None]


def test_reanchor_batch_collapse_to_same_new() -> None:
    """Two old chunks merged in the new run — both anchor to the
    same new chunk."""
    old_chunks = [{"p1"}, {"p2"}]
    new_chunks = [{"p1", "p2"}]
    assert reanchor_chunk_positions(old_chunks, new_chunks) == [0, 0]


def test_reanchor_batch_empty_old_list() -> None:
    """No old chunks → empty list (not an error)."""
    assert reanchor_chunk_positions([], [{"p1"}]) == []
