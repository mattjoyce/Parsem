"""Tests for parsem.web.view. Spec: parsem-spec.md §15."""

from __future__ import annotations

from parsem.domain.chunking import Chunk, Section
from parsem.web.view import (
    _dot_classes,
    current_section_heading,
    document_title,
    next_chunk,
    windowed_chunks,
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


def test_windowed_chunks_returns_last_k_when_far_into_document() -> None:
    chunks = [_chunk(i) for i in range(10)]
    window = windowed_chunks(chunks, current=8, k=5)
    assert [c.position for c in window] == [4, 5, 6, 7, 8]


def test_windowed_chunks_clamps_at_position_zero() -> None:
    chunks = [_chunk(i) for i in range(10)]
    window = windowed_chunks(chunks, current=0, k=5)
    assert [c.position for c in window] == [0]


def test_windowed_chunks_clamps_when_current_less_than_k_minus_one() -> None:
    chunks = [_chunk(i) for i in range(10)]
    window = windowed_chunks(chunks, current=2, k=5)
    assert [c.position for c in window] == [0, 1, 2]


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


def test_next_chunk_returns_chunk_at_current_plus_one() -> None:
    chunks = [_chunk(0), _chunk(1), _chunk(2)]
    assert next_chunk(chunks, current=0) is chunks[1]
    assert next_chunk(chunks, current=1) is chunks[2]


def test_next_chunk_returns_none_at_end_of_document() -> None:
    chunks = [_chunk(0), _chunk(1)]
    assert next_chunk(chunks, current=1) is None


def test_next_chunk_returns_none_for_empty_chunks() -> None:
    assert next_chunk([], current=0) is None


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
