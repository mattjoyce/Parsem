"""On-disk layout of a stored document.

Spec: ADR docs/adr/0001-nas-ingest-pipeline.md (directory contract);
ADR 0002 (ductile-driven eventing); bd claude-5h0.

A stored document is a *directory* under `originals/`, not a cluster of
files sharing a `<doc_id>` prefix:

    originals/<doc_id>/
    ├── document.md        # always — the markdown the reader chunks
    ├── source.<ext>       # the original upload when it wasn't already
    │                        markdown (e.g. source.pdf). Absent for .md
    │                        uploads — there the document IS the source.
    ├── extraction.json    # Marker's sidecar metadata, when the doc came
    │                        from a converter. Absent otherwise.
    └── images/            # extracted images; document.md references them
                             by the relative path `images/<file>` so a
                             markdown renderer resolves them against the
                             reader page URL (served by GET /documents/
                             {id}/images/{path}).

Why a directory: deleting or moving a document is one `rm -rf` / `mv`;
the relationship between markdown and its assets is structural, not a
filename convention a third tool has to know; and a portable bundle is
one `tar -C originals <doc_id>` away with zero refactor.

The welcome doc is the one exception — it's seeded from the repo's
`data/welcome.md`, its `original_path` points there, and it never gets
a directory under `originals/`. Callers that touch the welcome doc's
storage must tolerate the absence (the delete/retry paths already do).

This module is pure path policy: it never touches the filesystem. The
arrivals layer creates the directories; this module only computes paths.
"""

from __future__ import annotations

from pathlib import Path

MARKDOWN_NAME = "document.md"
EXTRACTION_JSON_NAME = "extraction.json"
IMAGES_DIRNAME = "images"


def document_dir(originals_dir: Path, document_id: int) -> Path:
    """The directory holding everything for one document."""
    return originals_dir / str(document_id)


def markdown_path(originals_dir: Path, document_id: int) -> Path:
    """`originals/<doc_id>/document.md` — always present once ingested."""
    return document_dir(originals_dir, document_id) / MARKDOWN_NAME


def source_path(originals_dir: Path, document_id: int, suffix: str) -> Path:
    """`originals/<doc_id>/source<suffix>` — the original upload when it
    wasn't itself markdown. `suffix` includes the dot (e.g. ".pdf")."""
    return document_dir(originals_dir, document_id) / f"source{suffix}"


def extraction_json_path(originals_dir: Path, document_id: int) -> Path:
    """`originals/<doc_id>/extraction.json` — Marker's sidecar metadata."""
    return document_dir(originals_dir, document_id) / EXTRACTION_JSON_NAME


def images_dir(originals_dir: Path, document_id: int) -> Path:
    """`originals/<doc_id>/images/` — extracted images."""
    return document_dir(originals_dir, document_id) / IMAGES_DIRNAME


def asset_path(originals_dir: Path, document_id: int, rel: str) -> Path | None:
    """Resolve a request for `originals/<doc_id>/images/<rel>`, refusing
    anything that escapes the document's images directory.

    Returns the resolved path on success, or `None` when `rel` is empty,
    absolute, or contains a `..` segment that would climb out. The caller
    still has to check the path exists — this only guards traversal.
    """
    if not rel or rel.startswith("/"):
        return None
    base = images_dir(originals_dir, document_id).resolve()
    candidate = (base / rel).resolve()
    # `is_relative_to` is the cheapest correct containment check; a `..`
    # in `rel` that climbs above `base` makes the resolved path fail it.
    if not candidate.is_relative_to(base):
        return None
    return candidate
