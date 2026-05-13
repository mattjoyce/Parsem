"""Reader view helpers — pure functions that turn ReaderState into a
template context dict. Spec: parsem-spec.md §9.5, §15.

Presentation logic only; no IO, no clock, no global state.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from markdown_it import MarkdownIt
from markupsafe import Markup

from parsem.domain.bucket import tokens_now
from parsem.domain.materialize import Chunk
from parsem.web.state import ReaderState

_RENDERER = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
# CommonMark + the GFM `table` rule (claude-l51) and `strikethrough`
# (claude-ucd) — matches the parser (`markdown_parse._PARSER`) so a
# pipe-table that the chunker kept whole renders as a real <table> (not
# literal `|` text) and `~~text~~` renders as <s>text</s> like Obsidian.
# Explicit `html=False` — the commonmark profile actually defaults to
# `html=True`, so we override. Raw HTML / `<script>` in user-uploaded
# markdown is escaped to text. Single-user local-first app — the user
# owns their corpus — but defense in depth keeps the render layer
# honest (Parsem-kli).


@lru_cache(maxsize=2000)
def render_chunk_html(text: str) -> Markup:
    """Render a chunk's source markdown as HTML, wrapped in `Markup`
    so Jinja autoescape passes it through unmodified.

    Memoized: every reveal/conceal/pin POST re-renders the entire
    revealed prefix. Chunk text is immutable, so the same chunk's
    HTML is computed once per process. Bounded cache keeps memory
    flat across multiple opened documents."""
    return Markup(_RENDERER.render(text))


# Closing tags we'll inject the inline reveal glyph BEFORE — text-bearing
# blocks where the glyph naturally follows the last character of prose.
# `<pre>` and `<table>` are intentionally excluded: injecting inside a
# code fence would break monospace flow. Chunks composed entirely of
# such blocks fall back to append-after-close.
_INLINE_GLYPH_TARGETS: tuple[str, ...] = (
    "</p>",
    "</li>",
    "</h1>", "</h2>", "</h3>", "</h4>", "</h5>", "</h6>",
    "</blockquote>",
)

# The two pre-rendered glyph variants. Filled diamond ♦ (U+2666) chosen
# over the prior » so the affordance reads as a chunky symbol, not as
# trailing punctuation (claude-jvs UAT). Empty-bucket variant carries
# the --empty modifier for the visual ghost; JS no longer reads it for
# click routing (server's X-Reveal-Outcome is authoritative — claude-
# jvs fix for the bucket-regen click bug).
_REVEAL_BTN: Markup = Markup(
    '<button type="button" class="reveal-symbol" '
    'aria-label="Reveal next chunk">&#9830;</button>'
)
_REVEAL_BTN_EMPTY: Markup = Markup(
    '<button type="button" class="reveal-symbol reveal-symbol--empty" '
    'aria-label="Reveal next chunk" aria-disabled="true">&#9830;</button>'
)


def _inject_reveal_symbol(html: Markup, button: Markup) -> Markup:
    """Insert ``button`` immediately before the latest text-bearing
    closing tag in ``html`` so the inline reveal glyph sits at the end
    of the chunk's last text line — not on a new line below. Falls
    back to appending if no text-bearing block is present (rare —
    pure-code-fence chunks)."""
    last_pos = max(
        (html.rfind(tag) for tag in _INLINE_GLYPH_TARGETS),
        default=-1,
    )
    if last_pos == -1:
        return html + button
    return html[:last_pos] + button + html[last_pos:]


@dataclass(frozen=True)
class VisibleChunk:
    """Template-side bundle of a chunk and its rendered HTML.
    The chunk carries position/pin metadata; the html is the
    Obsidian-style rendered body."""

    chunk: Chunk
    html: Markup


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


def next_chunk(chunks: list[Chunk], high_water: int) -> Chunk | None:
    """Return the chunk at ``high_water + 1`` — the next chunk a paid
    reveal would expose — or None at end-of-document. Anchored on
    `high_water_position` rather than `current_position` because the
    preview gutter (spec §9.5) always shows what Space would *spend*
    a token to reach. When the reader has clicked back (claude-axx.3),
    the chunks between current and high_water are already visible, so
    the preview must look further forward — past the frontier."""
    if high_water + 1 < len(chunks):
        return chunks[high_water + 1]
    return None


def revealed_chunks(chunks: list[Chunk], high_water: int) -> list[Chunk]:
    """All chunks ever revealed in this session, INCLUDING the chunk
    just revealed at the frontier (`high_water_position`).

    Anchored on `high_water_position`, not `current_position`. The
    distinction matters once pointer-back-nav (claude-axx.3, §8a) and
    Backspace-conceal (§7.2) can shift `current_position` behind
    `high_water_position`: the reader is still "in" all chunks they
    have paid for and the growing-document model (Parsem-kli, §15)
    must keep them visible. Hiding chunks 6-10 when the reader clicks
    back to chunk 5 would break the trail they were reading.

    Supersedes the Parsem-apa section-clamped sliding window. The
    .chunk--current vertical bar marks "now"; everything else carries
    its full settled rendering (claude-axx — settled opacity is 1.0)."""
    return chunks[: high_water + 1]


def current_section_heading(
    chunks: list[Chunk], sections: list[Any], current: int
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


def _dot_classes(filled: int, capacity: int, regen_seconds: int) -> list[tuple[str, float]]:
    """``(class_suffix, animation_delay_seconds)`` per dot in the top-bar
    pictograph. Filled dots get delay 0. Every open slot is class ``regen``
    with delay staggered at ``i * regen_seconds`` so the cascade plays out
    client-side over ``open_count * regen_seconds`` without server polling.

    Phase 1 simplification: cascade restarts on each server render
    (no phase-aware mid-cycle resume; spec §15.4 "starting points,
    tuned for feel").
    """
    result: list[tuple[str, float]] = [("filled", 0.0)] * filled
    open_count = capacity - filled
    for i in range(open_count):
        result.append(("regen", float(i * regen_seconds)))
    return result


def _progress_percent(current: int, total: int) -> float:
    """Width percentage for the progress bar's fill, clamped to [0, 100].
    Guards the empty-document edge case (zero total)."""
    if total <= 0:
        return 0.0
    return round(current * 100 / total, 1)


def build_reader_context(state: ReaderState) -> dict[str, Any]:
    """Assemble the dict the reader template renders against. Single source
    of truth for the partial's view-model.

    Each entry in `visible_chunks` is a `VisibleChunk` carrying both the
    chunk and its rendered HTML, so the template doesn't run markdown
    rendering inline.
    """
    visible = [
        VisibleChunk(chunk=c, html=render_chunk_html(c.text))
        for c in revealed_chunks(state.chunks, state.high_water_position)
    ]
    upcoming = next_chunk(state.chunks, state.high_water_position)
    upcoming_visible = (
        VisibleChunk(chunk=upcoming, html=render_chunk_html(upcoming.text))
        if upcoming is not None
        else None
    )
    filled = tokens_now(state.paid_reveal_times, state.bucket_config, state.clock())
    # Bucket-empty drives the reveal symbol's ghosted state (§8a.4) — when
    # the bucket has no tokens, the server-rendered class hints the visual.
    # JS no longer reads it for click routing; the server's X-Reveal-Outcome
    # header is authoritative (claude-jvs — bucket-regen click bug).
    bucket_empty = filled == 0
    # Glyph injection runs POST-cache — context-dependent state
    # (bucket_empty, end-of-document, current vs settled) never enters
    # render_chunk_html's lru_cache key. visible[current_position]
    # indexes directly because revealed_chunks returns chunks[:high+1]
    # in position order. The bounds guard handles transient states
    # where current_position > high_water_position (e.g. a jump-to-pin
    # to a pin set on an unrevealed chunk in tests) — in that case the
    # current chunk isn't visible at all, so there's no glyph to inject.
    if upcoming is not None and 0 <= state.current_position < len(visible):
        button = _REVEAL_BTN_EMPTY if bucket_empty else _REVEAL_BTN
        current = visible[state.current_position]
        visible[state.current_position] = VisibleChunk(
            chunk=current.chunk,
            html=_inject_reveal_symbol(current.html, button),
        )
    progress_current = state.current_position + 1
    progress_total = len(state.chunks)
    return {
        "document_id": state.document_id,
        "visible_chunks": visible,
        "current_position": state.current_position,
        "high_water_position": state.high_water_position,
        "section_heading": current_section_heading(
            state.chunks, state.sections, state.current_position
        ),
        "pin_colors": state.pin_colors,
        "chunk_ratings": state.chunk_ratings,
        "current_chunk_rating": state.chunk_ratings.get(state.current_position),
        "title": document_title(state.chunks),
        "progress_current": progress_current,
        "progress_total": progress_total,
        "progress_percent": _progress_percent(progress_current, progress_total),
        "dot_classes": _dot_classes(
            filled, state.bucket_config.capacity, state.bucket_config.regen_seconds
        ),
        "regen_seconds": state.bucket_config.regen_seconds,
        "next_chunk": upcoming_visible,
        "bucket_empty": bucket_empty,
    }
