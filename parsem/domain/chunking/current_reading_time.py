"""current_reading_time strategy — rebuilds today's chunker behaviour
through the new substrate.

Spec: AtomicChunkingPhase1.md §Current Reading Time Strategy.

Rules:
  - prose sentences pack greedily up to `budget_seconds`;
  - consecutive paragraph blocks pack across boundaries (the same
    cross-paragraph packing introduced by Parsem-e9t);
  - headings attach forward to the budget (current behaviour);
  - code, list_run, blockquote, table are each one chunk;
  - a colon-terminated previous chunk absorbs into the next list_run
    when `list_lead_in == "colon_previous_paragraph"`.

The planner emits a `ChunkPlan` over piece ordinals only. Final chunk
text is materialised separately from the revision's source text.
"""

from __future__ import annotations

from parsem.domain.preprocessed import PreprocessedPiece

from . import (
    ChunkingRuleset,
    ChunkPlan,
    PlannedChunk,
    PlanningReason,
)


class CurrentReadingTimeStrategy:
    """Reproduces today's deterministic time-based chunking behaviour.

    Renamed to `current_reading_time_legacy` (claude-axx.10): the
    cursor-based composition in `cursor_current_reading_time.py` now
    owns the `current_reading_time` registry key. The legacy strategy
    stays registered so the equivalence test and any manual side-by-
    side comparison (`PARSEM_CHUNKING_STRATEGY=current_reading_time_legacy`)
    can reach it. Slated for deletion once the cursor strategy has soaked."""

    name = "current_reading_time_legacy"
    version = "1.0.0"

    def plan(
        self,
        preprocessed: list[PreprocessedPiece],
        rules: ChunkingRuleset,
    ) -> ChunkPlan:
        return _plan(preprocessed, rules)


def _plan(
    preprocessed: list[PreprocessedPiece],
    rules: ChunkingRuleset,
) -> ChunkPlan:
    chunks: list[PlannedChunk] = []
    bucket: list[PreprocessedPiece] = []
    bucket_seconds = 0.0
    budget = rules.reading_rules.budget_seconds

    def flush(reason: PlanningReason) -> None:
        nonlocal bucket, bucket_seconds
        if not bucket:
            return
        chunks.append(_make_planned_chunk(
            ordinal=len(chunks),
            pieces=bucket,
            seconds=bucket_seconds,
            reason=reason,
        ))
        bucket = []
        bucket_seconds = 0.0

    def absorb_colon_lead_in_into_block(structural: PreprocessedPiece) -> bool:
        """If the most recently emitted chunk's last piece is a colon-
        terminated sentence, fold that whole chunk into a new
        list_with_colon_lead_in chunk that also contains `structural`.

        Generalised in claude-axx.2: the rule fires for *any* structural
        atomic block (code, list_run, blockquote, table) — not just
        list_run. Pedagogical coupling beats tidy budget split. (HR is
        skipped earlier in the loop, so it never reaches here.)

        Returns True on a successful merge.
        """
        if rules.structural_rules.list_lead_in != "colon_previous_paragraph":
            return False
        if not chunks:
            return False
        prev = chunks[-1]
        prev_last_ord = prev.piece_ordinals[-1]
        prev_last_piece = preprocessed[prev_last_ord]
        if prev_last_piece.piece.kind != "sentence":
            return False
        if not prev_last_piece.is_colon_terminated:
            return False
        chunks.pop()
        merged_ords = [*prev.piece_ordinals, structural.piece.ordinal]
        merged_seconds = prev.estimated_read_seconds + structural.estimated_read_seconds
        chunks.append(PlannedChunk(
            ordinal=len(chunks),
            piece_ordinals=merged_ords,
            estimated_read_seconds=merged_seconds,
            lead_piece_ordinal=merged_ords[0],
            reason="list_with_colon_lead_in",
        ))
        return True

    for piece in preprocessed:
        if piece.is_horizontal_rule:
            # HR is a thematic break, not content (claude-jvs.3 UAT).
            # Flush the prose bucket so prose on either side stays
            # separate — the break preserves the author's split — but
            # don't emit a chunk for the HR itself.
            flush("prose_budget")
            continue
        if piece.is_structural_atomic and not piece.is_heading:
            # code_block, list_run, list_item, blockquote, table.
            # Colon-lead-in merge folds these into a preceding
            # colon-terminated paragraph (claude-axx.2 generalised the
            # rule beyond list_run).
            flush("prose_budget")
            if absorb_colon_lead_in_into_block(piece):
                continue
            chunks.append(_make_planned_chunk(
                ordinal=len(chunks),
                pieces=[piece],
                seconds=piece.estimated_read_seconds,
                reason="list_run" if piece.is_list_run else "structural_atomic_block",
            ))
            continue

        if piece.is_heading:
            flush("prose_budget")
            bucket.append(piece)
            bucket_seconds += piece.estimated_read_seconds
            continue

        # Prose: sentence or paragraph piece. Pack greedily up to budget,
        # but keep the bucket non-empty so a single oversized sentence
        # still becomes its own chunk rather than vanishing.
        if bucket and bucket_seconds + piece.estimated_read_seconds > budget:
            flush("heading_attach_forward" if _bucket_lead_is_heading(bucket) else "prose_budget")
        bucket.append(piece)
        bucket_seconds += piece.estimated_read_seconds

    flush("end_of_document" if not chunks or _last_bucket_is_simple(bucket) else "prose_budget")
    return ChunkPlan(planned_chunks=chunks)


def _make_planned_chunk(
    *,
    ordinal: int,
    pieces: list[PreprocessedPiece],
    seconds: float,
    reason: PlanningReason,
) -> PlannedChunk:
    ords = [p.piece.ordinal for p in pieces]
    return PlannedChunk(
        ordinal=ordinal,
        piece_ordinals=ords,
        estimated_read_seconds=seconds,
        lead_piece_ordinal=ords[0],
        reason=reason,
    )


def _bucket_lead_is_heading(bucket: list[PreprocessedPiece]) -> bool:
    return bool(bucket) and bucket[0].is_heading


def _last_bucket_is_simple(bucket: list[PreprocessedPiece]) -> bool:
    """True when the trailing prose bucket has no heading prefix —
    affects only the `reason` label, not the chunk content."""
    return not _bucket_lead_is_heading(bucket)
