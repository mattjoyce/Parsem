"""Atomic-piece persistence. One row per piece, indexed by (revision, ordinal).

Pieces persist before chunking runs because chunk_pieces references them
by id. The insert returns an ordinal→id map so the caller can resolve
in-memory piece references when persisting chunks.
"""

from __future__ import annotations

import sqlite3

from parsem.domain.atomic import AtomicPiece


def insert_atomic_pieces(
    conn: sqlite3.Connection,
    *,
    revision_id: int,
    pieces: list[AtomicPiece],
) -> dict[int, int]:
    """Insert pieces in ordinal order. Returns ordinal→piece_id mapping.

    Resolves `structural_parent_ordinal` to `structural_parent_piece_id`
    using the partial map built during the loop. Phase 1 doesn't set
    parent ordinals, but the resolution path is wired so later strategies
    can use it without a schema change.
    """
    ordinal_to_id: dict[int, int] = {}
    for piece in pieces:
        parent_id = (
            ordinal_to_id.get(piece.structural_parent_ordinal)
            if piece.structural_parent_ordinal is not None
            else None
        )
        cur = conn.execute(
            "INSERT INTO atomic_pieces"
            " (revision_id, ordinal, kind, source_block_index, ordinal_in_block,"
            "  source_offset_start, source_offset_end, start_line, end_line,"
            "  start_column, end_column, text_hash, text_snapshot, heading_level,"
            "  structural_parent_piece_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision_id,
                piece.ordinal,
                piece.kind,
                piece.source_block_index,
                piece.ordinal_in_block,
                piece.source_offset_start,
                piece.source_offset_end,
                piece.start_line,
                piece.end_line,
                piece.start_column,
                piece.end_column,
                piece.text_hash,
                piece.text_snapshot,
                piece.heading_level,
                parent_id,
            ),
        )
        piece_id = cur.lastrowid
        assert piece_id is not None
        ordinal_to_id[piece.ordinal] = piece_id
    return ordinal_to_id


def load_atomic_pieces(
    conn: sqlite3.Connection, revision_id: int
) -> list[AtomicPiece]:
    """Load all pieces for a revision in ordinal order."""
    rows = conn.execute(
        "SELECT id, ordinal, kind, source_block_index, ordinal_in_block,"
        " source_offset_start, source_offset_end, start_line, end_line,"
        " start_column, end_column, text_hash, text_snapshot, heading_level"
        " FROM atomic_pieces WHERE revision_id=? ORDER BY ordinal",
        (revision_id,),
    ).fetchall()
    return [
        AtomicPiece(
            id=row["id"],
            ordinal=row["ordinal"],
            kind=row["kind"],
            source_block_index=row["source_block_index"],
            ordinal_in_block=row["ordinal_in_block"],
            source_offset_start=row["source_offset_start"],
            source_offset_end=row["source_offset_end"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            start_column=row["start_column"],
            end_column=row["end_column"],
            text_hash=row["text_hash"],
            text_snapshot=row["text_snapshot"],
            heading_level=row["heading_level"],
        )
        for row in rows
    ]
