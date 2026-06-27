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


def _callout(kind: str, title: str, body: str) -> str:
    """Render an Obsidian callout — ``> [!kind] title`` then the body as
    blockquote lines, so the whole block is one collapsible, titled box
    in a vault (and reads fine as plain text / to an AI agent). Callers
    MUST leave a blank line between adjacent callouts or Obsidian merges
    them."""
    head = f"> [!{kind}] {title}"
    quoted = _blockquote(body)
    return f"{head}\n{quoted}" if quoted else head


def _yaml_str(value: str) -> str:
    """Double-quote a scalar for YAML frontmatter, escaping backslashes
    and quotes so a title with a colon/quote can't break the block."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_notes_markdown(
    *,
    title: str,
    document_id: int,
    reader_url: str,
    notes: dict[int, str],
    chunks: list[Chunk],
    generated_at: str | None = None,
) -> str:
    """Render a document's notes as a shareable markdown document. Pure —
    no IO.

    Shape (so an AI agent handed the URL sees both the source and the
    note): YAML frontmatter about the parent document, then one section
    per noted chunk in position order. Each section is an Obsidian-native
    pair of callouts — a ``[!quote]`` carrying the chunk's prose and a
    deep link back to the reader, and a ``[!note]`` titled "Notes about
    Chunk NN" carrying the reader's note — separated by ``---`` between
    chunks. ``reader_url`` is the reader URL with no query string; the
    quote callout title appends ``?chunk={position}`` for the link.

    A note whose position has no matching chunk (drift after a re-chunk)
    still renders, with its quote callout omitted — the note is never
    lost.
    """
    by_position = {c.position: c for c in chunks}
    fm: list[str] = [
        "---",
        f"document: {_yaml_str(title)}",
        f"document_id: {document_id}",
        f"reader_url: {reader_url}",
        f"notes_count: {len(notes)}",
    ]
    if generated_at is not None:
        fm.append(f"exported: {generated_at}")
    fm.append("---")
    lines: list[str] = [*fm, "", f"# Notes — {title}", ""]
    if not notes:
        lines.append("_No notes yet._")
        return "\n".join(lines) + "\n"
    for position in sorted(notes):
        chunk = by_position.get(position)
        lines.append(f"## Chunk {position}")
        lines.append("")
        if chunk is not None and chunk.text.strip():
            link = f"Chunk {position} · [open in reader →]({reader_url}?chunk={position})"
            lines.append(_callout("quote", link, chunk.text))
            lines.append("")  # blank line — keeps the two callouts separate
        lines.append(_callout("note", f"Notes about Chunk {position}", notes[position]))
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
    generated_at: str | None = None,
) -> Path:
    """Write (or rewrite) the document's notes file and return its path.

    Creates ``notes_dir`` if needed. When ``notes`` is empty the file is
    removed if present — an emptied note set leaves no orphan file. The
    write is whole-file (notes are few and small per document); the event
    log remains the source of truth, this file (and the
    ``GET /documents/{id}/notes`` endpoint) are projections of it.
    """
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / note_file_name(document_id, title)
    if not notes:
        path.unlink(missing_ok=True)
        return path
    body = render_notes_markdown(
        title=title,
        document_id=document_id,
        reader_url=reader_url,
        notes=notes,
        chunks=chunks,
        generated_at=generated_at,
    )
    path.write_text(body, encoding="utf-8")
    return path
