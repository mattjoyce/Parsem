"""SQLite foundation for Phase 2 persistence. Spec: parsem-spec.md §19, §21.

Connection helper applies the three PRAGMAs that spec §19 mandates
(foreign_keys=ON for cascade integrity; journal_mode=WAL for
single-writer/many-reader concurrency on file-backed databases;
synchronous=NORMAL as the WAL-friendly durability/throughput trade).

The schema mirrors spec §21 exactly. A single forward-only migration
list drives the migration runner; the schema_version table records
which migrations have been applied so re-running migrate() on an
already-current database is a no-op (the test ISC-17 guards this).

Pure infra. No imports from parsem.web or parsem.domain — the dependency
direction stays store ← (web|domain), never the reverse.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_V1 = """
CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'markdown',
    original_path TEXT NOT NULL,
    status TEXT NOT NULL,
    failure_reason TEXT,
    total_chunks INTEGER,
    preference_overrides_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    heading_chunk_id INTEGER,
    heading_level INTEGER,
    start_chunk_position INTEGER NOT NULL,
    end_chunk_position INTEGER NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    source_offset_start INTEGER NOT NULL,
    source_offset_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    lead_token_type TEXT NOT NULL,
    lead_heading_level INTEGER,
    section_id INTEGER,
    estimated_read_seconds REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY(section_id) REFERENCES sections(id) ON DELETE SET NULL,
    UNIQUE(document_id, position)
);
CREATE INDEX idx_chunks_doc_pos ON chunks(document_id, position);

-- reading_events.chunk_id stores the chunk's POSITION within the
-- document, not chunks.id. The position-as-id semantics carry over from
-- Phase 1's in-memory EventLog (Parsem-v5l drop-in). Cascade-on-document-
-- delete still fires via the documents FK, so deleting a document still
-- removes its events. The chunks-side FK from spec §21 is intentionally
-- omitted — see Parsem-v5l for the reasoning.
CREATE TABLE reading_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    chunk_id INTEGER,
    event_type TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX idx_events_doc_created ON reading_events(document_id, created_at);

CREATE TABLE reading_state (
    document_id INTEGER PRIMARY KEY,
    high_water_position INTEGER NOT NULL DEFAULT 0,
    current_position INTEGER NOT NULL DEFAULT 0,
    last_event_id_applied INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE chunk_ratings (
    chunk_id INTEGER PRIMARY KEY,
    rating INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);

CREATE TABLE pins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    chunk_id_start INTEGER NOT NULL,
    word_start INTEGER NOT NULL DEFAULT 0,
    chunk_id_end INTEGER NOT NULL,
    word_end INTEGER NOT NULL DEFAULT -1,
    color_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY(chunk_id_start) REFERENCES chunks(id) ON DELETE CASCADE,
    FOREIGN KEY(chunk_id_end) REFERENCES chunks(id) ON DELETE CASCADE
);
CREATE INDEX idx_pins_doc_color ON pins(document_id, color_id);

CREATE TABLE pin_color_labels (
    color_id INTEGER PRIMARY KEY,
    label TEXT
);

CREATE TABLE settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    config_json TEXT NOT NULL
);
"""

# v2 — atomic chunking substrate (claude-axx). Adds immutable revisions,
# named chunking runs (provenance), atomic pieces, chunk↔piece junction.
# Existing chunks/sections gain back-references to revision + run.
# User authorized full data wipe — v1 rows are removed during this migration.
SCHEMA_V2 = """
CREATE TABLE document_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    full_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    line_index_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX idx_revisions_doc ON document_revisions(document_id, created_at DESC);

CREATE TABLE chunking_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    rules_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(revision_id) REFERENCES document_revisions(id) ON DELETE CASCADE
);
CREATE INDEX idx_runs_revision ON chunking_runs(revision_id, created_at DESC);

CREATE TABLE atomic_pieces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL,
    source_block_index INTEGER NOT NULL,
    ordinal_in_block INTEGER NOT NULL,
    source_offset_start INTEGER NOT NULL,
    source_offset_end INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    start_column INTEGER NOT NULL,
    end_column INTEGER NOT NULL,
    text_hash TEXT NOT NULL,
    text_snapshot TEXT NOT NULL,
    heading_level INTEGER,
    structural_parent_piece_id INTEGER,
    FOREIGN KEY(revision_id) REFERENCES document_revisions(id) ON DELETE CASCADE,
    FOREIGN KEY(structural_parent_piece_id) REFERENCES atomic_pieces(id) ON DELETE SET NULL,
    UNIQUE(revision_id, ordinal)
);
CREATE INDEX idx_pieces_revision_ord ON atomic_pieces(revision_id, ordinal);

CREATE TABLE chunk_pieces (
    chunk_id INTEGER NOT NULL,
    piece_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY(chunk_id, ordinal),
    FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE,
    FOREIGN KEY(piece_id) REFERENCES atomic_pieces(id) ON DELETE CASCADE
);
CREATE INDEX idx_chunk_pieces_piece ON chunk_pieces(piece_id);

