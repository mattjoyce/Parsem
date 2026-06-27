"""Reader-notes export — one markdown file per document.

When a reader attaches a note to a chunk (POST /note), the document's
notes are rewritten to a single markdown file under the configured
notes directory (`paths.notes`; point it at an Obsidian vault to have
notes land there). Each entry carries the chunk's prose (blockquoted),
the reader's note, and a deep link back into the Parsem reader at that
exact chunk — so the exported file links home, and the reader surfaces
a link to open the file (the two directions of the deep link).

App-level egress concern, so it lives at the package root alongside
`config.py` (the other module that touches the filesystem). The render
half is pure; only `write_notes_file` does IO.
"""

from __future__ import annotations

import re
from pathlib import Path

from parsem.domain.materialize import Chunk

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase, hyphenate, strip to ``[a-z0-9-]``. Empty/garbage input
    collapses to "untitled" so the filename is always well-formed."""
    slug = _SLUG_STRIP_RE.sub("-", text.lower()).strip("-")
    return slug or "untitled"


def note_file_name(document_id: int, title: str) -> str:
    """Stable per-document filename: ``{id}-{slug}.md``. The id prefix
    keeps the name unique and stable even if two documents share a title
    or the title is later edited."""
    return f"{document_id}-{slugify(title)}.md"


def _blockquote(text: str) -> str:
    """Prefix every line with ``> `` so multi-line prose renders as one
    markdown blockquote. Blank lines become a bare ``>`` to keep the
    quote contiguous."""
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def render_notes_markdown(
    *,
    title: str,
    reader_url: str,
    notes: dict[int, str],
    chunks: list[Chunk],
) -> str:
    """Render a document's notes as a single markdown document. Pure —
    no IO. Entries are ordered by chunk position. ``reader_url`` is the
    document's reader URL with no query string; each entry appends
    ``?chunk={position}`` for the backlink.

    A note whose position has no matching chunk (drift after a re-chunk)
    still renders, with its prose omitted — the note text is never lost.
    """
    by_position = {c.position: c for c in chunks}
    lines: list[str] = [f"# Notes — {title}", ""]
    lines.append(
        "> Exported from Parsem. Each note links back to its place in the reader."
    )
    lines.append("")
    for position in sorted(notes):
        chunk = by_position.get(position)
        lines.append(f"## Chunk {position}")
        lines.append("")
        if chunk is not None and chunk.text.strip():
            lines.append(_blockquote(chunk.text))
            lines.append("")
        lines.append(notes[position])
        lines.append("")
        lines.append(f"[↩ Open in Parsem]({reader_url}?chunk={position})")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_notes_file(
    *,
    notes_dir: Path,
    document_id: int,
    title: str,
    reader_url: str,
    notes: dict[int, str],
    chunks: list[Chunk],
) -> Path:
    """Write (or rewrite) the document's notes file and return its path.

    Creates ``notes_dir`` if needed. When ``notes`` is empty the file is
    removed if present — an emptied note set leaves no orphan file. The
    write is whole-file (notes are few and small per document); the event
    log remains the source of truth, this file is a projection.
    """
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / note_file_name(document_id, title)
    if not notes:
        path.unlink(missing_ok=True)
        return path
    body = render_notes_markdown(
        title=title, reader_url=reader_url, notes=notes, chunks=chunks
    )
    path.write_text(body, encoding="utf-8")
    return path
