"""Chunking strategies — named, versioned, deterministic plan producers.

Spec: AtomicChunkingPhase1.md §Deterministic Strategies. A strategy
takes preprocessed pieces and a ruleset, and emits a `ChunkPlan` over
piece ordinals. It never produces final chunk text — that's
materialization's job.

A strategy's `(name, version, rules_hash)` triple is the provenance for
a `ChunkingRun`. Changing any of them creates a new run, never a
mutation of an old run's meaning.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal, Protocol

from parsem.domain.atomic import AtomicRules
from parsem.domain.preprocessed import PreprocessedPiece, ReadingRules

PlanningReason = Literal[
    "prose_budget",
    "heading_attach_forward",
    "structural_atomic_block",
    "list_run",
    "list_with_colon_lead_in",
    "end_of_document",
]


@dataclass(frozen=True)
class StructuralRules:
    heading_attachment: Literal[
        "alone", "attach_forward", "zero_cost_attach_forward"
    ] = "attach_forward"
    code_handling: Literal["atomic"] = "atomic"
    list_handling: Literal["item", "run"] = "run"
    list_lead_in: Literal["none", "colon_previous_paragraph"] = "colon_previous_paragraph"
    table_handling: Literal["atomic"] = "atomic"
    blockquote_handling: Literal["atomic"] = "atomic"


@dataclass(frozen=True)
class MaterializationRules:
    require_contiguous_chunks: bool = True
    preserve_source_text_when_contiguous: bool = True


@dataclass(frozen=True)
class ChunkingRuleset:
    atomic_rules: AtomicRules = field(default_factory=AtomicRules)
    reading_rules: ReadingRules = field(default_factory=ReadingRules)
    structural_rules: StructuralRules = field(default_factory=StructuralRules)
    materialization_rules: MaterializationRules = field(default_factory=MaterializationRules)

    def rules_hash(self) -> str:
        """Stable hash over all rule fields. Used as `chunking_runs.rules_hash`
        so any rule change produces a new run identity, not a mutation of
        an old run's meaning."""
        payload = {
            "atomic": _dataclass_to_dict(self.atomic_rules),
            "reading": _dataclass_to_dict(self.reading_rules),
            "structural": _dataclass_to_dict(self.structural_rules),
            "materialization": _dataclass_to_dict(self.materialization_rules),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PlannedChunk:
    """A planning decision over piece ordinals. `lead_piece_ordinal` is
    the first piece in document order; the materialiser uses it to
    derive the chunk's `lead_token_type` and `lead_heading_level`."""

    ordinal: int
    piece_ordinals: list[int]
    estimated_read_seconds: float
    lead_piece_ordinal: int
    reason: PlanningReason


@dataclass(frozen=True)
class ChunkPlan:
    planned_chunks: list[PlannedChunk]


class ChunkingStrategy(Protocol):
    """Protocol every strategy implements. `version` bumps when the
    strategy's algorithm changes in a way that affects output."""

    name: str
    version: str

    def plan(
        self,
        preprocessed: list[PreprocessedPiece],
        rules: ChunkingRuleset,
    ) -> ChunkPlan: ...


def validate_chunk_plan(
    plan: ChunkPlan, pieces: list[PreprocessedPiece]
) -> None:
    """Phase 1 plan invariants. Raises AssertionError on first violation.

    HR pieces are skipped from chunking (claude-jvs.3 UAT) — they read
    as blank chunks. They remain in the preprocessed list but must
    never appear in a chunk; the `revealable` set excludes them and
    serves as both the "unknown piece" guard and the completeness set."""
    revealable = {p.piece.ordinal for p in pieces if not p.is_horizontal_rule}
    seen: set[int] = set()
    for i, chunk in enumerate(plan.planned_chunks):
        assert chunk.ordinal == i, (
            f"chunk[{i}] ordinal={chunk.ordinal} (gap or reorder)"
        )
        assert chunk.piece_ordinals, f"chunk[{i}] is empty"
        for ord_ in chunk.piece_ordinals:
            assert ord_ in revealable, (
                f"chunk[{i}] references unknown-or-skipped piece ord={ord_}"
            )
            assert ord_ not in seen, f"chunk[{i}] piece ord={ord_} already assigned"
            seen.add(ord_)
        ordered = sorted(chunk.piece_ordinals)
        assert chunk.piece_ordinals == ordered, (
            f"chunk[{i}] piece ordinals not in document order"
        )
        assert chunk.lead_piece_ordinal == chunk.piece_ordinals[0], (
            f"chunk[{i}] lead_piece_ordinal must equal first piece"
        )
    assert seen == revealable, (
        f"chunk plan missing revealable pieces: {sorted(revealable - seen)}"
    )


def _dataclass_to_dict(obj: object) -> dict[str, object]:
    """Lightweight dataclass→dict that doesn't import dataclasses.asdict
    (which copies; we just want the fields)."""
    return {f: getattr(obj, f) for f in obj.__dataclass_fields__}  # type: ignore[attr-defined]
