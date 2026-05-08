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

# Forward-only migration list. Index = (version - 1). Append, never edit.
MIGRATIONS: list[str] = [SCHEMA_V1]


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
