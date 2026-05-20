"""Cursor engine — walks pieces once, consults priority-ordered rule
values, emits a `ChunkPlan`.

Spec: claude-axx.10 (cursor chunker epic). The engine is a small, dumb
conductor (~80 lines of behaviour). Intelligence lives in the rule
**values** (frozen dataclasses) and in the **annotator values** they
consult. The engine knows about three things:

  - the **bucket** — pieces accumulating into the next chunk
  - the **chunks_so_far** — already-emitted plan entries (rules may
    consult these for lookback decisions like absorb-previous)
  - the **rules** — sorted descending by priority once at boot

At each piece, every rule is consulted in priority order; the first
non-PASS decision wins and the engine executes it. The default
(no rule claims) is `KEEP` — add the piece to the bucket.

Design notes (Hickey + Armstrong):

  - `RuleDecision` is a frozen value with an action + reasons. Rules
    return data, not commands; the engine executes.
  - `Action` enum names what to do, not why. Why = a string reason the
    rule attaches — matches the existing `PlanningReason` vocabulary so
    persisted runs are comparable across engines.
  - Missing-annotation validation happens at strategy construction
    (`compose_strategy` below), not runtime. Let it crash early.
  - The engine is pure: same `(pieces, rules, ruleset)` always produces
    the same `ChunkPlan`. No globals, no clock reads, no logging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from parsem.domain.chunking.annotators import validate_requirements
from parsem.domain.preprocessed import PreprocessedPiece

from . import (
    ChunkingRuleset,
    ChunkPlan,
    PlannedChunk,
    PlanningReason,
)


class Action(Enum):
    """What the engine does in response to a rule's decision.

    `PASS` is a non-claim — the engine tries the next rule. Every other
    action is terminal for the current piece; the engine executes it
    and moves on."""

    PASS = "pass"
    KEEP = "keep"
    FLUSH_THEN_KEEP = "flush_then_keep"
    EMIT_ALONE = "emit_alone"
    FLUSH_THEN_ABSORB_PREVIOUS = "flush_then_absorb_previous"
    SKIP_FLUSH = "skip_flush"


@dataclass(frozen=True)
class RuleDecision:
    """A rule's response when consulted on a piece. `reason` labels the
    emitted/absorbed chunk; `flush_reason` labels any bucket flushed as
    a side effect (so a heading-led bucket flushed by the budget rule
    can be labelled `heading_attach_forward` while the new chunk it
    creates carries its own reason)."""

    action: Action
    reason: PlanningReason = "prose_budget"
    flush_reason: PlanningReason = "prose_budget"


PASS = RuleDecision(action=Action.PASS)


@dataclass(frozen=True)
class CursorContext:
    """Snapshot of engine state visible to a rule's `consult`. Rules
    that need to compare against the budget read `ruleset` here; rules
    that care about how much prose has already packed read
    `bucket_seconds`. Rules that look back at the previously emitted
    chunk's last piece (absorb-style merges) use `pieces` to resolve
    `chunks_so_far[-1].piece_ordinals[-1]` back to a `PreprocessedPiece`.
    Rules ignore what they don't need."""

    ruleset: ChunkingRuleset
    bucket_seconds: float
    pieces: list[PreprocessedPiece]

    def prev_last_piece(self, chunk: PlannedChunk) -> PreprocessedPiece | None:
        """Resolve a planned chunk's trailing piece back to its
        `PreprocessedPiece`. Returns None if the chunk is empty (it
        shouldn't be — the engine enforces non-empty — but cheap to
        guard)."""
        if not chunk.piece_ordinals:
            return None
        return self.pieces[chunk.piece_ordinals[-1]]


