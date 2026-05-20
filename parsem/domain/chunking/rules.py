"""Cursor rule values — the building blocks strategies compose.

Spec: claude-axx.10. Each rule is a frozen dataclass implementing the
`Rule` protocol from `cursor.py`. Rules are *values*, not classes-with-
behaviour: their fields are the policy knobs, their `consult()` is
pure. A strategy is just an ordered tuple of these.

The rules shipped here are the ones needed to reproduce
`current_reading_time` over the cursor engine, byte-identically:

  - HorizontalRuleSkip       — flush bucket, drop the HR piece
  - ColonLeadInAbsorb        — fold structural-after-colon back into prev
  - StructuralAtomicEmitAlone — each code/list/blockquote/table is a chunk
  - HeadingAttachForward     — heading flushes prose, then leads new bucket
  - BudgetSplit              — flush when adding piece would exceed budget

Priorities are spaced 10 apart so a future rule can slot between
without renumbering. Naming follows Hickey: each rule is named for what
it does to a piece, not what it claims about the document.
"""

from __future__ import annotations

from dataclasses import dataclass

from parsem.domain.preprocessed import PreprocessedPiece

from . import PlannedChunk, PlanningReason
from .cursor import PASS, Action, CursorContext, RuleDecision


@dataclass(frozen=True)
class HorizontalRuleSkip:
    """An HR is a thematic break, not content. Flush the prose bucket
    so prose on either side of the break stays separate (the author's
    split is preserved), but do not emit a chunk for the HR itself.

    Highest priority so HR is handled before any other rule could try
    to claim it (none would today, but priority order makes the intent
    explicit)."""

    name: str = "horizontal_rule_skip"
    priority: int = 100
    requires: tuple[str, ...] = ()

    def consult(
        self,
        piece: PreprocessedPiece,
        bucket: list[PreprocessedPiece],
        chunks_so_far: list[PlannedChunk],
        ctx: CursorContext,
    ) -> RuleDecision:
        if piece.is_horizontal_rule:
            return RuleDecision(
                action=Action.SKIP_FLUSH,
                flush_reason="prose_budget",
            )
        return PASS


@dataclass(frozen=True)
class ColonLeadInAbsorb:
    """When a structural atomic piece (code, list_run, blockquote,
    table — not a heading) arrives, and the prose that would be flushed
    OR the previously-emitted chunk ends in a colon-terminated
    sentence, fold the lead-in into a single `list_with_colon_lead_in`
    chunk that also contains the structural piece.

    Pedagogical coupling beats tidy budget split — `Here are three
    items:` should never be separated from the list it introduces.

    Fires *before* `StructuralAtomicEmitAlone` so absorb wins when
    both would match. The action is `FLUSH_THEN_ABSORB_PREVIOUS`: the
    engine flushes the bucket first (making it the absorb target),
    then pops+merges with the structural piece. When the bucket is
    empty, the existing `chunks_so_far[-1]` is the absorb target."""

    name: str = "colon_lead_in_absorb"
    priority: int = 90
    requires: tuple[str, ...] = ()

    def consult(
        self,
        piece: PreprocessedPiece,
        bucket: list[PreprocessedPiece],
        chunks_so_far: list[PlannedChunk],
        ctx: CursorContext,
    ) -> RuleDecision:
        if not piece.is_structural_atomic or piece.is_heading:
            return PASS
        # The candidate for absorb is the piece that will be at the tail
        # of chunks[-1] AFTER the imminent flush. If the bucket is
        # non-empty, that's bucket[-1]. If the bucket is empty, it's the
        # existing last piece of chunks_so_far[-1].
        if bucket:
            candidate: PreprocessedPiece | None = bucket[-1]
        elif chunks_so_far:
            candidate = ctx.prev_last_piece(chunks_so_far[-1])
        else:
            return PASS
        if candidate is None:
            return PASS
        if candidate.piece.kind != "sentence":
            return PASS
        if not candidate.is_colon_terminated:
            return PASS
        return RuleDecision(
            action=Action.FLUSH_THEN_ABSORB_PREVIOUS,
            reason="list_with_colon_lead_in",
            flush_reason="prose_budget",
        )


@dataclass(frozen=True)
class StructuralAtomicEmitAlone:
    """Code blocks, list runs, blockquotes, tables — each is its own
    chunk. Flush any prose bucket first; emit the piece alone.

    The reason label depends on kind: `list_run` for list runs (matches
    legacy), `structural_atomic_block` for everything else.

    Fires *after* `ColonLeadInAbsorb` so absorb wins the structural+colon
    case. If absorb passed (no colon lead-in, or bucket non-empty), this
    rule handles the flush-and-emit."""

    name: str = "structural_atomic_emit_alone"
    priority: int = 80
    requires: tuple[str, ...] = ()

    def consult(
        self,
        piece: PreprocessedPiece,
        bucket: list[PreprocessedPiece],
        chunks_so_far: list[PlannedChunk],
        ctx: CursorContext,
    ) -> RuleDecision:
        if not piece.is_structural_atomic or piece.is_heading:
            return PASS
        reason: PlanningReason = (
            "list_run" if piece.is_list_run else "structural_atomic_block"
        )
        return RuleDecision(
            action=Action.EMIT_ALONE,
            reason=reason,
            flush_reason="prose_budget",
        )


@dataclass(frozen=True)
class HeadingAttachForward:
    """A heading flushes any existing prose bucket and then becomes the
    lead piece of the next bucket — the forward-attach rule from the
    legacy planner. The bucket it starts will pack subsequent prose
    pieces under it until budget hits (or another structural event)."""

    name: str = "heading_attach_forward"
    priority: int = 70
    requires: tuple[str, ...] = ()

    def consult(
        self,
        piece: PreprocessedPiece,
        bucket: list[PreprocessedPiece],
        chunks_so_far: list[PlannedChunk],
        ctx: CursorContext,
    ) -> RuleDecision:
        if not piece.is_heading:
            return PASS
        return RuleDecision(
            action=Action.FLUSH_THEN_KEEP,
            flush_reason="prose_budget",
        )


@dataclass(frozen=True)
class BudgetSplit:
    """When adding the incoming piece to a non-empty bucket would push
    `bucket_seconds + piece.estimated_read_seconds` past
    `ruleset.reading_rules.budget_seconds`, flush the bucket and start
    a fresh one containing the piece. The flush reason is
    `heading_attach_forward` when the bucket is heading-led (the
    heading-attach forward bucket has hit its limit), else
    `prose_budget`.

    Lowest priority of the shipped rules: it only fires on plain prose
    that no structural rule claimed."""

    name: str = "budget_split"
    priority: int = 60
    requires: tuple[str, ...] = ()

    def consult(
        self,
        piece: PreprocessedPiece,
        bucket: list[PreprocessedPiece],
        chunks_so_far: list[PlannedChunk],
        ctx: CursorContext,
    ) -> RuleDecision:
        if not bucket:
            return PASS
        budget = ctx.ruleset.reading_rules.budget_seconds
        if ctx.bucket_seconds + piece.estimated_read_seconds <= budget:
            return PASS
        flush_reason: PlanningReason = (
            "heading_attach_forward"
            if bucket and bucket[0].is_heading
            else "prose_budget"
        )
        return RuleDecision(
            action=Action.FLUSH_THEN_KEEP,
            flush_reason=flush_reason,
        )
