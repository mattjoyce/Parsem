"""Tests for parsem.domain.chunking. Spec: parsem-spec.md §10, §11."""

from __future__ import annotations

import pytest

from parsem.domain.chunking import ChunkingConfig, chunk
from parsem.parse.markdown_parse import ParsedBlock


def _block(
    block_type: str,
    text: str,
    *,
    heading_level: int | None = None,
    start: int = 0,
) -> ParsedBlock:
    """Build a ParsedBlock fixture with derived offsets."""
    return ParsedBlock(
        type=block_type,  # type: ignore[arg-type]
        heading_level=heading_level,
        text=text,
        source_offset_start=start,
        source_offset_end=start + len(text),
    )


def test_empty_blocks_returns_empty_chunks_and_sections() -> None:
    result = chunk([], ChunkingConfig())
    assert result.chunks == []
    assert result.sections == []


def test_single_short_paragraph_produces_one_chunk_in_prologue_section() -> None:
    block = _block("paragraph", "A single short sentence.")
    result = chunk([block], ChunkingConfig())
    assert len(result.chunks) == 1
    assert result.chunks[0].lead_token_type == "paragraph"
    assert result.chunks[0].position == 0
    assert len(result.sections) == 1
    assert result.sections[0].heading_chunk_position is None
    assert result.sections[0].start_chunk_position == 0
    assert result.sections[0].end_chunk_position == 0


def test_single_heading_produces_one_heading_chunk() -> None:
    block = _block("heading", "# Title", heading_level=1)
    result = chunk([block], ChunkingConfig())
    assert len(result.chunks) == 1
    assert result.chunks[0].lead_token_type == "heading"
    assert result.chunks[0].lead_heading_level == 1


def test_single_code_block_is_one_chunk_regardless_of_length() -> None:
    long_code = "```python\n" + "\n".join(f"line_{i} = {i}" for i in range(200)) + "\n```"
    block = _block("code", long_code)
    result = chunk([block], ChunkingConfig())
    assert len(result.chunks) == 1
    assert result.chunks[0].lead_token_type == "code"


def test_each_list_item_produces_one_chunk_when_handling_is_item() -> None:
    """list_handling='item' (legacy explicit) preserves per-item chunks."""
    blocks = [_block("list_item", f"- Item {i}", start=i * 20) for i in range(3)]
    result = chunk(blocks, ChunkingConfig(list_handling="item"))
    assert len(result.chunks) == 3
    assert all(c.lead_token_type == "list_item" for c in result.chunks)


def test_consecutive_list_items_merge_into_one_chunk_under_block_default() -> None:
    """list_handling='block' is the default — three consecutive items
    become one chunk with all three texts joined."""
    blocks = [_block("list_item", f"- Item {i}\n", start=i * 20) for i in range(3)]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 1
    assert result.chunks[0].lead_token_type == "list_item"
    assert "Item 0" in result.chunks[0].text
    assert "Item 1" in result.chunks[0].text
    assert "Item 2" in result.chunks[0].text


