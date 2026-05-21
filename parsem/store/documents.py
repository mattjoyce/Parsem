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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from parsem.domain.atomic import AtomicPiece
from parsem.domain.materialize import Chunk, Section
from parsem.store.atomic_pieces import insert_atomic_pieces
from parsem.store.chunking_runs import ChunkingRun, insert_chunking_run
from parsem.store.tags import load_tags_for_documents

SILHOUETTE_BUCKET_COUNT = 25

BucketKind = Literal["absent", "unread", "read_unrated", "rated"]


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
    source_type: str = "markdown",
    source_hash: str | None = None,
    now: datetime,
) -> int:
    """Insert a documents row; return the new id.

    `source_type` defaults to 'markdown' for back-compat with the
    cycle-1 path; ductile-driven PDF arrivals pass 'pdf'. `source_hash`
    is the SHA-256 of the originally-arrived bytes — populated by the
    arrivals path so subsequent duplicate drops short-circuit (ADR 0002).
    """
    cur = conn.execute(
        "INSERT INTO documents "
        "(title, source_type, original_path, status, failure_reason,"
        " total_chunks, source_hash, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            title,
            source_type,
            original_path,
            status,
            failure_reason,
            total_chunks,
            source_hash,
            now.isoformat(),
            now.isoformat(),
        ),
    )
    new_id = cur.lastrowid
    assert new_id is not None  # AUTOINCREMENT always returns an id
    conn.commit()
    return new_id


def find_document_id_by_source_hash(
    conn: sqlite3.Connection, source_hash: str
) -> int | None:
    """Lookup for the arrivals dedup path (ADR 0002). Returns the
    existing document id when a row already carries this hash, or None
    when this is a first arrival. Hash is the SHA-256 of the
    originally-arrived bytes (.md text or .pdf binary)."""
    row = conn.execute(
        "SELECT id FROM documents WHERE source_hash=? LIMIT 1",
        (source_hash,),
    ).fetchone()
    return row["id"] if row else None


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
    """Patch original_path after the file is renamed into originals/.
    The two-step pattern (insert with placeholder → write/rename file
    → UPDATE) lives in the watcher's ingest path, where the doc id
    has to exist before the file is renamed to `originals/<id>.md`."""
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
class BucketState:
    """One of the 25 cells in the library-tile silhouette (ADR 0005).

    `kind`:
      - 'absent' — bucket covers no chunks (only happens when the doc
        has fewer than 25 chunks; the cell renders invisible).
      - 'unread' — all assigned chunks are past the high-water mark.
      - 'read_unrated' — bucket has settled chunks but no ratings.
      - 'rated' — bucket has at least one rated chunk; `mean_rating`
        is the mean of the rated chunks in the bucket, rounded to
        the integer rating palette {1..5}.

    `mean_rating` is non-None iff `kind == 'rated'`. The drawer's
    full-resolution heatmap uses the same value space at chunk level.
    """

    kind: BucketKind
    mean_rating: int | None = None


