"""Tests for parsem.store.db. Spec: parsem-spec.md §19, §21; bead Parsem-q0t."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from parsem.store.db import MIGRATIONS, connect, migrate

EXPECTED_TABLES = {
    "documents",
    "sections",
    "chunks",
    "reading_events",
    "reading_state",
    "chunk_ratings",
    "pins",
    "pin_color_labels",
    "settings",
}

EXPECTED_INDEXES = {
    "idx_chunks_doc_pos",
    "idx_events_doc_created",
    "idx_pins_doc_color",
}


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    return {row["name"]: row for row in conn.execute(f"PRAGMA table_info({table})")}


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return row is not None


def test_connect_enables_foreign_keys() -> None:
    conn = connect(":memory:")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_connect_sets_wal_journal_on_file_backed_db(tmp_path: Path) -> None:
    conn = connect(tmp_path / "parsem.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_connect_sets_synchronous_normal() -> None:
    conn = connect(":memory:")
    # synchronous: 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_migrate_creates_documents_table(db: sqlite3.Connection) -> None:
    cols = _columns(db, "documents")
    assert "id" in cols
    assert "title" in cols
    assert "source_type" in cols
    assert "original_path" in cols
    assert "status" in cols
    assert "failure_reason" in cols
    assert "total_chunks" in cols
    assert "preference_overrides_json" in cols
    assert "created_at" in cols
    assert "updated_at" in cols


def test_migrate_creates_sections_with_fk_to_documents(db: sqlite3.Connection) -> None:
    assert _table_exists(db, "sections")
    fks = list(db.execute("PRAGMA foreign_key_list(sections)"))
    assert any(fk["table"] == "documents" for fk in fks)


def test_migrate_creates_chunks_with_unique_doc_position(db: sqlite3.Connection) -> None:
    assert _table_exists(db, "chunks")
    db.execute(
        "INSERT INTO documents (title, original_path, status, created_at, updated_at)"
        " VALUES ('t', 'p', 'ready', '2026-01-01', '2026-01-01')"
    )
    db.execute(
        "INSERT INTO chunks (document_id, position, source_offset_start, source_offset_end,"
        " text, lead_token_type, estimated_read_seconds, created_at)"
        " VALUES (1, 0, 0, 5, 'hi', 'paragraph', 1.0, '2026-01-01')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO chunks (document_id, position, source_offset_start, source_offset_end,"
            " text, lead_token_type, estimated_read_seconds, created_at)"
            " VALUES (1, 0, 5, 10, 'no', 'paragraph', 1.0, '2026-01-01')"
        )


def test_migrate_creates_reading_events_with_documents_fk(db: sqlite3.Connection) -> None:
    """reading_events has only the documents FK — chunks FK was dropped
    in v5l so chunk_id stores chunk POSITION, not chunks.id."""
    assert _table_exists(db, "reading_events")
    fks = list(db.execute("PRAGMA foreign_key_list(reading_events)"))
    referenced = {fk["table"] for fk in fks}
    assert referenced == {"documents"}


def test_migrate_creates_reading_state_table(db: sqlite3.Connection) -> None:
    cols = _columns(db, "reading_state")
    expected = {"document_id", "high_water_position", "current_position", "last_event_id_applied"}
    assert expected <= cols.keys()


def test_migrate_creates_chunk_ratings_table(db: sqlite3.Connection) -> None:
    cols = _columns(db, "chunk_ratings")
    assert {"chunk_id", "rating", "updated_at"} <= cols.keys()


def test_migrate_creates_pins_with_three_fks(db: sqlite3.Connection) -> None:
    assert _table_exists(db, "pins")
    fks = list(db.execute("PRAGMA foreign_key_list(pins)"))
    # documents + chunk_id_start + chunk_id_end → 3 references
    assert len(fks) == 3


def test_migrate_creates_pin_color_labels_table(db: sqlite3.Connection) -> None:
    cols = _columns(db, "pin_color_labels")
    assert {"color_id", "label"} <= cols.keys()


def test_settings_table_rejects_non_one_id(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO settings (id, config_json) VALUES (1, '{}')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO settings (id, config_json) VALUES (2, '{}')")


def test_idx_chunks_doc_pos_exists(db: sqlite3.Connection) -> None:
    assert _index_exists(db, "idx_chunks_doc_pos")


def test_idx_events_doc_created_exists(db: sqlite3.Connection) -> None:
    assert _index_exists(db, "idx_events_doc_created")


def test_idx_pins_doc_color_exists(db: sqlite3.Connection) -> None:
    assert _index_exists(db, "idx_pins_doc_color")


def test_schema_version_tracks_applied_migrations(db: sqlite3.Connection) -> None:
    versions = [
        row["version"]
        for row in db.execute("SELECT version FROM schema_version ORDER BY version")
    ]
    assert versions == list(range(1, len(MIGRATIONS) + 1))


def test_migrate_is_idempotent() -> None:
    conn = connect(":memory:")
    migrate(conn)
    migrate(conn)  # second call must not double-apply
    row = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
    assert row[0] == len(MIGRATIONS)


def test_documents_delete_cascades_to_children(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO documents (id, title, original_path, status, created_at, updated_at)"
        " VALUES (1, 't', 'p', 'ready', '2026-01-01', '2026-01-01')"
    )
    db.execute(
        "INSERT INTO sections (document_id, start_chunk_position, end_chunk_position)"
        " VALUES (1, 0, 0)"
    )
    db.execute(
        "INSERT INTO chunks (id, document_id, position, source_offset_start,"
        " source_offset_end, text, lead_token_type, estimated_read_seconds, created_at)"
        " VALUES (1, 1, 0, 0, 5, 'hi', 'paragraph', 1.0, '2026-01-01')"
    )
    db.execute(
        "INSERT INTO reading_events (document_id, chunk_id, event_type, created_at)"
        " VALUES (1, 1, 'reveal', '2026-01-01')"
    )
    db.execute(
        "INSERT INTO reading_state (document_id, updated_at) VALUES (1, '2026-01-01')"
    )
    db.execute(
        "INSERT INTO pins (document_id, chunk_id_start, chunk_id_end, color_id, created_at)"
        " VALUES (1, 1, 1, 1, '2026-01-01')"
    )

    db.execute("DELETE FROM documents WHERE id=1")

    for table in ("sections", "chunks", "reading_events", "reading_state", "pins"):
        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0, f"cascade failed for {table}"


# ─── v3 PDF-readiness hooks (claude-axx.7) ────────────────────────────


def test_v3_extraction_runs_table_exists(db: sqlite3.Connection) -> None:
    """v3 reserves a structural seam for non-markdown source ingest
    (PDF/epub/etc.). The table is empty for markdown-only ingest."""
    assert _table_exists(db, "extraction_runs")


def test_v3_extraction_runs_columns(db: sqlite3.Connection) -> None:
    cols = _columns(db, "extraction_runs")
    assert {"id", "document_id", "source_type", "extractor_name",
            "extractor_version", "source_path", "params_json",
            "created_at"} <= set(cols)


def test_v3_document_revisions_extraction_run_id_nullable(
    db: sqlite3.Connection,
) -> None:
    """Markdown ingest creates revisions with extraction_run_id NULL —
    revision IS the upload. The column must be nullable."""
    cols = _columns(db, "document_revisions")
    assert "extraction_run_id" in cols
    # PRAGMA table_info `notnull` is 0 when nullable, 1 when NOT NULL.
    assert cols["extraction_run_id"]["notnull"] == 0


def test_v3_atomic_pieces_external_anchor_json_nullable(
    db: sqlite3.Connection,
) -> None:
    """Non-markdown converters can stash source-shaped anchors here
    (pdf_page, pdf_y, epub_cfi). NULL for markdown pieces."""
    cols = _columns(db, "atomic_pieces")
    assert "external_anchor_json" in cols
    assert cols["external_anchor_json"]["notnull"] == 0


def test_v3_extraction_runs_cascades_on_document_delete(
    db: sqlite3.Connection,
) -> None:
    """Deleting a document drops its extraction_runs (FK ON DELETE
    CASCADE) — same lifecycle as document_revisions."""
    db.execute(
        "INSERT INTO documents (id, title, original_path, status, created_at, updated_at)"
        " VALUES (1, 't', 'p', 'ready', '2026-01-01', '2026-01-01')"
    )
    db.execute(
        "INSERT INTO extraction_runs (document_id, source_type, extractor_name,"
        " extractor_version, source_path, created_at)"
        " VALUES (1, 'pdf', 'pdftotext', '0.1.0', '/x.pdf', '2026-01-01')"
    )
    db.execute("DELETE FROM documents WHERE id=1")
    count = db.execute("SELECT COUNT(*) FROM extraction_runs").fetchone()[0]
    assert count == 0


def test_v3_revision_extraction_run_set_null_on_extraction_delete(
    db: sqlite3.Connection,
) -> None:
    """Deleting an extraction_run nulls the revision's link rather
    than cascading — the revision (= the converted markdown) survives
    even if the converter run record is purged."""
    db.execute(
        "INSERT INTO documents (id, title, original_path, status, created_at, updated_at)"
        " VALUES (1, 't', 'p', 'ready', '2026-01-01', '2026-01-01')"
    )
    db.execute(
        "INSERT INTO extraction_runs (id, document_id, source_type,"
        " extractor_name, extractor_version, source_path, created_at)"
        " VALUES (1, 1, 'pdf', 'pdftotext', '0.1.0', '/x.pdf', '2026-01-01')"
    )
    db.execute(
        "INSERT INTO document_revisions (id, document_id, full_text,"
        " content_hash, line_index_json, created_at, extraction_run_id)"
        " VALUES (1, 1, 'hi', 'h', '[]', '2026-01-01', 1)"
    )
    db.execute("DELETE FROM extraction_runs WHERE id=1")
    row = db.execute(
        "SELECT extraction_run_id FROM document_revisions WHERE id=1"
    ).fetchone()
    assert row["extraction_run_id"] is None


def test_db_module_does_not_import_from_web_or_domain() -> None:
    """db.py is the bottom of the dependency stack — it must not pull
    in anything from parsem.web or parsem.domain (would create a cycle
    once events.py / projections_cache.py land)."""
    import ast

    tree = ast.parse(Path("parsem/store/db.py").read_text(encoding="utf-8"))
    forbidden_prefixes = ("parsem.web", "parsem.domain")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(forbidden_prefixes), (
                f"forbidden import: from {node.module}"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(forbidden_prefixes), (
                    f"forbidden import: {alias.name}"
                )
