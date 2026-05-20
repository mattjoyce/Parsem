"""Pluggable annotators — pure passes that decorate atomic pieces with
per-piece values that rules later consult.

Spec: claude-axx.10 (cursor chunker epic). An annotator:

  - is a pure function `(pieces) -> Mapping[ordinal, Mapping[key, value]]`
  - declares the keys it `produces` so the engine can validate at boot
  - carries a `name` + `version` so a `ChunkingRun` can name its
    provenance and a future cache can key on it

The scaffold ships with no annotators registered. The first lexical
annotator (`transition_edge`) lands in a follow-up bead; this module is
the seam that lets it drop in without engine churn.

Design notes (Hickey + Armstrong):

  - Annotators produce **values** (e.g. a float per piece). They do not
    interpret those values. Naming is noun-claim about data
    (`transition_edge`) not verdict (`is_boundary`).
  - Rules declare `requires=(<key>,)` and the engine validates the union
    at strategy construction time -> missing annotation raises a loud,
    named error rather than no-op-ing at chunk time.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Protocol

from parsem.domain.atomic import AtomicPiece


class Annotator(Protocol):
    """Contract every annotator satisfies. `version` bumps when the
    annotator's output changes shape or value for the same input — so a
    persisted ChunkingRun keyed on the annotator can be invalidated."""

    name: str
    version: str
    produces: tuple[str, ...]

    def annotate(
        self,
        pieces: list[AtomicPiece],
    ) -> Mapping[int, Mapping[str, object]]:
        """Return `ordinal -> key -> value`. Keys must be a subset of
        `produces`. Pieces this annotator has nothing to say about may
        be omitted from the outer mapping."""
        ...


ANNOTATORS: dict[str, Annotator] = {}


def get_annotator(name: str) -> Annotator:
    """Look up by name. Unknown name raises — annotators are
    boot-required, not opportunistic."""
    try:
        return ANNOTATORS[name]
    except KeyError as exc:
        raise UnknownAnnotatorError(name) from exc


def is_known_annotator(name: str) -> bool:
    return name in ANNOTATORS


def annotator_set_hash(names: tuple[str, ...]) -> str:
    """Stable hash over an ordered annotator set. Lays the groundwork
    for `chunking_runs.annotator_set_hash` (provenance + replay). Names
    are sorted to make the hash order-independent — annotators are pure
    and commute."""
    payload = "\n".join(
        f"{name}:{ANNOTATORS[name].version}"
        for name in sorted(names)
        if name in ANNOTATORS
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class UnknownAnnotatorError(KeyError):
    """Raised when a rule's `requires` names an unregistered annotator
    (or `get_annotator` is called with one). Carries the missing name so
    the error message points at the right thing to register."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        registered = sorted(ANNOTATORS.keys()) or ["<none>"]
        return (
            f"annotator {self.name!r} is not registered. "
            f"Known annotators: {registered}."
        )


class MissingAnnotationError(LookupError):
    """Raised at ruleset construction when a rule requires an annotation
    key that no configured annotator produces. Names both the rule and
    the key — fail early with a useful message rather than silently
    no-op-ing at chunk time."""

    def __init__(self, rule_name: str, missing_key: str) -> None:
        super().__init__(missing_key)
        self.rule_name = rule_name
        self.missing_key = missing_key

    def __str__(self) -> str:
        return (
            f"rule {self.rule_name!r} requires annotation "
            f"{self.missing_key!r}, which no configured annotator produces"
        )


def validate_requirements(
    rule_requirements: Mapping[str, tuple[str, ...]],
    annotator_names: tuple[str, ...],
) -> None:
    """Check every rule's `requires` against the union of keys produced
    by the configured annotators. Raises `MissingAnnotationError` on the
    first miss. `rule_requirements` is `rule_name -> required_keys`."""
    available: set[str] = set()
    for name in annotator_names:
        annotator = get_annotator(name)
        available.update(annotator.produces)
    for rule_name, required in rule_requirements.items():
        for key in required:
            if key not in available:
                raise MissingAnnotationError(rule_name, key)
