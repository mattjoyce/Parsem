"""Tests for parsem.parse.markdown_parse. Spec: parsem-spec.md §11."""

from __future__ import annotations

from itertools import pairwise

from parsem.parse.markdown_parse import parse


def test_empty_string_returns_empty_list() -> None:
    assert parse("") == []


def test_single_h1_produces_one_heading_block_with_level_1() -> None:
    blocks = parse("# Hello world\n")
    assert len(blocks) == 1
    assert blocks[0].type == "heading"
    assert blocks[0].heading_level == 1


def test_heading_levels_h1_through_h6_are_recognised() -> None:
    src = "# A\n\n## B\n\n### C\n\n#### D\n\n##### E\n\n###### F\n"
    blocks = parse(src)
    assert [b.type for b in blocks] == ["heading"] * 6
    assert [b.heading_level for b in blocks] == [1, 2, 3, 4, 5, 6]


def test_paragraph_produces_paragraph_block() -> None:
    blocks = parse("This is a paragraph.\n")
    assert len(blocks) == 1
    assert blocks[0].type == "paragraph"
    assert blocks[0].heading_level is None


def test_fenced_code_block_is_one_code_block_regardless_of_length() -> None:
    src = "```python\nprint(1)\nprint(2)\nprint(3)\n```\n"
    blocks = parse(src)
    assert len(blocks) == 1
    assert blocks[0].type == "code"


def test_top_level_bullet_list_produces_one_list_item_per_entry() -> None:
    src = "- item one\n- item two\n- item three\n"
    blocks = parse(src)
    assert [b.type for b in blocks] == ["list_item", "list_item", "list_item"]


def test_blockquote_produces_one_blockquote_block_absorbing_inner_paragraph() -> None:
    src = "> A quoted line.\n> A second quoted line.\n"
    blocks = parse(src)
    assert len(blocks) == 1
    assert blocks[0].type == "blockquote"
    # The blockquote's offsets must cover the full source — proves the inner
    # paragraph wasn't emitted as a second overlapping block.
    assert blocks[0].source_offset_start == 0
    assert blocks[0].source_offset_end == len(src)


def test_pipe_table_produces_one_table_block_absorbing_its_rows() -> None:
    """GFM table rule is enabled (claude-l51): a pipe-table is ONE
    `table` block whose source slice covers all rows — not a paragraph,
    not one block per row."""
    src = "| col | data |\n| --- | ---- |\n| a | 1 |\n| b | 2 |\n"
    blocks = parse(src)
    assert len(blocks) == 1
    assert blocks[0].type == "table"
    assert src[blocks[0].source_offset_start : blocks[0].source_offset_end] == blocks[0].text


def test_empty_heading_produces_a_heading_block() -> None:
    blocks = parse("#\n")
    assert len(blocks) == 1
    assert blocks[0].type == "heading"
    assert blocks[0].heading_level == 1


def test_offsets_let_caller_reconstruct_each_block_text() -> None:
    src = "# Title\n\nA paragraph.\n\n- item one\n- item two\n"
    blocks = parse(src)
    for block in blocks:
        assert src[block.source_offset_start : block.source_offset_end] == block.text


def test_mixed_document_has_blocks_in_source_order() -> None:
    src = "# Title\n\nIntro.\n\n## Sub\n\nMore.\n"
    blocks = parse(src)
    assert [b.type for b in blocks] == ["heading", "paragraph", "heading", "paragraph"]
    for previous, current in pairwise(blocks):
        assert previous.source_offset_end <= current.source_offset_start


# --- block-level images (claude-axx.6) --------------------------------------


def test_standalone_image_paragraph_is_an_image_block() -> None:
    blocks = parse("![a diagram](pic.png)\n")
    assert len(blocks) == 1
    assert blocks[0].type == "image"


def test_prose_with_inline_image_stays_a_paragraph() -> None:
    blocks = parse("See ![this](pic.png) for details.\n")
    assert len(blocks) == 1
    assert blocks[0].type == "paragraph"


def test_consecutive_image_paragraphs_each_become_their_own_block() -> None:
    blocks = parse("![one](a.png)\n\n![two](b.png)\n\n![three](c.png)\n")
    assert [b.type for b in blocks] == ["image", "image", "image"]


def test_image_block_offsets_reconstruct_text() -> None:
    src = "# Title\n\n![fig](f.png)\n\nProse after.\n"
    blocks = parse(src)
    assert [b.type for b in blocks] == ["heading", "image", "paragraph"]
    for block in blocks:
        assert src[block.source_offset_start : block.source_offset_end] == block.text


def test_two_images_in_one_paragraph_stays_a_paragraph() -> None:
    # Two images separated only by a space share one inline run — not the
    # standalone form, so it remains prose. (Separate paragraphs split.)
    blocks = parse("![a](a.png) ![b](b.png)\n")
    assert len(blocks) == 1
    assert blocks[0].type == "paragraph"
