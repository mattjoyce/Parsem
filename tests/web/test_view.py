"""Tests for parsem.web.view. Spec: parsem-spec.md §15; bead Parsem-kli."""

from __future__ import annotations

from parsem.domain.chunking import Chunk, Section
from parsem.web.view import (
    _dot_classes,
    current_section_heading,
    document_title,
    next_chunk,
    render_chunk_html,
    revealed_chunks,
)


def _chunk(
    position: int,
    *,
    heading: bool = False,
    heading_level: int = 2,
    text: str | None = None,
) -> Chunk:
    return Chunk(
        position=position,
        source_offset_start=position * 10,
        source_offset_end=position * 10 + 10,
        text=text or (f"{'#' * heading_level} h-{position}" if heading else f"chunk-{position}"),
        lead_token_type="heading" if heading else "paragraph",
        lead_heading_level=heading_level if heading else None,
        estimated_read_seconds=1.0,
    )


def _section(start: int, end: int, heading_pos: int | None = None) -> Section:
    return Section(
        heading_chunk_position=heading_pos,
        heading_level=2 if heading_pos is not None else None,
        start_chunk_position=start,
        end_chunk_position=end,
    )


# ─── revealed_chunks: growing-document model (Parsem-kli) ─────────────


def test_revealed_chunks_at_high_water_zero_returns_just_the_first() -> None:
    chunks = [_chunk(i) for i in range(10)]
    assert [c.position for c in revealed_chunks(chunks, high_water=0)] == [0]


def test_revealed_chunks_mid_document_returns_all_paid() -> None:
    chunks = [_chunk(i) for i in range(10)]
    assert [c.position for c in revealed_chunks(chunks, high_water=4)] == [0, 1, 2, 3, 4]


def test_revealed_chunks_at_end_of_document_returns_every_chunk() -> None:
    chunks = [_chunk(i) for i in range(5)]
    assert [c.position for c in revealed_chunks(chunks, high_water=4)] == [0, 1, 2, 3, 4]


def test_revealed_chunks_does_not_clamp_at_section_boundaries() -> None:
    """Crossing a section heading no longer clears the visible set —
    the reader is now a growing rendered document (Parsem-kli supersedes
    the Parsem-apa section-clamp)."""
    chunks = [_chunk(i) for i in range(10)]
    # Two sections: [0..4] and [5..9]. At position 7, all of [0..7] visible.
    assert [c.position for c in revealed_chunks(chunks, high_water=7)] == [0, 1, 2, 3, 4, 5, 6, 7]


def test_revealed_chunks_after_click_back_still_shows_paid_territory() -> None:
    """When the reader clicks back (claude-axx.3), `current_position`
    drops behind `high_water_position`. The growing-document model
    (§15) keeps every paid chunk visible — the trail must not shorten
    just because the cursor moved back. Anchoring on `high_water`,
    not `current`, is what guarantees this."""
    chunks = [_chunk(i) for i in range(10)]
    # Reader paid up to chunk 7, clicked back to chunk 3 — chunks 4..7
    # are still in the DOM so the back-scrub trail stays intact.
    assert [c.position for c in revealed_chunks(chunks, high_water=7)] == [
        0, 1, 2, 3, 4, 5, 6, 7,
    ]


# ─── render_chunk_html: markdown → HTML ──────────────────────────────


def test_render_chunk_html_renders_heading() -> None:
    html = render_chunk_html("# Hello\n")
    assert "<h1>Hello</h1>" in html


def test_render_chunk_html_renders_list() -> None:
    html = render_chunk_html("- one\n- two\n")
    assert "<ul>" in html
    assert "<li>one</li>" in html


def test_render_chunk_html_renders_blockquote() -> None:
    html = render_chunk_html("> a quote\n")
    assert "<blockquote>" in html


def test_render_chunk_html_renders_code_fence() -> None:
    html = render_chunk_html("```python\nprint(1)\n```\n")
    assert "<pre>" in html and "<code" in html


def test_render_chunk_html_escapes_raw_html_tags() -> None:
    """commonmark mode keeps html=False — embedded <script> stays as
    text. Single-user app, but defense in depth."""
    html = render_chunk_html("<script>alert(1)</script>\n")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_chunk_html_renders_heading_absorbed_chunk() -> None:
    """Heading-absorbed chunks pack `# T\\n\\nbody` into one chunk; the
    renderer must produce both the heading and the paragraph."""
    html = render_chunk_html("# Welcome\n\nParsem is a reading chamber.")
    assert "<h1>Welcome</h1>" in html
    assert "<p>Parsem is a reading chamber.</p>" in html


