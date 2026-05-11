"""Materialization — turn a ChunkPlan into deterministic chunk records.

Spec: AtomicChunkingPhase1.md §Materialization. Phase 1 requires
contiguous chunks: the chunk's text is the revision's source slice
between the first and last piece's offsets. Non-contiguous (joined)
chunks are a deferred concern.

Chunk and Section are the canonical shapes for the rest of the
codebase (web/state, web/view, store/documents). The legacy
`parsem.domain.chunking` module was deleted in claude-axx.1 — its
fields lived on through this superset: text_hash, line/column
spans, and piece_ordinals are the substrate-introduced extras.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from parsem.domain.atomic import AtomicPiece, PieceKind
from parsem.domain.strategies import ChunkingRuleset, ChunkPlan
from parsem.parse.markdown_parse import BlockType
from parsem.store.revisions import DocumentRevision


@dataclass(frozen=True)
class Chunk:
    """The unit of reveal in the reading economy. A contiguous source
    span produced by the substrate's materialize_chunks pipeline.

    The first seven fields are the legacy chunk surface — every reader
    template, projection, and persistence path reads them. The next
    five (text_hash, line/column spans) and piece_ordinals are
    substrate-introduced extras with neutral defaults so load-from-DB
    paths that only need the legacy surface (e.g.,
    `load_chunks_for_document` for the reader view) can construct a
    Chunk without re-querying chunk_pieces. Re-anchor / re-chunk paths
    use dedicated queries (`get_chunk_piece_hashes_for_document`)
    rather than reading piece_ordinals off this dataclass."""

    position: int
    source_offset_start: int
    source_offset_end: int
    text: str
    lead_token_type: BlockType
    lead_heading_level: int | None
    estimated_read_seconds: float
    text_hash: str = ""
    start_line: int = 0
    end_line: int = 0
    start_column: int = 0
    end_column: int = 0
    piece_ordinals: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class Section:
    """Heading-bounded grouping of chunks. Same shape as legacy Section."""

    heading_chunk_position: int | None
    heading_level: int | None
    start_chunk_position: int
    end_chunk_position: int


_PIECE_KIND_TO_BLOCK_TYPE: dict[PieceKind, BlockType] = {
    "heading": "heading",
    "sentence": "paragraph",
    "paragraph": "paragraph",
    "code_block": "code",
    "list_item": "list_item",
    "list_run": "list_item",
    "blockquote": "blockquote",
    "table": "table",
    "horizontal_rule": "horizontal_rule",
    "image": "image",
}


def materialize_chunks(
    plan: ChunkPlan,
    revision: DocumentRevision,
    pieces: list[AtomicPiece],
    rules: ChunkingRuleset,
) -> list[Chunk]:
    """Walk the plan, emit one `Chunk` per planned chunk.

    Each chunk's text is sliced directly from `revision.full_text` —
    never reconstructed from piece snapshots. That keeps source fidelity
    cheap to verify (`hash(slice) == chunk.text_hash`).
    """
    if not rules.materialization_rules.require_contiguous_chunks:
        raise NotImplementedError(
            "Phase 1 requires contiguous chunks; "
            "non-contiguous materialization is a later phase"
        )

    chunks: list[Chunk] = []
    for planned in plan.planned_chunks:
        ordered = sorted(
            (pieces[ord_] for ord_ in planned.piece_ordinals),
            key=lambda p: p.source_offset_start,
        )
        first = ordered[0]
        last = ordered[-1]
        start = first.source_offset_start
        end = last.source_offset_end
        text = revision.full_text[start:end]
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        end_anchor = end - 1 if end > start else start
        start_line, start_column = revision.line_index.line_column(start)
        end_line, end_column = revision.line_index.line_column(end_anchor)
        chunks.append(Chunk(
            position=len(chunks),
            source_offset_start=start,
            source_offset_end=end,
            text=text,
            text_hash=text_hash,
            lead_token_type=_PIECE_KIND_TO_BLOCK_TYPE[first.kind],
            lead_heading_level=first.heading_level,
            estimated_read_seconds=planned.estimated_read_seconds,
            start_line=start_line,
            end_line=end_line,
            start_column=start_column,
            end_column=end_column,
            piece_ordinals=list(planned.piece_ordinals),
        ))
    return chunks


def derive_sections(chunks: list[Chunk]) -> list[Section]:
    """Group chunks into heading-bounded sections.

    A heading chunk starts a new section. Chunks before the first heading
    form a prologue section (heading_chunk_position=None). Same algorithm
    as legacy `_derive_sections` so reader navigation stays identical.
    """
    if not chunks:
        return []

    sections: list[Section] = []
    section_start = 0
    section_heading_position: int | None = None
    section_heading_level: int | None = None

    for chunk in chunks:
        if chunk.lead_token_type != "heading":
            continue
        if chunk.position > section_start or section_heading_position is not None:
            sections.append(Section(
                heading_chunk_position=section_heading_position,
                heading_level=section_heading_level,
                start_chunk_position=section_start,
                end_chunk_position=chunk.position - 1,
            ))
            section_start = chunk.position
        section_heading_position = chunk.position
        section_heading_level = chunk.lead_heading_level

    sections.append(Section(
        heading_chunk_position=section_heading_position,
        heading_level=section_heading_level,
        start_chunk_position=section_start,
        end_chunk_position=chunks[-1].position,
    ))
    return sections
