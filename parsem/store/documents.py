"""Document, chunk, and section persistence. Spec: parsem-spec.md §17.1, §21.

The chunks ↔ sections relationship is circular by design (each section's
heading_chunk_id points at a chunk; each chunk's section_id points at a
section). Insertion handles this in three passes inside one transaction:

1. INSERT all chunks with section_id NULL, capturing position→id map.
2. INSERT sections, resolving heading_chunk_id from the position→id map.
3. UPDATE chunks.section_id by position-range matching against sections.

Reads use a LEFT JOIN to recover heading_chunk_position from the FK.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from parsem.domain.chunking import Chunk, Section


@dataclass(frozen=True)
class Document:
    """Mirrors a documents row. Phase 1 ReaderState.document_id always
    pointed at id=1; Phase 2 makes Documents addressable.
    """

    id: int
    title: str
    source_type: str
    original_path: str
    status: str  # uploaded | processing | ready | failed
    failure_reason: str | None
    total_chunks: int | None
    preference_overrides_json: str | None
    created_at: datetime
    updated_at: datetime


def insert_document(
    conn: sqlite3.Connection,
    *,
    title: str,
    original_path: str,
    status: str,
    total_chunks: int | None = None,
    failure_reason: str | None = None,
    now: datetime,
) -> int:
    """Insert a documents row; return the new id."""
    cur = conn.execute(
        "INSERT INTO documents "
        "(title, source_type, original_path, status, failure_reason,"
        " total_chunks, created_at, updated_at) "
        "VALUES (?, 'markdown', ?, ?, ?, ?, ?, ?)",
        (
            title,
            original_path,
            status,
            failure_reason,
            total_chunks,
            now.isoformat(),
            now.isoformat(),
        ),
    )
    new_id = cur.lastrowid
    assert new_id is not None  # AUTOINCREMENT always returns an id
    conn.commit()
    return new_id


def insert_chunks_and_sections(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    chunks: list[Chunk],
    sections: list[Section],
    now: datetime,
) -> None:
    """Three-pass insert inside one transaction. Rolls back wholesale on
    any error so the database never sees a partial document."""
    timestamp = now.isoformat()
    try:
        position_to_id: dict[int, int] = {}
        for c in chunks:
            cur = conn.execute(
                "INSERT INTO chunks "
                "(document_id, position, source_offset_start, source_offset_end,"
                " text, lead_token_type, lead_heading_level,"
                " estimated_read_seconds, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    c.position,
                    c.source_offset_start,
                    c.source_offset_end,
                    c.text,
                    c.lead_token_type,
                    c.lead_heading_level,
                    c.estimated_read_seconds,
                    timestamp,
                ),
            )
            inserted_id = cur.lastrowid
            assert inserted_id is not None
            position_to_id[c.position] = inserted_id

        section_id_by_start: dict[int, int] = {}
        for s in sections:
            heading_id = (
                position_to_id.get(s.heading_chunk_position)
                if s.heading_chunk_position is not None
                else None
            )
            cur = conn.execute(
                "INSERT INTO sections "
                "(document_id, heading_chunk_id, heading_level,"
                " start_chunk_position, end_chunk_position) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    document_id,
                    heading_id,
                    s.heading_level,
                    s.start_chunk_position,
                    s.end_chunk_position,
                ),
            )
            inserted_id = cur.lastrowid
            assert inserted_id is not None
            section_id_by_start[s.start_chunk_position] = inserted_id

        for s in sections:
            section_id = section_id_by_start[s.start_chunk_position]
            conn.execute(
                "UPDATE chunks SET section_id=? "
                "WHERE document_id=? AND position BETWEEN ? AND ?",
                (section_id, document_id, s.start_chunk_position, s.end_chunk_position),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def mark_document_ready(
    conn: sqlite3.Connection,
    document_id: int,
    *,
    total_chunks: int,
    now: datetime,
) -> None:
    """Final state of a successful upload pipeline (spec §17.1):
    `status='ready'`, `total_chunks` set, `failure_reason` cleared."""
    conn.execute(
        "UPDATE documents SET status='ready', total_chunks=?,"
        " failure_reason=NULL, updated_at=? WHERE id=?",
        (total_chunks, now.isoformat(), document_id),
    )
    conn.commit()


def mark_document_failed(
    conn: sqlite3.Connection,
    document_id: int,
    *,
    reason: str,
    now: datetime,
) -> None:
    """Final state of a failed upload pipeline (spec §17.2):
    `status='failed'` with a human-readable `failure_reason`."""
    conn.execute(
        "UPDATE documents SET status='failed', failure_reason=?,"
        " updated_at=? WHERE id=?",
        (reason, now.isoformat(), document_id),
    )
    conn.commit()


def update_document_original_path(
    conn: sqlite3.Connection,
    document_id: int,
    *,
    original_path: str,
    now: datetime,
) -> None:
    """Patch original_path after the file has been written. Two-step
    insert: documents row goes in first (with a placeholder path) so we
    have an id; the file lands at `data/originals/{id}.md`; then this
    UPDATE records the resolved path."""
    conn.execute(
        "UPDATE documents SET original_path=?, updated_at=? WHERE id=?",
        (original_path, now.isoformat(), document_id),
    )
    conn.commit()


def load_document(conn: sqlite3.Connection, document_id: int) -> Document | None:
    row = conn.execute(
        "SELECT id, title, source_type, original_path, status, failure_reason,"
        " total_chunks, preference_overrides_json, created_at, updated_at"
        " FROM documents WHERE id=?",
        (document_id,),
    ).fetchone()
    if row is None:
        return None
    return Document(
        id=row["id"],
        title=row["title"],
        source_type=row["source_type"],
        original_path=row["original_path"],
        status=row["status"],
        failure_reason=row["failure_reason"],
        total_chunks=row["total_chunks"],
        preference_overrides_json=row["preference_overrides_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def list_documents_for_library(conn: sqlite3.Connection) -> list[Document]:
    """All documents ordered by last-opened DESC (reading_state.updated_at),
    falling back to created_at when a document has never been opened, with
    a stable secondary sort by title. Spec §9.1, bead Parsem-3z8.

    The LEFT JOIN avoids dropping never-opened documents from the listing.
    Sorting in SQL keeps the route handler dumb — it just renders.
    """
    rows = conn.execute(
        "SELECT d.id, d.title, d.source_type, d.original_path, d.status,"
        " d.failure_reason, d.total_chunks, d.preference_overrides_json,"
        " d.created_at, d.updated_at"
        " FROM documents d"
        " LEFT JOIN reading_state rs ON rs.document_id = d.id"
        " ORDER BY COALESCE(rs.updated_at, d.created_at) DESC, d.title ASC"
    ).fetchall()
    return [
        Document(
            id=row["id"],
            title=row["title"],
            source_type=row["source_type"],
            original_path=row["original_path"],
            status=row["status"],
            failure_reason=row["failure_reason"],
            total_chunks=row["total_chunks"],
            preference_overrides_json=row["preference_overrides_json"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
        for row in rows
    ]


def load_chunks_for_document(conn: sqlite3.Connection, document_id: int) -> list[Chunk]:
    rows = conn.execute(
        "SELECT position, source_offset_start, source_offset_end, text,"
        " lead_token_type, lead_heading_level, estimated_read_seconds"
        " FROM chunks WHERE document_id=? ORDER BY position",
        (document_id,),
    ).fetchall()
    return [
        Chunk(
            position=row["position"],
            source_offset_start=row["source_offset_start"],
            source_offset_end=row["source_offset_end"],
            text=row["text"],
            lead_token_type=row["lead_token_type"],
            lead_heading_level=row["lead_heading_level"],
            estimated_read_seconds=row["estimated_read_seconds"],
        )
        for row in rows
    ]


def load_sections_for_document(conn: sqlite3.Connection, document_id: int) -> list[Section]:
    rows = conn.execute(
        "SELECT s.start_chunk_position, s.end_chunk_position, s.heading_level,"
        " c.position AS heading_chunk_position"
        " FROM sections s LEFT JOIN chunks c ON c.id = s.heading_chunk_id"
        " WHERE s.document_id=? ORDER BY s.start_chunk_position",
        (document_id,),
    ).fetchall()
    return [
        Section(
            heading_chunk_position=row["heading_chunk_position"],
            heading_level=row["heading_level"],
            start_chunk_position=row["start_chunk_position"],
            end_chunk_position=row["end_chunk_position"],
        )
        for row in rows
    ]