class Rule(Protocol):
    """Contract every cursor rule satisfies.

    `priority` orders consultation (higher fires first). `requires`
    names annotation keys this rule reads from `piece.annotations`;
    the engine validates the set at strategy boot. `consult` returns
    a `RuleDecision`; `PASS` means *I don't claim this piece*.

    Metadata is declared as read-only properties so frozen-dataclass
    rule values satisfy the protocol without variance warnings."""

    @property
    def name(self) -> str: ...

    @property
    def priority(self) -> int: ...

    @property
    def requires(self) -> tuple[str, ...]: ...

    def consult(
        self,
        piece: PreprocessedPiece,
        bucket: list[PreprocessedPiece],
        chunks_so_far: list[PlannedChunk],
        ctx: CursorContext,
    ) -> RuleDecision: ...


# Default decision when no rule claims a piece: add to bucket as prose.
_DEFAULT_DECISION = RuleDecision(action=Action.KEEP, reason="prose_budget")


@dataclass(frozen=True)
class ComposedStrategy:
    """A cursor strategy is just a `(name, version, rules, annotators)`
    tuple. Rules are pre-sorted descending by priority once at boot so
    the per-piece loop is allocation-free."""

    name: str
    version: str
    rules: tuple[Rule, ...]
    annotator_names: tuple[str, ...] = ()

    def plan(
        self,
        preprocessed: list[PreprocessedPiece],
        rules: ChunkingRuleset,
    ) -> ChunkPlan:
        return run_cursor(preprocessed, self.rules, rules)


def compose_strategy(
    *,
    name: str,
    version: str,
    rules: tuple[Rule, ...],
    annotator_names: tuple[str, ...] = (),
) -> ComposedStrategy:
    """Build a ComposedStrategy. Validates the rule/annotator contract
    at construction time — a rule requiring an annotation no
    configured annotator produces raises `MissingAnnotationError`
    before any piece is touched."""
    rule_requirements = {r.name: r.requires for r in rules}
    validate_requirements(rule_requirements, annotator_names)
    sorted_rules = tuple(sorted(rules, key=lambda r: -r.priority))
    return ComposedStrategy(
        name=name,
        version=version,
        rules=sorted_rules,
        annotator_names=annotator_names,
    )


def run_cursor(
    pieces: list[PreprocessedPiece],
    rules: tuple[Rule, ...],
    ruleset: ChunkingRuleset,
) -> ChunkPlan:
    """Single-pass cursor walk over `pieces`. Rules consulted in the
    order given (caller is expected to have sorted by priority via
    `compose_strategy`). Returns a `ChunkPlan` ready for materialisation."""
    chunks: list[PlannedChunk] = []
    state = _CursorState(chunks=chunks)
    for piece in pieces:
        ctx = CursorContext(
            ruleset=ruleset,
            bucket_seconds=state.bucket_seconds,
            pieces=pieces,
        )
        decision = _consult(rules, piece, state.bucket, chunks, ctx)
        _apply(decision, piece, state)
    state.final_flush()
    return ChunkPlan(planned_chunks=chunks)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _consult(
    rules: tuple[Rule, ...],
    piece: PreprocessedPiece,
    bucket: list[PreprocessedPiece],
    chunks: list[PlannedChunk],
    ctx: CursorContext,
) -> RuleDecision:
    for rule in rules:
        decision = rule.consult(piece, bucket, chunks, ctx)
        if decision.action != Action.PASS:
            return decision
    return _DEFAULT_DECISION


