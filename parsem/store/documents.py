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

from parsem.domain.atomic import AtomicPiece
from parsem.domain.chunking import Chunk, Section
from parsem.domain.materialize import ChunkRecord, SectionRecord
from parsem.store.atomic_pieces import insert_atomic_pieces
from parsem.store.chunking_runs import ChunkingRun, insert_chunking_run


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


@dataclass(frozen=True)
class LibraryRow:
    """A document plus the small bits of derived display state the
    library row needs. Bead Parsem-5oi adds `progress_percent`; future
    beads may add the heatmap strip here too (Parsem-8p5)."""

    document: Document
    progress_percent: int


def progress_percent(total_chunks: int | None, current_position: int | None) -> int:
    """Library-row progress percentage. Bead Parsem-5oi, spec §9.1.

    Formula: 100 * (current_position + 1) / total_chunks, rounded and
    clamped to [0, 100]. Returns 0 when the document has never been
    opened (current_position is None) or when total_chunks is unknown
    (still processing or failed)."""
    if not total_chunks or current_position is None:
        return 0
    raw = round(100 * (current_position + 1) / total_chunks)
    return max(0, min(100, raw))


def _document_from_row(row: sqlite3.Row) -> Document:
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


def list_library_rows(conn: sqlite3.Connection) -> list[LibraryRow]:
    """All documents with progress percent, ordered by last-opened DESC
    (reading_state.updated_at), falling back to created_at for never-
    opened docs, with a stable secondary sort by title. Spec §9.1;
    beads Parsem-3z8 + Parsem-5oi.

    Single LEFT JOIN against reading_state covers ordering AND progress
    computation in one round trip.
    """
    rows = conn.execute(
        "SELECT d.id, d.title, d.source_type, d.original_path, d.status,"
        " d.failure_reason, d.total_chunks, d.preference_overrides_json,"
        " d.created_at, d.updated_at, rs.current_position"
        " FROM documents d"
        " LEFT JOIN reading_state rs ON rs.document_id = d.id"
        " ORDER BY COALESCE(rs.updated_at, d.created_at) DESC, d.title ASC"
    ).fetchall()
    return [
        LibraryRow(
            document=_document_from_row(row),
            progress_percent=progress_percent(
                row["total_chunks"], row["current_position"]
            ),
        )
        for row in rows
    ]


