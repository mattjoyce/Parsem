"""Offset → (line, column) lookup over an immutable revision text.

Spec: AtomicChunkingPhase1.md §Revision Validation. Cheap to build (one
linear scan), cheap to query (binary search), trivially serializable
(JSON list of offsets) so it round-trips through the document_revisions
row without losing fidelity.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True)
class LineIndex:
    """Maps a byte/char offset to its (line, column), 0-indexed.

    `line_starts[i]` is the offset of the first character of line `i`.
    A trailing sentinel at `len(text)` is stored so `bisect_right` on the
    final offset still returns a valid index without a special case.
    """

    line_starts: tuple[int, ...]

    @classmethod
    def from_text(cls, text: str) -> LineIndex:
        starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                starts.append(i + 1)
        starts.append(len(text))
        return cls(line_starts=tuple(starts))

    def line_column(self, offset: int) -> tuple[int, int]:
        """Return (line, column) for `offset`. Both 0-indexed."""
        if offset < 0:
            raise ValueError(f"negative offset: {offset}")
        # bisect_right finds the insertion point; the line containing
        # `offset` is one less than that. Clamp to last real line so a
        # query at len(text) lands on the trailing line, not the sentinel.
        line = bisect_right(self.line_starts, offset) - 1
        line = min(line, len(self.line_starts) - 2)
        line = max(line, 0)
        column = offset - self.line_starts[line]
        return line, column

    def to_json(self) -> str:
        return json.dumps(list(self.line_starts))

    @classmethod
    def from_json(cls, s: str) -> LineIndex:
        return cls(line_starts=tuple(json.loads(s)))