def test_merged_list_chunk_spans_first_item_start_to_last_item_end() -> None:
    blocks = [
        _block("list_item", "- A\n", start=0),
        _block("list_item", "- B\n", start=10),
        _block("list_item", "- C\n", start=20),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 1
    assert result.chunks[0].source_offset_start == 0
    assert result.chunks[0].source_offset_end == blocks[-1].source_offset_end


def test_non_consecutive_list_items_form_separate_chunks() -> None:
    """A paragraph between two lists splits them into two list-chunks."""
    blocks = [
        _block("list_item", "- A\n", start=0),
        _block("list_item", "- B\n", start=10),
        _block("paragraph", "Between.", start=20),
        _block("list_item", "- C\n", start=40),
        _block("list_item", "- D\n", start=50),
    ]
    result = chunk(blocks, ChunkingConfig())
    list_chunks = [c for c in result.chunks if c.lead_token_type == "list_item"]
    assert len(list_chunks) == 2


def test_single_item_list_under_block_handling_produces_one_chunk() -> None:
    blocks = [_block("list_item", "- only\n", start=0)]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 1
    assert result.chunks[0].lead_token_type == "list_item"


# Parsem-5lx — colon-terminated lead-in absorption.


def test_paragraph_ending_in_colon_is_absorbed_into_following_list() -> None:
    blocks = [
        _block("paragraph", "This list:", start=0),
        _block("list_item", "- a\n", start=11),
        _block("list_item", "- b\n", start=15),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 1
    assert result.chunks[0].lead_token_type == "list_item"
    assert "This list:" in result.chunks[0].text
    assert "- a" in result.chunks[0].text
    assert "- b" in result.chunks[0].text


def test_absorbed_lead_in_chunk_starts_at_paragraphs_source_offset() -> None:
    """The combined chunk's source_offset_start must come from the
    lead-in (earlier in the source), not the first list item."""
    blocks = [
        _block("paragraph", "Lead:", start=5),
        _block("list_item", "- x\n", start=20),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 1
    assert result.chunks[0].source_offset_start == 5


def test_paragraph_without_trailing_colon_is_not_absorbed() -> None:
    """The previous chunk stays standalone; only ':' triggers absorption."""
    blocks = [
        _block("paragraph", "Just a paragraph.", start=0),
        _block("list_item", "- a\n", start=20),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 2
    assert result.chunks[0].lead_token_type == "paragraph"
    assert result.chunks[1].lead_token_type == "list_item"


def test_mid_sentence_colon_does_not_trigger_absorption() -> None:
    """The colon must be the LAST non-whitespace character. A 'Note:
    something' paragraph ends with 'something', not ':'."""
    blocks = [
        _block("paragraph", "Note: something then more text.", start=0),
        _block("list_item", "- a\n", start=40),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 2
    assert result.chunks[0].text == "Note: something then more text."


def test_trailing_whitespace_after_colon_still_triggers_absorption() -> None:
    blocks = [
        _block("paragraph", "Items:   \n", start=0),
        _block("list_item", "- a\n", start=20),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 1
    assert "Items:" in result.chunks[0].text


def test_absorption_does_not_reach_two_chunks_back() -> None:
    """If a non-colon paragraph (or anything else) sits between the
    colon-paragraph and the list, the lead-in absorption must skip."""
    blocks = [
        _block("paragraph", "Earlier:", start=0),
        _block("paragraph", "Distractor sentence.", start=10),
        _block("list_item", "- a\n", start=40),
    ]
    result = chunk(blocks, ChunkingConfig())
    # 'Earlier:' is two-back relative to the list — must NOT be absorbed.
    earlier = next(c for c in result.chunks if "Earlier:" in c.text)
    assert earlier.lead_token_type == "paragraph"


def test_heading_immediately_before_list_is_not_treated_as_lead_in() -> None:
    """A heading (even if its text ends with ':') is not a paragraph
    chunk; the existing heading-absorption rule covers headings."""
    blocks = [
        _block("heading", "## Things:", heading_level=2, start=0),
        _block("list_item", "- a\n", start=20),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert any(c.lead_token_type == "heading" for c in result.chunks)
    assert any(c.lead_token_type == "list_item" for c in result.chunks)


def test_absorption_disabled_by_config_flag() -> None:
    blocks = [
        _block("paragraph", "Items:", start=0),
        _block("list_item", "- a\n", start=10),
    ]
    result = chunk(blocks, ChunkingConfig(absorb_colon_lead_in=False))
    assert len(result.chunks) == 2
    assert result.chunks[0].lead_token_type == "paragraph"


def test_absorbed_chunks_read_seconds_equals_sum_of_originals() -> None:
    """Word counts are additive across the lead-in and list, so the
    combined chunk's estimated_read_seconds must equal the sum of the
    two would-be standalone chunks."""
    para = _block("paragraph", "Reasons to keep reading:", start=0)
    items = [
        _block("list_item", "- one good reason\n", start=30),
        _block("list_item", "- another reason here\n", start=50),
    ]
    config = ChunkingConfig()
    combined_result = chunk([para, *items], config)
    standalone_result = chunk(
        [para, *items], ChunkingConfig(absorb_colon_lead_in=False)
    )
    assert len(combined_result.chunks) == 1
    assert len(standalone_result.chunks) == 2
    expected = sum(c.estimated_read_seconds for c in standalone_result.chunks)
    assert combined_result.chunks[0].estimated_read_seconds == pytest.approx(expected)


def test_absorption_works_for_ordered_lists_too() -> None:
    """Ordered list items use the same `list_item` block type — the
    rule applies regardless of bullet vs ordered."""
    blocks = [
        _block("paragraph", "Steps:", start=0),
        _block("list_item", "1. first\n", start=10),
        _block("list_item", "2. second\n", start=20),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 1
    assert "Steps:" in result.chunks[0].text
    assert "1. first" in result.chunks[0].text
    assert "2. second" in result.chunks[0].text


def test_blockquote_is_one_chunk_regardless_of_length() -> None:
    block = _block("blockquote", "> A quoted line.\n> A second line.\n> A third line.")
    result = chunk([block], ChunkingConfig())
    assert len(result.chunks) == 1
    assert result.chunks[0].lead_token_type == "blockquote"


def test_paragraph_with_sentences_within_budget_packs_into_one_chunk() -> None:
    block = _block("paragraph", "First sentence. Second sentence. Third sentence.")
    result = chunk([block], ChunkingConfig())
    assert len(result.chunks) == 1


def test_paragraph_overflowing_budget_produces_multiple_chunks() -> None:
    # 20 sentences of ~13 words each, ~260 total. At budget 10s and 220 wpm
    # we get ~36 words per chunk, so expect roughly 7 chunks.
    sentences = [
        f"This is sentence number {i} with several extra words here padded a bit."
        for i in range(20)
    ]
    block = _block("paragraph", " ".join(sentences))
    result = chunk([block], ChunkingConfig())
    assert len(result.chunks) >= 2
    for c in result.chunks:
        assert c.lead_token_type == "paragraph"


def test_sentence_longer_than_budget_is_emitted_solo() -> None:
    # One enormous "sentence" without internal periods → must become one chunk regardless.
    huge = " ".join(f"word{i}" for i in range(500)) + "."  # ~500 words → ~136s at 220 wpm
    block = _block("paragraph", huge)
    result = chunk([block], ChunkingConfig())
    assert len(result.chunks) == 1
    assert result.chunks[0].lead_token_type == "paragraph"


def test_lead_heading_level_is_none_for_non_heading_chunks() -> None:
    blocks = [
        _block("paragraph", "Plain text."),
        _block("code", "code block"),
        _block("blockquote", "> quote"),
        _block("list_item", "- item"),
    ]
    result = chunk(blocks, ChunkingConfig())
    for c in result.chunks:
        assert c.lead_heading_level is None


def test_heading_followed_by_short_paragraph_absorbs_into_one_chunk() -> None:
    blocks = [
        _block("heading", "# Title", heading_level=1, start=0),
        _block("paragraph", "A brief intro sentence.", start=10),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 1
    assert result.chunks[0].lead_token_type == "heading"
    assert result.chunks[0].lead_heading_level == 1
    # The chunk's text spans from heading to absorbed paragraph
    assert "Title" in result.chunks[0].text
    assert "intro" in result.chunks[0].text


def test_heading_followed_by_long_paragraph_absorbs_prefix_and_emits_remainder() -> None:
    long_para = " ".join(
        f"This is paragraph sentence number {i} padded with several extra words." for i in range(20)
    )
    blocks = [
        _block("heading", "# Title", heading_level=1, start=0),
        _block("paragraph", long_para, start=10),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) >= 2
    # First chunk is the heading-with-absorbed-prefix.
    assert result.chunks[0].lead_token_type == "heading"
    # Subsequent chunks are paragraph chunks (the unabsorbed sentences).
    for follow in result.chunks[1:]:
        assert follow.lead_token_type == "paragraph"


def test_heading_immediately_followed_by_another_heading_is_heading_only() -> None:
    blocks = [
        _block("heading", "# Top", heading_level=1, start=0),
        _block("heading", "## Sub", heading_level=2, start=10),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 2
    assert result.chunks[0].lead_token_type == "heading"
    assert result.chunks[0].lead_heading_level == 1
    assert "Sub" not in result.chunks[0].text
    assert result.chunks[1].lead_token_type == "heading"
    assert result.chunks[1].lead_heading_level == 2


def test_heading_at_end_of_document_is_heading_only() -> None:
    blocks = [
        _block("paragraph", "Some intro.", start=0),
        _block("heading", "# Final", heading_level=1, start=20),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert result.chunks[-1].lead_token_type == "heading"
    assert "Final" in result.chunks[-1].text


def test_heading_followed_by_code_does_not_absorb_code() -> None:
    blocks = [
        _block("heading", "# Title", heading_level=1, start=0),
        _block("code", "```\nlots of code\n```", start=10),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 2
    assert result.chunks[0].lead_token_type == "heading"
    assert "code" not in result.chunks[0].text
    assert result.chunks[1].lead_token_type == "code"


def test_heading_followed_by_list_item_does_not_absorb_list() -> None:
    blocks = [
        _block("heading", "# Title", heading_level=1, start=0),
        _block("list_item", "- first item", start=10),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.chunks) == 2
    assert result.chunks[0].lead_token_type == "heading"
    assert result.chunks[1].lead_token_type == "list_item"


def test_document_with_no_heading_has_one_prologue_section() -> None:
    blocks = [
        _block("paragraph", "First.", start=0),
        _block("paragraph", "Second.", start=20),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.sections) == 1
    assert result.sections[0].heading_chunk_position is None
    assert result.sections[0].start_chunk_position == 0
    assert result.sections[0].end_chunk_position == len(result.chunks) - 1


def test_single_heading_starts_one_section_at_that_chunk() -> None:
    blocks = [
        _block("heading", "# Title", heading_level=1),
        _block("paragraph", "Body sentence.", start=20),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.sections) == 1
    assert result.sections[0].heading_chunk_position == 0
    assert result.sections[0].heading_level == 1


def test_two_headings_produce_two_sections() -> None:
    blocks = [
        _block("heading", "# One", heading_level=1, start=0),
        _block("paragraph", "Para A.", start=10),
        _block("heading", "# Two", heading_level=1, start=20),
        _block("paragraph", "Para B.", start=30),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.sections) == 2
    assert result.sections[0].heading_chunk_position == 0
    assert result.sections[1].heading_chunk_position == result.sections[0].end_chunk_position + 1


def test_content_before_first_heading_forms_a_prologue_section() -> None:
    blocks = [
        _block("paragraph", "Intro before any heading.", start=0),
        _block("heading", "# First Section", heading_level=1, start=30),
        _block("paragraph", "Body.", start=50),
    ]
    result = chunk(blocks, ChunkingConfig())
    assert len(result.sections) == 2
    assert result.sections[0].heading_chunk_position is None
    assert result.sections[1].heading_chunk_position is not None


def test_prose_chunk_uses_prose_wpm_for_read_time() -> None:
    config = ChunkingConfig(read_wpm_prose=120, read_wpm_code=600)
    block = _block("paragraph", "Ten words here exactly without any punctuation marks at all.")
    result = chunk([block], config)
    # 10 words at 120 wpm = 5 seconds
    assert result.chunks[0].estimated_read_seconds == 5.0


def test_code_chunk_uses_code_wpm_for_read_time() -> None:
    config = ChunkingConfig(read_wpm_prose=600, read_wpm_code=120)
    block = _block("code", "Ten words here exactly without any punctuation marks at all.")
    result = chunk([block], config)
    # 10 words at 120 wpm = 5 seconds
    assert result.chunks[0].estimated_read_seconds == 5.0


def test_user_scaling_doubles_or_halves_read_time() -> None:
    block = _block("paragraph", "One two three four five six seven eight nine ten.")
    base = chunk([block], ChunkingConfig(wpm_user_scaling=1.0))
    half = chunk([block], ChunkingConfig(wpm_user_scaling=2.0))
    assert half.chunks[0].estimated_read_seconds == base.chunks[0].estimated_read_seconds / 2


def test_absorbed_heading_chunk_offsets_span_heading_to_paragraph_prefix() -> None:
    heading = _block("heading", "# Title", heading_level=1, start=0)
    paragraph = _block("paragraph", "Brief intro sentence.", start=10)
    result = chunk([heading, paragraph], ChunkingConfig())
    assert len(result.chunks) == 1
    absorbed_chunk = result.chunks[0]
    # Source-offset span runs from start of heading to end of absorbed prefix.
    assert absorbed_chunk.source_offset_start == heading.source_offset_start
    assert absorbed_chunk.source_offset_end == paragraph.source_offset_end


@pytest.mark.parametrize(
    "config",
    [
        ChunkingConfig(code_handling="prose"),
        ChunkingConfig(list_handling="prose"),
    ],
    ids=["code_handling=prose", "list_handling=prose"],
)
def test_unsupported_handling_mode_raises_not_implemented(
    config: ChunkingConfig,
) -> None:
    block = _block("paragraph", "Anything.")
    try:
        chunk([block], config)
    except NotImplementedError:
        return
    raise AssertionError("expected NotImplementedError")