def progress_percent_for_document(
    conn: sqlite3.Connection, document_id: int
) -> int:
    """Single-doc progress lookup. Used by the rename route, which needs
    to render one row partial without a full library scan."""
    row = conn.execute(
        "SELECT d.total_chunks, rs.current_position"
        " FROM documents d"
        " LEFT JOIN reading_state rs ON rs.document_id = d.id"
        " WHERE d.id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        return 0
    return progress_percent(row["total_chunks"], row["current_position"])


def rename_document(
    conn: sqlite3.Connection,
    document_id: int,
    *,
    title: str,
    now: datetime,
) -> None:
    """Rename a document. Caller is responsible for trimming and length
    validation (the route does that so it can return a 422 with a
    helpful detail). Spec §22; bead Parsem-kwq."""
    conn.execute(
        "UPDATE documents SET title=?, updated_at=? WHERE id=?",
        (title, now.isoformat(), document_id),
    )
    conn.commit()


def delete_document_chunks_and_sections(
    conn: sqlite3.Connection, document_id: int
) -> None:
    """Wipe a document's substrate (revisions → pieces, runs, chunks) and
    sections. Used by retry-parse to clear prior partial state before
    re-running the parse pipeline.

    Deleting `document_revisions` cascades to `atomic_pieces`,
    `chunking_runs`, `chunks`, `chunk_pieces`, `chunk_ratings`, and
    `pins`. Sections aren't cascaded by revisions (they FK directly to
    documents) so they're wiped explicitly. Stray chunks with NULL
    chunking_run_id (legacy / test fixtures) are also cleared so a
    retry doesn't leave a half-substrate behind."""
    conn.execute(
        "DELETE FROM document_revisions WHERE document_id=?", (document_id,)
    )
    conn.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
    conn.execute("DELETE FROM sections WHERE document_id=?", (document_id,))
    conn.commit()


def delete_document(conn: sqlite3.Connection, document_id: int) -> bool:
    """Hard-delete a document. Returns True on success, False if no row
    matched the id. Spec §22; bead Parsem-eci.

    The schema's ON DELETE CASCADE on sections/chunks/reading_events/
    reading_state/pins (§21) is what actually wipes the dependents —
    the route also needs to unlink the original .md file separately.
    """
    cur = conn.execute("DELETE FROM documents WHERE id=?", (document_id,))
    conn.commit()
    return cur.rowcount > 0


def load_chunks_for_document(conn: sqlite3.Connection, document_id: int) -> list[Chunk]:
    """Latest-run chunks for a document, returned as legacy `Chunk` shape.

    Filters by the most recent chunking_run when one exists; falls back
    to chunks with NULL chunking_run_id for legacy/test fixtures that
    bypass the substrate. Splitting the query on the run-existence check
    keeps the SQL simple and the index hits clean.
    """
    run_row = conn.execute(
        "SELECT cr.id FROM chunking_runs cr"
        " JOIN document_revisions dr ON dr.id = cr.revision_id"
        " WHERE dr.document_id = ?"
        " ORDER BY cr.id DESC LIMIT 1",
        (document_id,),
    ).fetchone()
    if run_row is not None:
        rows = conn.execute(
            "SELECT position, source_offset_start, source_offset_end, text,"
            " lead_token_type, lead_heading_level, estimated_read_seconds"
            " FROM chunks WHERE document_id=? AND chunking_run_id=?"
            " ORDER BY position",
            (document_id, run_row["id"]),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT position, source_offset_start, source_offset_end, text,"
            " lead_token_type, lead_heading_level, estimated_read_seconds"
            " FROM chunks WHERE document_id=? AND chunking_run_id IS NULL"
            " ORDER BY position",
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


def insert_chunking_artifacts(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    revision_id: int,
    strategy_name: str,
    strategy_version: str,
    rules_hash: str,
    pieces: list[AtomicPiece],
    chunk_records: list[ChunkRecord],
    section_records: list[SectionRecord],
    now: datetime,
) -> ChunkingRun:
    """Persist the full substrate output for a single chunking pass.

    Order matches the FK graph: revisions exist already (caller handed
    in `revision_id`); pieces and the run go in next; chunks reference
    the run; chunk_pieces junction maps planned ordinals to piece ids;
    sections reference chunks via heading_chunk_id (resolved from the
    position→id map); chunks.section_id is back-filled in a final
    UPDATE pass once section ids are known.

    Wraps all writes in a single transaction so a failed insert leaves
    no half-substrate behind.
    """
    timestamp = now.isoformat()
    try:
        ordinal_to_piece_id = insert_atomic_pieces(
            conn, revision_id=revision_id, pieces=pieces
        )
        run = insert_chunking_run(
            conn,
            revision_id=revision_id,
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            rules_hash=rules_hash,
            now=now,
        )

        position_to_chunk_id: dict[int, int] = {}
        for record in chunk_records:
            cur = conn.execute(
                "INSERT INTO chunks"
                " (document_id, position, source_offset_start, source_offset_end,"
                "  text, lead_token_type, lead_heading_level,"
                "  estimated_read_seconds, created_at,"
                "  chunking_run_id, revision_id, text_hash,"
                "  start_line, end_line, start_column, end_column)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    record.position,
                    record.source_offset_start,
                    record.source_offset_end,
                    record.text,
                    record.lead_token_type,
                    record.lead_heading_level,
                    record.estimated_read_seconds,
                    timestamp,
                    run.id,
                    revision_id,
                    record.text_hash,
                    record.start_line,
                    record.end_line,
                    record.start_column,
                    record.end_column,
                ),
            )
            chunk_id = cur.lastrowid
            assert chunk_id is not None
            position_to_chunk_id[record.position] = chunk_id

            for ordinal_in_chunk, piece_ordinal in enumerate(record.piece_ordinals):
                conn.execute(
                    "INSERT INTO chunk_pieces (chunk_id, piece_id, ordinal)"
                    " VALUES (?, ?, ?)",
                    (chunk_id, ordinal_to_piece_id[piece_ordinal], ordinal_in_chunk),
                )

        section_id_by_start: dict[int, int] = {}
        for section in section_records:
            heading_chunk_id = (
                position_to_chunk_id.get(section.heading_chunk_position)
                if section.heading_chunk_position is not None
                else None
            )
            cur = conn.execute(
                "INSERT INTO sections"
                " (document_id, heading_chunk_id, heading_level,"
                "  start_chunk_position, end_chunk_position)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    document_id,
                    heading_chunk_id,
                    section.heading_level,
                    section.start_chunk_position,
                    section.end_chunk_position,
                ),
            )
            section_id = cur.lastrowid
            assert section_id is not None
            section_id_by_start[section.start_chunk_position] = section_id

        for section in section_records:
            section_id = section_id_by_start[section.start_chunk_position]
            conn.execute(
                "UPDATE chunks SET section_id=?"
                " WHERE document_id=? AND chunking_run_id=?"
                " AND position BETWEEN ? AND ?",
                (
                    section_id,
                    document_id,
                    run.id,
                    section.start_chunk_position,
                    section.end_chunk_position,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return run


def load_chunk_records_for_document(
    conn: sqlite3.Connection, document_id: int
) -> list[ChunkRecord]:
    """Latest-run chunk records for a document. Returns [] when the
    document has no chunking run yet (still processing or failed)."""
    rows = conn.execute(
        "SELECT c.id, c.position, c.source_offset_start, c.source_offset_end,"
        " c.text, c.text_hash, c.lead_token_type, c.lead_heading_level,"
        " c.estimated_read_seconds, c.start_line, c.end_line, c.start_column,"
        " c.end_column"
        " FROM chunks c"
        " JOIN ("
        "  SELECT id FROM chunking_runs cr"
        "  JOIN document_revisions dr ON dr.id = cr.revision_id"
        "  WHERE dr.document_id = ?"
        "  ORDER BY cr.id DESC LIMIT 1"
        " ) latest ON latest.id = c.chunking_run_id"
        " ORDER BY c.position",
        (document_id,),
    ).fetchall()
    if not rows:
        return []
    chunk_ids = [row["id"] for row in rows]
    placeholders = ",".join("?" * len(chunk_ids))
    junction_rows = conn.execute(
        f"SELECT cp.chunk_id, cp.ordinal, ap.ordinal AS piece_ordinal"
        f" FROM chunk_pieces cp"
        f" JOIN atomic_pieces ap ON ap.id = cp.piece_id"
        f" WHERE cp.chunk_id IN ({placeholders})"
        f" ORDER BY cp.chunk_id, cp.ordinal",
        chunk_ids,
    ).fetchall()
    pieces_by_chunk: dict[int, list[int]] = {cid: [] for cid in chunk_ids}
    for jr in junction_rows:
        pieces_by_chunk[jr["chunk_id"]].append(jr["piece_ordinal"])
    return [
        ChunkRecord(
            position=row["position"],
            source_offset_start=row["source_offset_start"],
            source_offset_end=row["source_offset_end"],
            text=row["text"],
            text_hash=row["text_hash"],
            lead_token_type=row["lead_token_type"],
            lead_heading_level=row["lead_heading_level"],
            estimated_read_seconds=row["estimated_read_seconds"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            start_column=row["start_column"],
            end_column=row["end_column"],
            piece_ordinals=pieces_by_chunk[row["id"]],
        )
        for row in rows
    ]


def load_section_records_for_document(
    conn: sqlite3.Connection, document_id: int
) -> list[SectionRecord]:
    """Latest-run sections for a document."""
    rows = conn.execute(
        "SELECT s.start_chunk_position, s.end_chunk_position, s.heading_level,"
        " c.position AS heading_chunk_position"
        " FROM sections s LEFT JOIN chunks c ON c.id = s.heading_chunk_id"
        " WHERE s.document_id=?"
        " AND ("
        "  s.heading_chunk_id IS NULL"
        "  OR c.chunking_run_id = ("
        "    SELECT id FROM chunking_runs cr"
        "    JOIN document_revisions dr ON dr.id = cr.revision_id"
        "    WHERE dr.document_id = ?"
        "    ORDER BY cr.id DESC LIMIT 1"
        "  )"
        " )"
        " ORDER BY s.start_chunk_position",
        (document_id, document_id),
    ).fetchall()
    return [
        SectionRecord(
            heading_chunk_position=row["heading_chunk_position"],
            heading_level=row["heading_level"],
            start_chunk_position=row["start_chunk_position"],
            end_chunk_position=row["end_chunk_position"],
        )
        for row in rows
    ]


def load_sections_for_document(conn: sqlite3.Connection, document_id: int) -> list[Section]:
    """Latest-run sections for a document, in legacy `Section` shape.

    The heading_chunk_id → chunking_run_id check filters out sections
    that belong to retired runs; prologue sections (heading_chunk_id
    IS NULL) survive the filter so they're shown for either run."""
    run_row = conn.execute(
        "SELECT cr.id FROM chunking_runs cr"
        " JOIN document_revisions dr ON dr.id = cr.revision_id"
        " WHERE dr.document_id = ?"
        " ORDER BY cr.id DESC LIMIT 1",
        (document_id,),
    ).fetchone()
    if run_row is not None:
        rows = conn.execute(
            "SELECT s.start_chunk_position, s.end_chunk_position, s.heading_level,"
            " c.position AS heading_chunk_position"
            " FROM sections s LEFT JOIN chunks c ON c.id = s.heading_chunk_id"
            " WHERE s.document_id=?"
            " AND (s.heading_chunk_id IS NULL OR c.chunking_run_id=?)"
            " ORDER BY s.start_chunk_position",
            (document_id, run_row["id"]),
        ).fetchall()
    else:
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
