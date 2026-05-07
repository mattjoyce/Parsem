"""Tests for parsem.web.view. Spec: parsem-spec.md §15."""

from __future__ import annotations

from parsem.domain.chunking import Chunk, Section
from parsem.web.view import current_section_heading, windowed_chunks


def _chunk(position: int, *, heading: bool = False, text: str | None = None) -> Chunk:
    return Chunk(
        position=position,
        source_offset_start=position * 10,
        source_offset_end=position * 10 + 10,
        text=text or (f"## h-{position}" if heading else f"chunk-{position}"),
        lead_token_type="heading" if heading else "paragraph",
        lead_heading_level=2 if heading else None,
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