def compute_silhouette_buckets(
    chunk_ratings: list[int | None],
    high_water_position: int,
) -> list[BucketState]:
    """Down-sample a doc's chunk-level state into the 25-cell tile
    silhouette. Pure function — no DB access.

    Each chunk `j` maps to bucket `⌊j·25/N⌋` where N is the total
    chunk count. The same formula works for N>=25 (many chunks per
    bucket) and N<25 (sparse buckets, some 'absent'). For N==0 the
    silhouette is 25 'unread' buckets — covers converting / failed /
    never-parsed docs.

    `chunk_ratings` is the dense rating list from
    `load_chunk_ratings_dense`: length == N, entry i is the latest
    rating on chunk i or None. `high_water_position` partitions the
    sequence: chunk j is settled iff j < high_water_position.

    See ADR 0005 §"Tile anatomy" and §"5x5 silhouette" for the
    semantic role of the resulting marks. The data layer commits to
    delivering 25 buckets; the template renders them as a 5x5 grid.
    """
    total_chunks = len(chunk_ratings)
    if total_chunks == 0:
        return [BucketState("unread") for _ in range(SILHOUETTE_BUCKET_COUNT)]

    # Bucket assignment per chunk. Same formula for any N — sparse for
    # small docs, dense for large ones.
    bucket_chunks: list[list[int]] = [
        [] for _ in range(SILHOUETTE_BUCKET_COUNT)
    ]
    for chunk_pos in range(total_chunks):
        bucket_idx = chunk_pos * SILHOUETTE_BUCKET_COUNT // total_chunks
        # Safety clamp — exact arithmetic should always land in range
        # but a defensive min() protects against any drift.
        bucket_idx = min(bucket_idx, SILHOUETTE_BUCKET_COUNT - 1)
        bucket_chunks[bucket_idx].append(chunk_pos)

    result: list[BucketState] = []
    for chunk_positions in bucket_chunks:
        if not chunk_positions:
            result.append(BucketState("absent"))
            continue

        ratings_in_bucket: list[int] = [
            r for pos in chunk_positions
            if (r := chunk_ratings[pos]) is not None
        ]
        any_settled = any(pos < high_water_position for pos in chunk_positions)

        if not any_settled:
            result.append(BucketState("unread"))
        elif not ratings_in_bucket:
            result.append(BucketState("read_unrated"))
        else:
            mean = sum(ratings_in_bucket) / len(ratings_in_bucket)
            rounded = max(1, min(5, round(mean)))
            result.append(BucketState("rated", mean_rating=rounded))

    return result


@dataclass(frozen=True)
class DrawerSection:
    """One section in the library v2 drawer's full-resolution heatmap
    (ADR 0005). `title` is the section heading text (empty for the
    pre-first-heading lead). `cells` is one `BucketState` per chunk in
    the section — no down-sampling, so cell `i` represents the chunk
    at the section's i-th position. Rated cells carry the chunk's own
    rating (no mean — there's only one chunk per cell).

    Reuses BucketState as the value type; `mean_rating` on a single-
    chunk cell is just that chunk's rating value."""

    title: str
    cells: list[BucketState]


def compute_drawer_sections(
    section_layout: list[tuple[str, int]],
    chunk_ratings: list[int | None],
    high_water_position: int,
) -> list[DrawerSection]:
    """Render the drawer's section-aware full heatmap (ADR 0005).
    Pure function — no DB access. One cell per chunk, grouped by
    section, with the same three-state semantics as the tile
    silhouette (unread / read_unrated / rated).

    `section_layout` is `(title, chunk_count)` in section order; we
    walk it concurrently with `chunk_ratings`. If section_layout is
    empty (doc not parsed yet) returns an empty list — the drawer
    template hides the heatmap section in that case.
    """
    if not section_layout:
        return []
    result: list[DrawerSection] = []
    chunk_pos = 0
    total_chunks = len(chunk_ratings)
    for title, count in section_layout:
        cells: list[BucketState] = []
        for _ in range(count):
            if chunk_pos >= high_water_position:
                cells.append(BucketState("unread"))
            elif chunk_pos >= total_chunks:
                # Section layout claims more chunks than we have
                # ratings for — defensive 'unread' fallback. Should
                # not happen in practice (the layout is built from
                # the same `sections` table chunk_ratings indexes
                # into) but keeps the renderer safe.
                cells.append(BucketState("unread"))
            else:
                rating = chunk_ratings[chunk_pos]
                if rating is None:
                    cells.append(BucketState("read_unrated"))
                else:
                    cells.append(BucketState("rated", mean_rating=rating))
            chunk_pos += 1
        result.append(DrawerSection(title=title, cells=cells))
    return result


def derive_source_domain(source_type: str, original_path: str) -> str | None:
    """Pull the registrable host from a URL-ingested doc's stored URL.

    Returns the netloc (host[:port]) for `source_type == 'url'` rows,
    None otherwise. For URL docs, `documents.original_path` carries the
    full URL (see parsem.ingest.url_submit). Malformed URLs yield None
    rather than crashing — let the slug fall back to the URL badge.
    """
    if source_type != "url":
        return None
    try:
        parsed = urlparse(original_path)
    except (ValueError, AttributeError):
        return None
    host = (parsed.hostname or "").lower()
    return host or None


