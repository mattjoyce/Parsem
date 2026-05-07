"""Reader view helpers — pure functions that turn ReaderState into a
template context dict. Spec: parsem-spec.md §9.5, §15.

Presentation logic only; no IO, no clock, no global state. Forward-compat
note: when Parsem-apa lands section-boundary window clearing, the
``windowed_chunks`` window start grows from ``current - (k-1)`` to
``max(section_start, current - (k-1))`` — same call site, same return
type, just an additional input.
"""

from __future__ import annotations

from typing import Any

from parsem.domain.chunking import Chunk, Section
from parsem.web.state import ReaderState

WINDOW_K = 5


def windowed_chunks(chunks: list[Chunk], current: int, k: int) -> list[Chunk]:
    """Return the last ``k`` chunks ending at ``current``, clamped at zero.

    The slice is inclusive of ``current``: positions ``[max(0, current-k+1) .. current]``.
    """
    return chunks[max(0, current - (k - 1)) : current + 1]


def current_section_heading(
    chunks: list[Chunk], sections: list[Section], current: int
) -> str | None:
    """Return the heading text of the section containing ``current``, or None
    for the prologue (a section with no heading chunk).

    Strips leading ``#`` markers — heading chunks store raw markdown
    (``## Tips for deep reading``) but the sticky banner displays the title
    only.
    """
    for section in sections:
        if section.start_chunk_position <= current <= section.end_chunk_position:
            if section.heading_chunk_position is None:
                return None
            return chunks[section.heading_chunk_position].text.lstrip("#").strip()
    return None


def build_reader_context(
    state: ReaderState, *, bucket_empty: bool = False, k: int = WINDOW_K
) -> dict[str, Any]:
    """Assemble the dict the reader template renders against. Single source
    of truth for the partial's view-model."""
    visible = windowed_chunks(state.chunks, state.current_position, k)
    return {
        "visible_chunks": visible,
        "current_position": state.current_position,
        "section_heading": current_section_heading(
            state.chunks, state.sections, state.current_position
        ),
        "pin_colors": state.pin_colors,
        "bucket_empty": bucket_empty,
        "seconds_until_token": state.bucket_config.regen_seconds,
    }
