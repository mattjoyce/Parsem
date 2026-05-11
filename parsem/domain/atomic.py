"""Atomic pieces — the smallest legal source-faithful unit a deterministic
chunking strategy may place into a chunk.

Spec: AtomicChunkingPhase1.md §AtomicPiece. A piece is fully derived from
a `DocumentRevision` plus an `AtomicRules`; same inputs produce identical
pieces (byte-for-byte). The piece carries source offsets, a hash of its
slice for validation, and a snapshot for debug/test convenience.

`structural_parent_ordinal` is an in-memory backreference (e.g., a sentence
to its paragraph piece, if paragraph parent tracking is enabled). It maps
to `structural_parent_piece_id` after persist. Phase 1 leaves it None for
all pieces — paragraphs are atomized at sentence grain by default and we
don't carry a parent paragraph piece alongside them. The hook is here so
later strategies can opt in without a schema change.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

from parsem.parse.line_index import LineIndex
from parsem.parse.markdown_parse import ParsedBlock
from parsem.parse.sentence import split_sentences

PieceKind = Literal[
    "heading",
    "sentence",
    "paragraph",
    "code_block",
    "list_item",
    "list_run",
    "blockquote",
    "table",
    "horizontal_rule",
    "image",
]


@dataclass(frozen=True)
class AtomicRules:
    """Atomicity decisions made before planning begins.

    Defaults match Phase 1 product direction: sentence-grain prose so
    reading-time packing has whole-sentence units to work with; whole-block
    code/table/blockquote so they never split; list_run so a list reads
    as one unit instead of one reveal per item.
    """

    paragraph_atomicity: Literal["sentence", "paragraph"] = "sentence"
    code_atomicity: Literal["block"] = "block"
    table_atomicity: Literal["block"] = "block"
    blockquote_atomicity: Literal["block"] = "block"
    list_atomicity: Literal["item", "run"] = "run"


@dataclass(frozen=True)
class AtomicPiece:
    """A source-faithful unit. `id` is None pre-persist; the planner and
    materializer reference pieces by `ordinal` until they hit the DB."""

    ordinal: int
    kind: PieceKind
    source_block_index: int
    ordinal_in_block: int
    source_offset_start: int
    source_offset_end: int
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    text_hash: str
    text_snapshot: str
    heading_level: int | None = None
    structural_parent_ordinal: int | None = None
    id: int | None = field(default=None, compare=False)


def build_atomic_pieces(
    blocks: list[ParsedBlock],
    rules: AtomicRules,
    full_text: str,
    line_index: LineIndex,
) -> list[AtomicPiece]:
    """Produce the deterministic atomic-piece sequence for a revision.

    Pieces are emitted in document order. The function is pure: same
    `(blocks, rules, full_text)` always produces identical pieces.
    """
    pieces: list[AtomicPiece] = []
    block_index = 0
    while block_index < len(blocks):
        block = blocks[block_index]
        if block.type == "heading":
            pieces.append(_make_block_piece(
                block, kind="heading", ordinal=len(pieces),
                source_block_index=block_index, ordinal_in_block=0,
                line_index=line_index,
            ))
            block_index += 1
        elif block.type == "paragraph":
            if rules.paragraph_atomicity == "sentence":
                _append_sentence_pieces(
                    pieces, block, block_index, line_index,
                )
            else:
                pieces.append(_make_block_piece(
                    block, kind="paragraph", ordinal=len(pieces),
                    source_block_index=block_index, ordinal_in_block=0,
                    line_index=line_index,
                ))
            block_index += 1
        elif block.type == "code":
            pieces.append(_make_block_piece(
                block, kind="code_block", ordinal=len(pieces),
                source_block_index=block_index, ordinal_in_block=0,
                line_index=line_index,
            ))
            block_index += 1
        elif block.type == "list_item":
            block_index = _append_list_pieces(
                pieces, blocks, block_index, rules, full_text, line_index,
            )
        elif block.type == "blockquote":
            pieces.append(_make_block_piece(
                block, kind="blockquote", ordinal=len(pieces),
                source_block_index=block_index, ordinal_in_block=0,
                line_index=line_index,
            ))
            block_index += 1
        elif block.type == "table":
            pieces.append(_make_block_piece(
                block, kind="table", ordinal=len(pieces),
                source_block_index=block_index, ordinal_in_block=0,
                line_index=line_index,
            ))
            block_index += 1
        elif block.type == "horizontal_rule":
            pieces.append(_make_block_piece(
                block, kind="horizontal_rule", ordinal=len(pieces),
                source_block_index=block_index, ordinal_in_block=0,
                line_index=line_index,
            ))
            block_index += 1
        elif block.type == "image":
            pieces.append(_make_block_piece(
                block, kind="image", ordinal=len(pieces),
                source_block_index=block_index, ordinal_in_block=0,
                line_index=line_index,
            ))
            block_index += 1
        else:  # pragma: no cover — markdown_parse emits only the known types
            raise ValueError(f"unknown block type: {block.type}")

    return pieces


def _make_block_piece(
    block: ParsedBlock,
    *,
    kind: PieceKind,
    ordinal: int,
    source_block_index: int,
    ordinal_in_block: int,
    line_index: LineIndex,
) -> AtomicPiece:
    start_line, start_column = line_index.line_column(block.source_offset_start)
    end_anchor = _inclusive_end(block.source_offset_start, block.source_offset_end)
    end_line, end_column = line_index.line_column(end_anchor)
    return AtomicPiece(
        ordinal=ordinal,
        kind=kind,
        source_block_index=source_block_index,
        ordinal_in_block=ordinal_in_block,
        source_offset_start=block.source_offset_start,
        source_offset_end=block.source_offset_end,
        start_line=start_line,
        end_line=end_line,
        start_column=start_column,
        end_column=end_column,
        text_hash=_hash(block.text),
        text_snapshot=block.text,
        heading_level=block.heading_level,
    )


def _append_sentence_pieces(
    pieces: list[AtomicPiece],
    block: ParsedBlock,
    block_index: int,
    line_index: LineIndex,
) -> None:
    """Emit one piece per sentence within a paragraph block.

    Sentence char_start/char_end are relative to the block's text; we
    rebase them onto the revision's source offsets so each piece carries
    an absolute slice anchor.
    """
    sentences = split_sentences(block.text)
    if not sentences:
        # Empty paragraph — skip. ParsedBlock for an empty paragraph
        # would be unusual but not impossible (whitespace-only).
        return
    base = block.source_offset_start
    for i, sentence in enumerate(sentences):
        abs_start = base + sentence.char_start
        abs_end = base + sentence.char_end
        start_line, start_column = line_index.line_column(abs_start)
        end_line, end_column = line_index.line_column(_inclusive_end(abs_start, abs_end))
        pieces.append(AtomicPiece(
            ordinal=len(pieces),
            kind="sentence",
            source_block_index=block_index,
            ordinal_in_block=i,
            source_offset_start=abs_start,
            source_offset_end=abs_end,
            start_line=start_line,
            end_line=end_line,
            start_column=start_column,
            end_column=end_column,
            text_hash=_hash(sentence.text),
            text_snapshot=sentence.text,
        ))


def _append_list_pieces(
    pieces: list[AtomicPiece],
    blocks: list[ParsedBlock],
    block_index: int,
    rules: AtomicRules,
    full_text: str,
    line_index: LineIndex,
) -> int:
    """Emit list pieces from `blocks[block_index:]` and return the next
    block index to process."""
    if rules.list_atomicity == "item":
        block = blocks[block_index]
        pieces.append(_make_block_piece(
            block, kind="list_item", ordinal=len(pieces),
            source_block_index=block_index, ordinal_in_block=0,
            line_index=line_index,
        ))
        return block_index + 1

    # list_atomicity == "run": collect the consecutive list_item run.
    run_end = block_index
    while run_end + 1 < len(blocks) and blocks[run_end + 1].type == "list_item":
        run_end += 1
    first = blocks[block_index]
    last = blocks[run_end]
    span_start = first.source_offset_start
    span_end = last.source_offset_end
    span_text = full_text[span_start:span_end]
    start_line, start_column = line_index.line_column(span_start)
    end_line, end_column = line_index.line_column(_inclusive_end(span_start, span_end))
    pieces.append(AtomicPiece(
        ordinal=len(pieces),
        kind="list_run",
        source_block_index=block_index,
        ordinal_in_block=0,
        source_offset_start=span_start,
        source_offset_end=span_end,
        start_line=start_line,
        end_line=end_line,
        start_column=start_column,
        end_column=end_column,
        text_hash=_hash(span_text),
        text_snapshot=span_text,
    ))
    return run_end + 1


def _inclusive_end(start: int, end: int) -> int:
    """Pick the offset to look up `end_line/end_column` for a half-open
    span. For an empty span, fall back to start (avoids stepping past
    EOF when the block sits at the end of the document)."""
    return end - 1 if end > start else start


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_pieces(pieces: list[AtomicPiece], full_text: str) -> None:
    """Phase 1 validation gate. Raises AssertionError on first violation
    so failures surface immediately at ingest, not later at read time."""
    text_len = len(full_text)
    for i, p in enumerate(pieces):
        assert p.ordinal == i, f"piece[{i}] ordinal={p.ordinal} (gap or reorder)"
        assert 0 <= p.source_offset_start <= p.source_offset_end <= text_len, (
            f"piece[{i}] offsets out of bounds: "
            f"{p.source_offset_start}..{p.source_offset_end} (text len {text_len})"
        )
        slice_text = full_text[p.source_offset_start:p.source_offset_end]
        assert _hash(slice_text) == p.text_hash, (
            f"piece[{i}] text_hash mismatch (revision drifted from snapshot?)"
        )
        assert slice_text == p.text_snapshot, (
            f"piece[{i}] text_snapshot mismatch"
        )
        if i > 0:
            prev = pieces[i - 1]
            assert prev.source_offset_start <= p.source_offset_start, (
                f"piece[{i}] not in document order"
            )