@dataclass(frozen=True)
class LibraryRow:
    """A document plus the derived display state the library v2 tile +
    drawer needs (ADR 0005, bd Parsem-7wu).

    Phase 1 fields (preserved for v1 templates during transition):
      - `document`, `progress_percent`, `chunk_ratings`.

    Phase 2 additions (library v2 — pure data, no template change
    forced; the v1 row partial ignores these):
      - `source_domain`: parsed hostname for URL docs, None for files.
      - `ingest_date`: alias of `document.created_at`, surfaced
        prominently so the slug template doesn't reach through the
        document object.
      - `last_opened`: `reading_state.updated_at` if the doc has ever
        been opened, else None.
      - `pin_count`: integer count from the pins table.
      - `total_reading_seconds`: sum of `chunks.estimated_read_seconds`
        for the doc's chunks. 0.0 when the doc has no chunks yet.
      - `tags`: alphabetised list of manual tags (v2.0 has no
        auto-tags).
      - `section_layout`: list of `(section_title, chunk_count)` in
        section order. Empty when the doc isn't `ready` yet. Drives
        the drawer's full-resolution section-aware heatmap.
      - `silhouette_buckets`: always 25 `BucketState`s for the tile
        mark. Computed via `compute_silhouette_buckets`.

    Existing v1 consumers (the rename route's _library_row.html
    partial) read only the Phase 1 fields and stay green."""

    document: Document
    progress_percent: int
    chunk_ratings: list[int | None]
    source_domain: str | None
    ingest_date: datetime
    last_opened: datetime | None
    pin_count: int
    total_reading_seconds: float
    tags: list[str]
    section_layout: list[tuple[str, int]] = field(default_factory=list)
    silhouette_buckets: list[BucketState] = field(default_factory=list)
    # Drawer-shape data (Parsem-7wu.3). current_position / high_water
    # are exposed so the drawer can render "chunk x of N" stats and the
    # full-resolution heatmap walks the same partition the silhouette
    # uses. drawer_sections is the pre-computed section-aware heatmap
    # body (always-list, may be empty when the doc isn't parsed).
    current_position: int = 0
    high_water_position: int = 0
    drawer_sections: list[DrawerSection] = field(default_factory=list)


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
    """All documents with the full library v2 payload (ADR 0005, bd
    Parsem-7wu), ordered by last-opened DESC (reading_state.updated_at),
    falling back to created_at for never-opened docs, with a stable
    secondary sort by title. Spec §9.1; Parsem-3z8 + Parsem-5oi +
    claude-yda; library-v2 extension Parsem-7wu.1.

    One LEFT JOIN against reading_state covers ordering + progress +
    high-water + last-opened. Two correlated subqueries inline
    `pin_count` and `total_reading_seconds` so the per-doc loop only
    needs ratings, section layout, and the bulk tag load.
    """
    rows = conn.execute(
        "SELECT d.id, d.title, d.source_type, d.original_path, d.status,"
        " d.failure_reason, d.total_chunks, d.preference_overrides_json,"
        " d.created_at, d.updated_at,"
        " rs.current_position, rs.high_water_position,"
        " rs.updated_at AS last_opened_at,"
        " (SELECT COUNT(*) FROM pins WHERE document_id = d.id)"
        "   AS pin_count,"
        " (SELECT COALESCE(SUM(estimated_read_seconds), 0)"
        "    FROM chunks WHERE document_id = d.id)"
        "   AS total_reading_seconds"
        " FROM documents d"
        " LEFT JOIN reading_state rs ON rs.document_id = d.id"
        " ORDER BY COALESCE(rs.updated_at, d.created_at) DESC, d.title ASC"
    ).fetchall()

    doc_ids = [row["id"] for row in rows]
    tags_by_doc = load_tags_for_documents(conn, doc_ids)

    result: list[LibraryRow] = []
    for row in rows:
        doc = _document_from_row(row)
        ratings = load_chunk_ratings_dense(conn, doc.id, doc.total_chunks)
        high_water = row["high_water_position"] or 0
        last_opened_raw = row["last_opened_at"]
        last_opened = (
            datetime.fromisoformat(last_opened_raw)
            if last_opened_raw is not None
            else None
        )
        section_layout = (
            load_section_layout(conn, doc.id)
            if doc.status == "ready" and (doc.total_chunks or 0) > 0
            else []
        )
        silhouette = compute_silhouette_buckets(ratings, high_water)
        drawer_sections = compute_drawer_sections(
            section_layout, ratings, high_water
        )

        result.append(LibraryRow(
            document=doc,
            progress_percent=progress_percent(
                row["total_chunks"], row["current_position"]
            ),
            chunk_ratings=ratings,
            source_domain=derive_source_domain(
                doc.source_type, doc.original_path
            ),
            ingest_date=doc.created_at,
            last_opened=last_opened,
            pin_count=row["pin_count"],
            total_reading_seconds=float(row["total_reading_seconds"]),
            tags=tags_by_doc.get(doc.id, []),
            section_layout=section_layout,
            silhouette_buckets=silhouette,
            current_position=row["current_position"] or 0,
            high_water_position=high_water,
            drawer_sections=drawer_sections,
        ))
    return result


