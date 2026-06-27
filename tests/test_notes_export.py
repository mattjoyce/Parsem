"""Tests for the reader-notes export module (notes-export)."""

from __future__ import annotations

from pathlib import Path

from parsem.domain.materialize import Chunk
from parsem.notes_export import (
    note_file_name,
    render_notes_markdown,
    slugify,
    write_notes_file,
)


def _chunk(position: int, text: str) -> Chunk:
    return Chunk(
        position=position,
        source_offset_start=0,
        source_offset_end=len(text),
        text=text,
        lead_token_type="paragraph",
        lead_heading_level=None,
        estimated_read_seconds=1.0,
    )


def test_slugify_lowercases_and_hyphenates() -> None:
    assert slugify("The Great Essay!") == "the-great-essay"


def test_slugify_collapses_garbage_to_untitled() -> None:
    assert slugify("   ") == "untitled"
    assert slugify("***") == "untitled"


def test_note_file_name_prefixes_with_id() -> None:
    assert note_file_name(7, "My Doc") == "7-my-doc.md"


def test_render_includes_prose_note_and_backlink() -> None:
    md = render_notes_markdown(
        title="Doc",
        reader_url="http://h/documents/3/reader",
        notes={1: "first thought"},
        chunks=[_chunk(0, "intro"), _chunk(1, "the prose body")],
    )
    assert "# Notes — Doc" in md
    assert "## Chunk 1" in md
    assert "> the prose body" in md  # prose blockquoted
    assert "first thought" in md
    assert "[↩ Open in Parsem](http://h/documents/3/reader?chunk=1)" in md


def test_render_orders_entries_by_position() -> None:
    md = render_notes_markdown(
        title="Doc",
        reader_url="http://h/documents/3/reader",
        notes={2: "second", 0: "zeroth"},
        chunks=[_chunk(0, "a"), _chunk(1, "b"), _chunk(2, "c")],
    )
    assert md.index("## Chunk 0") < md.index("## Chunk 2")


def test_render_survives_note_with_no_matching_chunk() -> None:
    """Drift after a re-chunk: a note's position may no longer exist.
    The note text must still render; only the prose is omitted."""
    md = render_notes_markdown(
        title="Doc",
        reader_url="http://h/documents/3/reader",
        notes={9: "orphan note"},
        chunks=[_chunk(0, "a")],
    )
    assert "orphan note" in md
    assert "## Chunk 9" in md


def test_write_creates_file_and_returns_path(tmp_path: Path) -> None:
    notes_dir = tmp_path / "vault"  # does not exist yet
    path = write_notes_file(
        notes_dir=notes_dir,
        document_id=4,
        title="Title",
        reader_url="http://h/documents/4/reader",
        notes={0: "note text"},
        chunks=[_chunk(0, "body")],
    )
    assert path == notes_dir / "4-title.md"
    assert path.exists()
    assert "note text" in path.read_text(encoding="utf-8")


def test_write_with_empty_notes_removes_existing_file(tmp_path: Path) -> None:
    """An emptied note set leaves no orphan file behind."""
    args = dict(
        notes_dir=tmp_path,
        document_id=4,
        title="Title",
        reader_url="http://h/documents/4/reader",
        chunks=[_chunk(0, "body")],
    )
    path = write_notes_file(notes=({0: "x"}), **args)  # type: ignore[arg-type]
    assert path.exists()
    again = write_notes_file(notes={}, **args)  # type: ignore[arg-type]
    assert not again.exists()
