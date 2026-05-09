"""Re-anchor primitives — map a piece-set or source-offset range from
an old revision/chunking_run onto the corresponding chunk in a new
revision/chunking_run. Spec: parsem-spec.md (Phase 2 re-anchoring);
bead claude-z99.

The substrate guarantees that every chunk carries a `piece_ordinals`
list (deterministic, ordered, dense per chunking_run) and a
`source_offset_start`/`source_offset_end` pair. When the same source
text is re-chunked, atomic pieces are produced from the same
deterministic builder — so a piece's text_hash is stable across runs
even though its ordinal might shift.

Two distinct re-anchor problems, two distinct primitives:

  - Whole-chunk anchoring (pin colour on a chunk, latest rating):
    `best_chunk_by_jaccard(old_pieces, new_chunks_pieces)` —
    Jaccard of piece-identity sets picks the new chunk that retains
    the most of the old chunk's content.

  - Sub-chunk word range anchoring (word-level pins, post-MVP):
    `chunk_containing_offset_range(new_chunks_offsets, ...)` —
    source-offset overlap finds the new chunk that contains (most
    of) the old word range. Recomputing the new word offsets within
    that chunk is the consumer's responsibility — this primitive
    only solves the chunk-bucket question.

Pure functions, generic over the piece-identity type. No DB, no IO.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def jaccard(a: set[T] | frozenset[T], b: set[T] | frozenset[T]) -> float:
    """Jaccard similarity len(a & b) / len(a | b). Returns 0.0 for two
    empty sets — the convention is "no signal" rather than the
    mathematician's undefined; the consumer treats 0.0 as "no anchor"."""
    if not a and not b:
        return 0.0
    intersection = a & b
    union = a | b
    return len(intersection) / len(union)


def best_chunk_by_jaccard(
    old_pieces: set[T] | frozenset[T],
    new_chunks_pieces: Sequence[set[T] | frozenset[T]],
) -> int | None:
    """Pick the index of the new chunk whose piece-set has the highest
    Jaccard with `old_pieces`. Returns None when every candidate scores
    0.0 (no overlap — the old chunk's content is not in any new chunk;
    consumer falls back to "no anchor" / "drop the pin").

    Ties broken by lowest index — when two new chunks overlap the old
    one equally, prefer the one earlier in the document. Deterministic
    so re-anchor is reproducible across runs.
    """
    if not old_pieces:
        return None
    best_score = 0.0
    best_idx: int | None = None
    for i, np in enumerate(new_chunks_pieces):
        score = jaccard(old_pieces, np)
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx


def chunk_containing_offset_range(
    new_chunks_offsets: Sequence[tuple[int, int]],
    old_offset_start: int,
    old_offset_end: int,
) -> int | None:
    """Pick the index of the new chunk whose source-offset range
    `[start, end)` overlaps the old `[old_offset_start, old_offset_end)`
    range the most. Returns None when no chunk overlaps at all.

    Used for sub-chunk word-range anchoring (word-level pins, post-MVP):
    a pin is a span of source bytes; after a re-chunking run, the span
    is still in the source — we just need to find the new chunk that
    holds (most of) it. Recomputing the new word offsets within that
    chunk is up to the consumer.

    Half-open intervals — end is exclusive — so adjacent ranges don't
    register as overlapping. Ties broken by lowest index.
    """
    if old_offset_end <= old_offset_start:
        return None
    best_overlap = 0
    best_idx: int | None = None
    for i, (cs, ce) in enumerate(new_chunks_offsets):
        overlap = max(0, min(ce, old_offset_end) - max(cs, old_offset_start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_idx = i
    return best_idx


def reanchor_chunk_positions(
    old_chunks_pieces: Sequence[set[T] | frozenset[T]],
    new_chunks_pieces: Sequence[set[T] | frozenset[T]],
) -> list[int | None]:
    """Convenience wrapper: re-anchor every old chunk onto the new
    chunking_run. Returns a list parallel to `old_chunks_pieces`
    where element i is the new-chunk index that best matches old
    chunk i, or None when there is no overlap.

    Useful for batch operations: pin colour cleanup after a re-run,
    rating projection rebuild after a strategy change. Each old chunk
    is anchored independently — multiple old chunks may map to the
    same new chunk (collapsing) or some old chunks may map to None
    (vanished content).
    """
    return [best_chunk_by_jaccard(op, new_chunks_pieces) for op in old_chunks_pieces]
