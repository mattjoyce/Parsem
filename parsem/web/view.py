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

from parsem.domain.bucket import tokens_now
from parsem.domain.chunking import Chunk, Section
from parsem.web.state import ReaderState

WINDOW_K = 5


def _heading_line(chunk_text: str) -> str:
    """Return the first line of a heading chunk's text with ``#`` markers
    stripped. Heading chunks may carry an absorbed paragraph body (spec
    §11.2); both the title and the section banner display only the title."""
    return chunk_text.split("\n", 1)[0].lstrip("#").strip()


def document_title(chunks: list[Chunk]) -> str:
    """Return the document title — the text of the first H1 heading chunk,
    or "Untitled" if none exists."""
    for c in chunks:
        if c.lead_token_type == "heading" and c.lead_heading_level == 1:
            return _heading_line(c.text)
    return "Untitled"


def windowed_chunks(chunks: list[Chunk], current: int, k: int) -> list[Chunk]:
    """Return the last ``k`` chunks ending at ``current``, clamped at zero.

    The slice is inclusive of ``current``: positions ``[max(0, current-k+1) .. current]``.
    """
    return chunks[max(0, current - (k - 1)) : current + 1]


def current_section_heading(
    chunks: list[Chunk], sections: list[Section], current: int
) -> str | None:
    """Return the heading text of the section containing ``current``, or None
    for the prologue (a section with no heading chunk) AND for H1 sections
    (the H1 *is* the document title — showing it as the section line would
    duplicate it in the top bar). The section line surfaces only when the
    reader is inside an H2-or-deeper section."""
    for section in sections:
        if section.start_chunk_position <= current <= section.end_chunk_position:
            if section.heading_chunk_position is None:
                return None
            if section.heading_level == 1:
                return None
            return _heading_line(chunks[section.heading_chunk_position].text)
    return None


def _dot_classes(filled: int, capacity: int) -> list[str]:
    """One CSS-class suffix per dot in the top-bar pictograph: 'filled' for
    available tokens, 'regen' for the next-to-fill dot (only if any open
    slot exists), 'empty' for the rest. Precomputed here so the template
    just iterates a flat list."""
    classes = ["filled"] * filled
    if filled < capacity:
        classes.append("regen")
        classes.extend(["empty"] * (capacity - filled - 1))
    return classes


def _progress_percent(current: int, total: int) -> float:
    """Width percentage for the progress bar's fill, clamped to [0, 100].
    Guards the empty-document edge case (zero total)."""
    if total <= 0:
        return 0.0
    return round(current * 100 / total, 1)


def build_reader_context(
    state: ReaderState, *, bucket_empty: bool = False, k: int = WINDOW_K
) -> dict[str, Any]:
    """Assemble the dict the reader template renders against. Single source
    of truth for the partial's view-model."""
    visible = windowed_chunks(state.chunks, state.current_position, k)
    filled = tokens_now(state.paid_reveal_times, state.bucket_config, state.clock())
    progress_current = state.current_position + 1
    progress_total = len(state.chunks)
    return {
        "visible_chunks": visible,
        "current_position": state.current_position,
        "section_heading": current_section_heading(
            state.chunks, state.sections, state.current_position
        ),
        "pin_colors": state.pin_colors,
        "bucket_empty": bucket_empty,
        "seconds_until_token": state.bucket_config.regen_seconds,
        "title": document_title(state.chunks),
        "progress_current": progress_current,
        "progress_total": progress_total,
        "progress_percent": _progress_percent(progress_current, progress_total),
        "dot_classes": _dot_classes(filled, state.bucket_config.capacity),
        "regen_seconds": state.bucket_config.regen_seconds,
    }