def test_current_section_heading_returns_heading_text_for_section() -> None:
    chunks = [
        _chunk(0, heading=True, text="## Welcome"),
        _chunk(1),
        _chunk(2),
    ]
    sections = [
        Section(
            heading_chunk_position=0,
            heading_level=2,
            start_chunk_position=0,
            end_chunk_position=2,
        )
    ]
    # Stripped of leading `#` markers so the sticky banner shows the title only.
    assert current_section_heading(chunks, sections, current=1) == "Welcome"


def test_current_section_heading_returns_none_for_prologue() -> None:
    chunks = [_chunk(0), _chunk(1, heading=True, text="## Body")]
    sections = [
        Section(
            heading_chunk_position=None,
            heading_level=None,
            start_chunk_position=0,
            end_chunk_position=0,
        ),
        Section(
            heading_chunk_position=1,
            heading_level=2,
            start_chunk_position=1,
            end_chunk_position=1,
        ),
    ]
    assert current_section_heading(chunks, sections, current=0) is None


def test_document_title_returns_first_h1_heading_text() -> None:
    chunks = [_chunk(0, heading=True, heading_level=1, text="# Welcome to Parsem"), _chunk(1)]
    assert document_title(chunks) == "Welcome to Parsem"


def test_document_title_strips_absorbed_paragraph_body() -> None:
    # Heading absorption again — title from H1 should be only the first line.
    text = "# Welcome to Parsem\n\nParsem is a reading chamber, not a scrolling viewer."
    chunks = [_chunk(0, heading=True, heading_level=1, text=text), _chunk(1)]
    assert document_title(chunks) == "Welcome to Parsem"


def test_document_title_returns_untitled_when_no_h1_exists() -> None:
    # Doc with only H2s and paragraphs.
    chunks = [_chunk(0), _chunk(1, heading=True, heading_level=2, text="## Section")]
    assert document_title(chunks) == "Untitled"


def test_next_chunk_returns_chunk_at_high_water_plus_one() -> None:
    chunks = [_chunk(0), _chunk(1), _chunk(2)]
    assert next_chunk(chunks, high_water=0) is chunks[1]
    assert next_chunk(chunks, high_water=1) is chunks[2]


def test_next_chunk_returns_none_at_end_of_document() -> None:
    chunks = [_chunk(0), _chunk(1)]
    assert next_chunk(chunks, high_water=1) is None


def test_next_chunk_returns_none_for_empty_chunks() -> None:
    assert next_chunk([], high_water=0) is None


def test_dot_classes_full_bucket_emits_only_filled_with_zero_delay() -> None:
    assert _dot_classes(filled=5, capacity=5, regen_seconds=12) == [
        ("filled", 0.0),
        ("filled", 0.0),
        ("filled", 0.0),
        ("filled", 0.0),
        ("filled", 0.0),
    ]


def test_dot_classes_partial_bucket_staggers_open_slots_as_regen() -> None:
    # 3 filled + 2 open: delays 0, 12 for the open slots so they cascade.
    assert _dot_classes(filled=3, capacity=5, regen_seconds=12) == [
        ("filled", 0.0),
        ("filled", 0.0),
        ("filled", 0.0),
        ("regen", 0.0),
        ("regen", 12.0),
    ]


def test_dot_classes_empty_bucket_staggers_all_five_as_regen() -> None:
    # Every dot is regen; cascade plays out over 5 * 12s = 60s.
    assert _dot_classes(filled=0, capacity=5, regen_seconds=12) == [
        ("regen", 0.0),
        ("regen", 12.0),
        ("regen", 24.0),
        ("regen", 36.0),
        ("regen", 48.0),
    ]


def test_current_section_heading_returns_none_for_h1_section() -> None:
    # The H1 IS the document title; showing it as the section line would
    # duplicate it in the top bar (Designer review §9.5). Section heading
    # surfaces only inside H2-or-deeper sections.
    chunks = [_chunk(0, heading=True, heading_level=1, text="# Welcome")]
    sections = [
        Section(
            heading_chunk_position=0,
            heading_level=1,
            start_chunk_position=0,
            end_chunk_position=0,
        )
    ]
    assert current_section_heading(chunks, sections, current=0) is None


def test_current_section_heading_strips_absorbed_paragraph_body() -> None:
    # Heading absorption (spec §11.2) concatenates heading text + body
    # paragraph into one chunk.text. The sticky banner must show only the
    # heading line — the absorbed body belongs to the chunk, not the title.
    absorbed = "## Tips for deep reading\n\nRead with the keyboard, not the mouse."
    chunks = [_chunk(0, heading=True, text=absorbed)]
    sections = [
        Section(
            heading_chunk_position=0,
            heading_level=2,
            start_chunk_position=0,
            end_chunk_position=0,
        )
    ]
    assert current_section_heading(chunks, sections, current=0) == "Tips for deep reading"