@dataclass
class _CursorState:
    """Engine-internal mutable state.

    `pending_headings` (claude-axx.10.2 "no orphan headings"): a flush
    of a heading-only bucket does not emit; the heading is stashed
    here and prepended to whatever chunk emits next. At end-of-doc, a
    pending heading with no companion content emits alone (graceful
    degrade — we can't satisfy the constraint when the heading is the
    last thing in the document).

    This is a small named **flush-time policy**, not a piece-arrival
    rule, so it lives in the engine rather than the rule library. If
    multiple flush-time policies ever emerge, lift to a `Guard`
    concept then."""

    chunks: list[PlannedChunk]
    bucket: list[PreprocessedPiece] = field(default_factory=list)
    bucket_seconds: float = 0.0
    pending_headings: list[PreprocessedPiece] = field(default_factory=list)
    pending_seconds: float = 0.0

    def _flush(self, reason: PlanningReason) -> None:
        if not self.bucket:
            return
        # No-orphan-heading policy: if the bucket is all headings, do
        # not emit. Move the bucket into pending and reset; the next
        # emit (any kind) will prepend these as lead pieces.
        if all(p.is_heading for p in self.bucket):
            self.pending_headings.extend(self.bucket)
            self.pending_seconds += self.bucket_seconds
            self.bucket = []
            self.bucket_seconds = 0.0
            return
        self._emit_chunk(pieces=self.bucket, seconds=self.bucket_seconds, reason=reason)
        self.bucket = []
        self.bucket_seconds = 0.0

    def _emit_chunk(
        self,
        *,
        pieces: list[PreprocessedPiece],
        seconds: float,
        reason: PlanningReason,
    ) -> None:
        """Append a chunk to `chunks`, prepending any pending headings
        as lead pieces. Clears pending state after the emit."""
        if self.pending_headings:
            combined_pieces = [*self.pending_headings, *pieces]
            combined_seconds = self.pending_seconds + seconds
            self.pending_headings = []
            self.pending_seconds = 0.0
        else:
            combined_pieces = pieces
            combined_seconds = seconds
        self.chunks.append(_make_chunk(
            ordinal=len(self.chunks),
            pieces=combined_pieces,
            seconds=combined_seconds,
            reason=reason,
        ))

    def _append(self, piece: PreprocessedPiece) -> None:
        self.bucket.append(piece)
        self.bucket_seconds += piece.estimated_read_seconds

    def final_flush(self) -> None:
        # Match legacy end-of-doc reason logic: end_of_document when
        # this is the first chunk OR the trailing bucket has no heading
        # prefix; prose_budget when a heading-led bucket trails the doc.
        if self.bucket:
            if not self.chunks or not _bucket_lead_is_heading(self.bucket):
                self._flush("end_of_document")
            else:
                self._flush("prose_budget")
        # Degrade path: pending headings with nowhere to attach. Emit as
        # a single chunk so we never lose content. The reader sees a
        # bare heading only when the doc literally ends on heading(s).
        if self.pending_headings:
            self._emit_chunk(
                pieces=[],
                seconds=0.0,
                reason="end_of_document" if not self.chunks else "prose_budget",
            )


def _apply(
    decision: RuleDecision,
    piece: PreprocessedPiece,
    state: _CursorState,
) -> None:
    action = decision.action
    if action == Action.KEEP:
        state._append(piece)
        return
    if action == Action.FLUSH_THEN_KEEP:
        state._flush(decision.flush_reason)
        state._append(piece)
        return
    if action == Action.EMIT_ALONE:
        state._flush(decision.flush_reason)
        # Use _emit_chunk so any pending headings prepend to this solo
        # piece — e.g. `heading -> code` becomes one chunk [heading, code]
        # rather than two (no-orphan-heading policy).
        state._emit_chunk(
            pieces=[piece],
            seconds=piece.estimated_read_seconds,
            reason=decision.reason,
        )
        return
    if action == Action.FLUSH_THEN_ABSORB_PREVIOUS:
        # Flush the prose bucket first — the just-emitted chunk is now
        # the absorb target. Then pop it and rebuild as a merged chunk
        # containing this piece. Matches legacy
        # `absorb_colon_lead_in_into_block`: flush, then pop+merge.
        state._flush(decision.flush_reason)
        prev = state.chunks.pop()
        merged_ords = [*prev.piece_ordinals, piece.piece.ordinal]
        merged_seconds = prev.estimated_read_seconds + piece.estimated_read_seconds
        state.chunks.append(PlannedChunk(
            ordinal=len(state.chunks),
            piece_ordinals=merged_ords,
            estimated_read_seconds=merged_seconds,
            lead_piece_ordinal=merged_ords[0],
            reason=decision.reason,
        ))
        return
    if action == Action.SKIP_FLUSH:
        state._flush(decision.flush_reason)
        return
    raise AssertionError(f"unhandled cursor action: {action!r}")


def _make_chunk(
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
