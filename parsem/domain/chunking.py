"""Pure block-to-chunk transformation. Spec: parsem-spec.md §10, §11.

Takes a list of parsed Markdown blocks and produces:
  - a list of Chunks (the unit of reveal in the reading economy)
  - a list of Sections (heading-bounded groupings)

The chunker is a pure function: same inputs always produce the same
outputs. No IO, no clock reads, no global state.

Spec rules (§11) summary:
  - Sentence-aware paragraph packing into a `budget_seconds` envelope;
    never split a sentence.
  - Heading absorption: a heading absorbs forward sentences from the
    following paragraph (only) up to the budget OR until the next
    heading hits.
  - Code, blockquote, list_item, table: each is one chunk regardless
    of length.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from parsem.parse.markdown_parse import BlockType, ParsedBlock
from parsem.parse.sentence import Sentence, split_sentences


@dataclass(frozen=True)
class ChunkingConfig:
    """Chunking knobs. Defaults match spec §20."""

    budget_seconds: int = 10
    read_wpm_prose: int = 220
    read_wpm_code: int = 110
    wpm_user_scaling: float = 1.0
    code_handling: Literal["block", "prose"] = "block"
    list_handling: Literal["item", "block", "prose"] = "item"


@dataclass(frozen=True)
class Chunk:
    """A chunk — the atomic unit of reveal in the reader."""

    position: int
    source_offset_start: int
    source_offset_end: int
    text: str
    lead_token_type: BlockType
    lead_heading_level: int | None
    estimated_read_seconds: float


@dataclass(frozen=True)
class Section:
    """A heading-bounded grouping of chunks. heading_chunk_position is None
    for the prologue (content before the first heading)."""

    heading_chunk_position: int | None
    heading_level: int | None
    start_chunk_position: int
    end_chunk_position: int


@dataclass(frozen=True)
class ChunkerOutput:
    """The chunker's return value: chunks plus their section grouping."""

    chunks: list[Chunk]
    sections: list[Section]


_HEADING_BODY_SEPARATOR = "\n\n"


def chunk(blocks: list[ParsedBlock], config: ChunkingConfig) -> ChunkerOutput:
    """Transform parsed blocks into chunks and sections."""
    if config.code_handling != "block" or config.list_handling != "item":
        raise NotImplementedError(
            "Phase 1 supports only code_handling='block' and list_handling='item'. "
            "Other modes are spec'd in §11.3 but unimplemented."
        )

    chunks: list[Chunk] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        next_block = blocks[i + 1] if i + 1 < len(blocks) else None
        if block.type == "heading":
            heading_chunks, consumed_next = _absorb_heading(
                block, next_block, config, len(chunks)
            )
            chunks.extend(heading_chunks)
            i += 2 if consumed_next else 1
        elif block.type == "paragraph":
            chunks.extend(_pack_paragraph(block, config, len(chunks)))
            i += 1
        else:
            chunks.append(_solo_chunk(block, config, len(chunks)))
            i += 1
    sections = _derive_sections(chunks)
    return ChunkerOutput(chunks=chunks, sections=sections)


def _solo_chunk(block: ParsedBlock, config: ChunkingConfig, position: int) -> Chunk:
    """One block, one chunk — for headings and structural blocks."""
    return Chunk(
        position=position,
        source_offset_start=block.source_offset_start,
        source_offset_end=block.source_offset_end,
        text=block.text,
        lead_token_type=block.type,
        lead_heading_level=block.heading_level,
        estimated_read_seconds=_read_seconds(block.text, block.type, config),
    )


def _absorb_heading(
    heading: ParsedBlock,
    next_block: ParsedBlock | None,
    config: ChunkingConfig,
    position: int,
) -> tuple[list[Chunk], bool]:
    """Emit a heading chunk, absorbing forward sentences from the next paragraph
    (only) up to the budget. Returns (chunks, consumed_next_block)."""
    if next_block is None or next_block.type != "paragraph":
        return [_solo_chunk(heading, config, position)], False

    sentences = split_sentences(next_block.text)
    if not sentences:
        return [_solo_chunk(heading, config, position)], True

    heading_seconds = _read_seconds(heading.text, "heading", config)
    absorbed: list[Sentence] = []
    bucket_seconds = heading_seconds
    for sentence in sentences:
        sentence_seconds = _read_seconds(sentence.text, "paragraph", config)
        if bucket_seconds + sentence_seconds > config.budget_seconds:
            break
        absorbed.append(sentence)
        bucket_seconds += sentence_seconds

    if not absorbed:
        # Heading alone exhausts the budget. Emit heading-only and DON'T
        # consume the paragraph — it chunks normally next iteration.
        # (Asymmetric with the empty-sentences case above, which DID consume:
        #  empty paragraph has nothing to recover; this paragraph still has
        #  all its sentences pending.)
        return [_solo_chunk(heading, config, position)], False

    chunks: list[Chunk] = [
        _heading_with_absorbed_chunk(heading, next_block, absorbed, bucket_seconds, position)
    ]
    remaining = sentences[len(absorbed) :]
    chunks.extend(_pack_sentences(remaining, next_block, config, position + len(chunks)))
    return chunks, True


