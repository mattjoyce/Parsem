"""Markdown parsing into a flat list of block tokens with source offsets.

Spec: parsem-spec.md §11. Adapts markdown-it-py output into a stable
domain-friendly shape so the chunker can stream block tokens with
character offsets back into the source markdown.

Block types per spec §11.3: heading | paragraph | list_item | code |
blockquote | table | horizontal_rule | image.

`image` is the block-level case (claude-axx.6): a paragraph whose only
content is `![alt](url)`. Inline images inside prose stay part of their
containing paragraph and render normally; only the standalone form is
reclassified so the chunker can give it its own reveal unit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token

BlockType = Literal[
    "heading",
    "paragraph",
    "list_item",
    "code",
    "blockquote",
    "table",
    "horizontal_rule",
    "image",
]


@dataclass(frozen=True)
class ParsedBlock:
    """A block-level markdown token with character offsets into the source."""

    type: BlockType
    heading_level: int | None
    text: str
    source_offset_start: int
    source_offset_end: int


# Map markdown-it-py token types to our BlockType. The "open" suffix
# matches container tokens (heading_open ... heading_close); leaf tokens
# like fence and hr have no close pair and are mapped directly.
_OPEN_TOKEN_TYPES: dict[str, BlockType] = {
    "heading_open": "heading",
    "paragraph_open": "paragraph",
    "list_item_open": "list_item",
    "fence": "code",
    "code_block": "code",
    "blockquote_open": "blockquote",
    "table_open": "table",
    "hr": "horizontal_rule",
}

# Block types that "absorb" their inner content — children are not emitted
# as separate blocks because the outer block's source slice already covers them.
_ABSORBING_TYPES: frozenset[BlockType] = frozenset({"list_item", "blockquote", "table"})

_ABSORBING_CLOSE_TYPES: frozenset[str] = frozenset(
    {"list_item_close", "blockquote_close", "table_close"}
)

_PARSER = MarkdownIt("commonmark")


def parse(markdown_text: str) -> list[ParsedBlock]:
    """Parse markdown into a flat list of block-level tokens."""
    if not markdown_text:
        return []

    line_starts = _line_start_offsets(markdown_text)
    tokens = _PARSER.parse(markdown_text)

    blocks: list[ParsedBlock] = []
    absorbing_depth = 0

    for i, token in enumerate(tokens):
        block_type = _OPEN_TOKEN_TYPES.get(token.type)
        if block_type is None:
            if token.type in _ABSORBING_CLOSE_TYPES:
                absorbing_depth -= 1
            continue

        if absorbing_depth == 0 and token.map is not None:
            # A paragraph whose sole content is one image becomes a
            # block-level image (claude-axx.6) — its own reveal unit
            # with image-shaped read cost. Prose containing an inline
            # image stays a paragraph.
            if block_type == "paragraph" and _is_image_only_paragraph(tokens, i):
                block_type = "image"
            blocks.append(_token_to_block(token, block_type, line_starts, markdown_text))

        if block_type in _ABSORBING_TYPES:
            absorbing_depth += 1

    return blocks


# Inline child token types that carry no visible text — stripped when
# deciding whether a paragraph is "just an image".
_WHITESPACE_INLINE_TYPES: frozenset[str] = frozenset({"softbreak", "hardbreak"})


def _is_image_only_paragraph(tokens: list[Token], paragraph_open_index: int) -> bool:
    """True when the paragraph at `paragraph_open_index` contains exactly
    one image token and nothing else (whitespace text/breaks ignored).

    markdown-it emits `paragraph_open`, then an `inline` token whose
    `children` hold the rendered content. `![alt](url)` alone produces
    `children == [image]`; `see ![x](u) here` produces
    `[text, image, text]`, which stays prose.
    """
    if paragraph_open_index + 1 >= len(tokens):
        return False
    inline = tokens[paragraph_open_index + 1]
    if inline.type != "inline" or not inline.children:
        return False
    significant = []
    for child in inline.children:
        if child.type in _WHITESPACE_INLINE_TYPES:
            continue
        if child.type == "text" and not child.content.strip():
            continue
        significant.append(child)
    return len(significant) == 1 and significant[0].type == "image"


def _line_start_offsets(text: str) -> list[int]:
    """Character offset of the start of each line, plus a sentinel at end-of-text."""
    offsets = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            offsets.append(i + 1)
    offsets.append(len(text))
    return offsets


def _token_to_block(
    token: Token,
    block_type: BlockType,
    line_starts: list[int],
    source: str,
) -> ParsedBlock:
    assert token.map is not None
    start_line, end_line = token.map
    start = line_starts[start_line]
    end = line_starts[min(end_line, len(line_starts) - 1)]
    heading_level = int(token.tag[1:]) if block_type == "heading" else None
    return ParsedBlock(
        type=block_type,
        heading_level=heading_level,
        text=source[start:end],
        source_offset_start=start,
        source_offset_end=end,
    )