def load_section_layout(
    conn: sqlite3.Connection, document_id: int
) -> list[tuple[str, int]]:
    """Return the doc's sections as `(heading_text, chunk_count)` pairs
    in section order, for the library v2 drawer's full-resolution
    section-aware heatmap.

    Heading text is derived from the heading chunk's first line with
    leading `#` markers stripped — close enough for a section label and
    avoids storing a denormalised field. A section without a resolvable
    heading chunk (legacy / weird state) renders with an empty title;
    the drawer template falls back to "§" in that case.
    """
    rows = conn.execute(
        "SELECT s.start_chunk_position, s.end_chunk_position,"
        " c.text AS heading_text"
        " FROM sections s"
        " LEFT JOIN chunks c ON c.id = s.heading_chunk_id"
        " WHERE s.document_id = ?"
        " ORDER BY s.start_chunk_position",
        (document_id,),
    ).fetchall()
    layout: list[tuple[str, int]] = []
    for row in rows:
        chunk_count = row["end_chunk_position"] - row["start_chunk_position"] + 1
        heading_text = row["heading_text"] or ""
        first_line = heading_text.split("\n", 1)[0].lstrip("#").strip()
        layout.append((first_line, chunk_count))
    return layout


def load_chunk_ratings_dense(
    conn: sqlite3.Connection, document_id: int, total_chunks: int | None
) -> list[int | None]:
    """Return a dense per-position rating list for the library heatmap
    (claude-yda). Empty list when total_chunks is unknown (still
    processing or failed). Unrated chunks are None.

    JOIN chunks → chunk_ratings, position-keyed. The SQL filters by
    document_id; no chunking_run filter — chunks with NULL
    chunking_run_id (legacy fixtures) AND substrate-run chunks both
    contribute, since the projection key is chunks.id and ratings
    follow the chunk row regardless of which run produced it.
    """
    if not total_chunks:
        return []
    rows = conn.execute(
        "SELECT c.position, r.rating"
        " FROM chunks c"
        " LEFT JOIN chunk_ratings r ON r.chunk_id = c.id"
        " WHERE c.document_id = ?"
        " ORDER BY c.position",
        (document_id,),
    ).fetchall()
    by_pos: dict[int, int | None] = {row["position"]: row["rating"] for row in rows}
    return [by_pos.get(i) for i in range(total_chunks)]


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
    chunk_records: list[Chunk],
    section_records: list[Section],
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
) -> list[Chunk]:
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
        Chunk(
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
) -> list[Section]:
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
        Section(
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