-- ALTER chunks: add back-refs and source-anchoring extras. Nullable on
-- the column itself because SQLite ALTER ADD COLUMN can't enforce NOT
-- NULL retroactively; the v2 wipe below clears v1 rows so app-side
-- inserts will populate every new row.
ALTER TABLE chunks ADD COLUMN chunking_run_id INTEGER
    REFERENCES chunking_runs(id) ON DELETE CASCADE;
ALTER TABLE chunks ADD COLUMN revision_id INTEGER
    REFERENCES document_revisions(id) ON DELETE CASCADE;
ALTER TABLE chunks ADD COLUMN text_hash TEXT;
ALTER TABLE chunks ADD COLUMN start_line INTEGER;
ALTER TABLE chunks ADD COLUMN end_line INTEGER;
ALTER TABLE chunks ADD COLUMN start_column INTEGER;
ALTER TABLE chunks ADD COLUMN end_column INTEGER;
CREATE INDEX idx_chunks_run_pos ON chunks(chunking_run_id, position);

-- Wipe v1 rows. Cascade order matters but is enforced by FKs; explicit
-- order here for clarity. Pins/ratings/state/events are dependents of
-- documents, but we drain leaf tables first so no FK chooses a stale row.
-- Also reset AUTOINCREMENT counters so re-seeded docs start at id=1
-- (otherwise sqlite_sequence remembers the pre-wipe high water mark and
-- the next welcome doc would land at e.g. id=9 instead of 1).
DELETE FROM pins;
DELETE FROM chunk_ratings;
DELETE FROM reading_events;
DELETE FROM reading_state;
DELETE FROM chunks;
DELETE FROM sections;
DELETE FROM documents;
DELETE FROM sqlite_sequence
 WHERE name IN ('documents', 'sections', 'chunks', 'reading_events', 'pins');
"""

# v3 — PDF-readiness hooks (claude-axx.7). Pure additions; no behaviour
# change for the markdown ingest path. Reserves three structural seams
# so a future PDF (or epub / docx) ingest can land without touching
# already-committed schema.
#
#   1. extraction_runs — parallel to chunking_runs but for the
#      source -> markdown step. Markdown-only ingest creates no row;
#      future PDF ingest creates one and links it from the revision.
#   2. document_revisions.extraction_run_id — nullable FK back to
#      extraction_runs. NULL means "the revision IS the upload"
#      (current markdown path).
#   3. atomic_pieces.external_anchor_json — TEXT (JSON), NULL by
#      default. Lets non-markdown converters stash source-shaped
#      anchors (pdf_page, pdf_y, epub_cfi, ...) per piece. No code
#      path consumes it yet.
#
# documents.source_type vocabulary — NOT a schema change. The TEXT
# column already exists (v1). v3 only formalises the supported values:
#   'markdown' (default, in use now)
#   reserved for future: 'pdf', 'epub', 'html', 'docx'
#
# All additions are NULL-safe for existing rows; no backfill needed.
SCHEMA_V3 = """
CREATE TABLE extraction_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    extractor_name TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    source_path TEXT NOT NULL,
    params_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX idx_extraction_runs_doc ON extraction_runs(document_id, created_at DESC);

ALTER TABLE document_revisions ADD COLUMN extraction_run_id INTEGER
    REFERENCES extraction_runs(id) ON DELETE SET NULL;

ALTER TABLE atomic_pieces ADD COLUMN external_anchor_json TEXT;
"""

# v4 — content-hash dedup for the ductile-driven ingest path (ADR 0002).
# `documents.source_hash` is the SHA-256 of the originally-arrived bytes
# (the .md text or the .pdf binary). The /ingest/raw-arrived endpoint
# looks it up before doing any work; a hit returns action=duplicate
# without inserting a second row. NULL on legacy rows (cycle 1 docs);
# the lookup index is built so NULLs don't collide. No backfill.
SCHEMA_V4 = """
ALTER TABLE documents ADD COLUMN source_hash TEXT;
CREATE INDEX idx_documents_source_hash ON documents(source_hash);
"""

# Forward-only migration list. Index = (version - 1). Append, never edit.
MIGRATIONS: list[str] = [SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4]


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open a SQLite connection with Parsem's PRAGMAs applied.

    journal_mode=WAL is silently downgraded to "memory" on :memory:
    databases — that's a SQLite invariant, not a bug here. File-backed
    paths get true WAL.

    check_same_thread=False because FastAPI's TestClient and uvicorn run
    request handlers in worker threads while the connection is opened on
    the main thread. WAL + serialized writes (the sqlite3 module's
    default) keep this safe for our single-process usage.
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def migrate(conn: sqlite3.Connection) -> None:
    """Apply any pending migrations. Idempotent — re-running on a
    current database is a no-op (verified by ISC-17).
    """
    from datetime import UTC, datetime

    current = _current_version(conn)
    for i, sql in enumerate(MIGRATIONS, start=1):
        if i > current:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (i, datetime.now(UTC).isoformat()),
            )
            conn.commit()