def _heading_with_absorbed_chunk(
    heading: ParsedBlock,
    paragraph: ParsedBlock,
    absorbed: list[Sentence],
    total_seconds: float,
    position: int,
) -> Chunk:
    """Build a heading chunk whose body absorbs leading paragraph sentences.

    Note: the chunk's `text` length intentionally exceeds the source-offset
    span by len(_HEADING_BODY_SEPARATOR), because the rendered text injects
    a separator between heading and absorbed body that does not exist as a
    contiguous slice in the source. Re-anchoring at chunker re-runs uses
    source-offset overlap (spec §11.6), not text equality, so this is fine.
    """
    abs_end = absorbed[-1].char_end
    absorbed_body = paragraph.text[absorbed[0].char_start : abs_end]
    combined = heading.text + _HEADING_BODY_SEPARATOR + absorbed_body
    return Chunk(
        position=position,
        source_offset_start=heading.source_offset_start,
        source_offset_end=paragraph.source_offset_start + abs_end,
        text=combined,
        lead_token_type="heading",
        lead_heading_level=heading.heading_level,
        estimated_read_seconds=total_seconds,
    )


def _pack_paragraph(
    block: ParsedBlock,
    config: ChunkingConfig,
    position_offset: int,
) -> list[Chunk]:
    """Greedily pack whole sentences from a paragraph into budget-sized chunks."""
    return _pack_sentences(split_sentences(block.text), block, config, position_offset)


def _pack_sentences(
    sentences: list[Sentence],
    source_block: ParsedBlock,
    config: ChunkingConfig,
    position_offset: int,
) -> list[Chunk]:
    """Pack sentences from a single source block into budget-sized chunks."""
    if not sentences:
        return []

    chunks: list[Chunk] = []
    bucket: list[Sentence] = []
    bucket_seconds = 0.0

    for sentence in sentences:
        sentence_seconds = _read_seconds(sentence.text, "paragraph", config)
        if bucket and bucket_seconds + sentence_seconds > config.budget_seconds:
            chunks.append(
                _paragraph_chunk(bucket, source_block, config, position_offset + len(chunks))
            )
            bucket = []
            bucket_seconds = 0.0
        bucket.append(sentence)
        bucket_seconds += sentence_seconds

    if bucket:
        chunks.append(_paragraph_chunk(bucket, source_block, config, position_offset + len(chunks)))
    return chunks


def _paragraph_chunk(
    bucket: list[Sentence],
    block: ParsedBlock,
    config: ChunkingConfig,
    position: int,
) -> Chunk:
    """Build a paragraph Chunk from a bucket of sentences within one block."""
    char_start = bucket[0].char_start
    char_end = bucket[-1].char_end
    text = block.text[char_start:char_end]
    return Chunk(
        position=position,
        source_offset_start=block.source_offset_start + char_start,
        source_offset_end=block.source_offset_start + char_end,
        text=text,
        lead_token_type="paragraph",
        lead_heading_level=None,
        estimated_read_seconds=_read_seconds(text, "paragraph", config),
    )


def _read_seconds(text: str, block_type: BlockType, config: ChunkingConfig) -> float:
    """Estimated reading time per spec §11.4."""
    word_count = len(text.split())
    wpm = config.read_wpm_code if block_type == "code" else config.read_wpm_prose
    effective_wpm = wpm * config.wpm_user_scaling
    if effective_wpm <= 0:
        return 0.0
    return word_count / effective_wpm * 60


def _derive_sections(chunks: list[Chunk]) -> list[Section]:
    """Group chunks into heading-bounded sections.

    A heading chunk starts a new section. Chunks before the first heading
    form a prologue section (heading_chunk_position=None).
    """
    if not chunks:
        return []

    sections: list[Section] = []
    section_start = 0
    section_heading_position: int | None = None
    section_heading_level: int | None = None

    for chunk_obj in chunks:
        if chunk_obj.lead_token_type != "heading":
            continue
        if chunk_obj.position > section_start or section_heading_position is not None:
            sections.append(
                Section(
                    heading_chunk_position=section_heading_position,
                    heading_level=section_heading_level,
                    start_chunk_position=section_start,
                    end_chunk_position=chunk_obj.position - 1,
                )
            )
            section_start = chunk_obj.position
        section_heading_position = chunk_obj.position
        section_heading_level = chunk_obj.lead_heading_level

    sections.append(
        Section(
            heading_chunk_position=section_heading_position,
            heading_level=section_heading_level,
            start_chunk_position=section_start,
            end_chunk_position=chunks[-1].position,
        )
    )
    return sections
