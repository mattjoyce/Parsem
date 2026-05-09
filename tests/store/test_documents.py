"""Tests for parsem.store.documents. Spec: §17.1, §21; bead Parsem-crk."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from parsem.domain.materialize import Chunk, Section
from parsem.store.db import connect, migrate
from parsem.store.documents import (
    Document,
    insert_chunks_and_sections,
    insert_document,
    load_chunks_for_document,
    load_document,
    load_sections_for_document,
)
from tests.conftest import T0, chunk_via_substrate

WELCOME = Path(__file__).resolve().parents[2] / "data" / "welcome.md"


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


@pytest.fixture
def doc_id(db: sqlite3.Connection) -> int:
    return insert_document(
        db,
        title="welcome",
        original_path="data/originals/1.md",
        status="ready",
        total_chunks=35,
        now=T0,
    )


def _welcome_chunks_and_sections() -> tuple[list[Chunk], list[Section]]:
    return chunk_via_substrate(WELCOME.read_text(encoding="utf-8"))


def _strip_substrate_extras(c: Chunk) -> Chunk:
    """Reset the substrate-only fields (text_hash, line/column spans,
    piece_ordinals) to their dataclass defaults. The legacy
    `insert_chunks_and_sections` persistence path doesn't carry those
    fields through; comparing on equality after a round-trip would
    fail spuriously without this strip. Modern persistence
    (`insert_chunking_artifacts`) does carry them; tests of that path
    assert on the substrate fields directly."""
    from dataclasses import replace

    return replace(
        c,
        text_hash="",
        start_line=0,
        end_line=0,
        start_column=0,
        end_column=0,
        piece_ordinals=[],
    )


def test_insert_document_returns_new_id(db: sqlite3.Connection) -> None:
    new_id = insert_document(
        db, title="t", original_path="p", status="ready", now=T0
    )
    assert new_id == 1


def test_insert_document_persists_core_fields(db: sqlite3.Connection) -> None:
    new_id = insert_document(
        db, title="welcome", original_path="data/originals/1.md", status="ready", now=T0
    )
    row = db.execute("SELECT * FROM documents WHERE id=?", (new_id,)).fetchone()
    assert row["title"] == "welcome"
    assert row["original_path"] == "data/originals/1.md"
    assert row["status"] == "ready"


def test_insert_document_persists_total_chunks_and_timestamps(
    db: sqlite3.Connection,
) -> None:
    new_id = insert_document(
        db, title="t", original_path="p", status="ready", total_chunks=42, now=T0
    )
    row = db.execute("SELECT * FROM documents WHERE id=?", (new_id,)).fetchone()
    assert row["total_chunks"] == 42
    assert row["created_at"] == T0.isoformat()
    assert row["updated_at"] == T0.isoformat()


def test_insert_chunks_inserts_in_position_order(
    db: sqlite3.Connection, doc_id: int
) -> None:
    chunks, sections = _welcome_chunks_and_sections()
    insert_chunks_and_sections(
        db, document_id=doc_id, chunks=chunks, sections=sections, now=T0
    )
    positions = [
        row["position"]
        for row in db.execute(
            "SELECT position FROM chunks WHERE document_id=? ORDER BY id", (doc_id,)
        )
    ]
    assert positions == list(range(len(chunks)))


def test_insert_chunks_round_trip_via_load_chunks(
    db: sqlite3.Connection, doc_id: int
) -> None:
    chunks, sections = _welcome_chunks_and_sections()
    insert_chunks_and_sections(
        db, document_id=doc_id, chunks=chunks, sections=sections, now=T0
    )
    loaded = load_chunks_for_document(db, doc_id)
    assert loaded == [_strip_substrate_extras(c) for c in chunks]


def test_insert_sections_resolve_heading_chunk_id(
    db: sqlite3.Connection, doc_id: int
) -> None:
    chunks, sections = _welcome_chunks_and_sections()
    insert_chunks_and_sections(
        db, document_id=doc_id, chunks=chunks, sections=sections, now=T0
    )
    rows = db.execute(
        "SELECT s.heading_chunk_id, c.position FROM sections s"
        " LEFT JOIN chunks c ON c.id=s.heading_chunk_id"
        " WHERE s.document_id=?",
        (doc_id,),
    ).fetchall()
    for row, sec in zip(rows, sections, strict=True):
        if sec.heading_chunk_position is None:
            assert row["heading_chunk_id"] is None
        else:
            assert row["position"] == sec.heading_chunk_position


def test_chunks_section_id_populated_by_position_range(
    db: sqlite3.Connection, doc_id: int
) -> None:
    chunks, sections = _welcome_chunks_and_sections()
    insert_chunks_and_sections(
        db, document_id=doc_id, chunks=chunks, sections=sections, now=T0
    )
    for sec in sections:
        rows = db.execute(
            "SELECT section_id FROM chunks WHERE document_id=?"
            " AND position BETWEEN ? AND ?",
            (doc_id, sec.start_chunk_position, sec.end_chunk_position),
        ).fetchall()
        section_ids = {row["section_id"] for row in rows}
        assert len(section_ids) == 1, f"section spans should share section_id; got {section_ids}"
        assert next(iter(section_ids)) is not None


def test_insert_chunks_is_atomic(db: sqlite3.Connection, doc_id: int) -> None:
    """If any insert in the batch fails, none commit."""
    bad_chunk = Chunk(
        position=0,
        source_offset_start=0,
        source_offset_end=5,
        text="x",
        lead_token_type="paragraph",
        lead_heading_level=None,
        estimated_read_seconds=1.0,
    )
    duplicate_chunk = Chunk(  # same position → UNIQUE constraint violation
        position=0,
        source_offset_start=5,
        source_offset_end=10,
        text="y",
        lead_token_type="paragraph",
        lead_heading_level=None,
        estimated_read_seconds=1.0,
    )
    with pytest.raises(sqlite3.IntegrityError):
        insert_chunks_and_sections(
            db,
            document_id=doc_id,
            chunks=[bad_chunk, duplicate_chunk],
            sections=[],
            now=T0,
        )
    count = db.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id=?", (doc_id,)
    ).fetchone()[0]
    assert count == 0  # rollback erased the partial insert


def test_load_document_returns_full_record(db: sqlite3.Connection, doc_id: int) -> None:
    loaded = load_document(db, doc_id)
    assert isinstance(loaded, Document)
    assert loaded.id == doc_id
    assert loaded.title == "welcome"
    assert loaded.status == "ready"
    assert loaded.total_chunks == 35
    assert loaded.created_at == T0


def test_load_document_returns_none_for_missing_id(db: sqlite3.Connection) -> None:
    assert load_document(db, 9999) is None


def test_load_chunks_orders_by_position(db: sqlite3.Connection, doc_id: int) -> None:
    chunks, sections = _welcome_chunks_and_sections()
    insert_chunks_and_sections(
        db, document_id=doc_id, chunks=chunks, sections=sections, now=T0
    )
    loaded = load_chunks_for_document(db, doc_id)
    assert [c.position for c in loaded] == list(range(len(chunks)))


def test_load_chunks_round_trips_chunk_dataclass(
    db: sqlite3.Connection, doc_id: int
) -> None:
    chunks, sections = _welcome_chunks_and_sections()
    insert_chunks_and_sections(
        db, document_id=doc_id, chunks=chunks, sections=sections, now=T0
    )
    loaded = load_chunks_for_document(db, doc_id)
    assert loaded[0] == _strip_substrate_extras(chunks[0])
    assert loaded[-1] == _strip_substrate_extras(chunks[-1])


def test_load_sections_orders_by_start_position(
    db: sqlite3.Connection, doc_id: int
) -> None:
    chunks, sections = _welcome_chunks_and_sections()
    insert_chunks_and_sections(
        db, document_id=doc_id, chunks=chunks, sections=sections, now=T0
    )
    loaded = load_sections_for_document(db, doc_id)
    starts = [s.start_chunk_position for s in loaded]
    assert starts == sorted(starts)


def test_load_sections_round_trips_section_dataclass(
    db: sqlite3.Connection, doc_id: int
) -> None:
    chunks, sections = _welcome_chunks_and_sections()
    insert_chunks_and_sections(
        db, document_id=doc_id, chunks=chunks, sections=sections, now=T0
    )
    loaded = load_sections_for_document(db, doc_id)
    assert loaded == sections


def test_load_sections_resolves_heading_chunk_position(
    db: sqlite3.Connection, doc_id: int
) -> None:
    chunks, sections = _welcome_chunks_and_sections()
    insert_chunks_and_sections(
        db, document_id=doc_id, chunks=chunks, sections=sections, now=T0
    )
    loaded = load_sections_for_document(db, doc_id)
    for original, restored in zip(sections, loaded, strict=True):
        assert restored.heading_chunk_position == original.heading_chunk_position


def test_welcome_full_round_trip(db: sqlite3.Connection, doc_id: int) -> None:
    chunks, sections = _welcome_chunks_and_sections()
    insert_chunks_and_sections(
        db, document_id=doc_id, chunks=chunks, sections=sections, now=T0
    )
    assert load_chunks_for_document(db, doc_id) == [
        _strip_substrate_extras(c) for c in chunks
    ]
    assert load_sections_for_document(db, doc_id) == sections


def test_mark_document_ready_sets_status_and_total_chunks(
    db: sqlite3.Connection, doc_id: int
) -> None:
    from datetime import timedelta

    from parsem.store.documents import mark_document_ready

    later = T0 + timedelta(seconds=5)
    mark_document_ready(db, doc_id, total_chunks=42, now=later)
    doc = load_document(db, doc_id)
    assert doc is not None
    assert doc.status == "ready"
    assert doc.total_chunks == 42
    assert doc.failure_reason is None
    assert doc.updated_at == later


def test_mark_document_failed_sets_status_and_reason(db: sqlite3.Connection, doc_id: int) -> None:
    from datetime import timedelta

    from parsem.store.documents import mark_document_failed

    later = T0 + timedelta(seconds=5)
    mark_document_failed(db, doc_id, reason="Document is empty.", now=later)
    doc = load_document(db, doc_id)
    assert doc is not None
    assert doc.status == "failed"
    assert doc.failure_reason == "Document is empty."
    assert doc.updated_at == later


def test_mark_document_ready_clears_prior_failure_reason(
    db: sqlite3.Connection, doc_id: int
) -> None:
    from datetime import timedelta

    from parsem.store.documents import mark_document_failed, mark_document_ready

    mark_document_failed(db, doc_id, reason="oops", now=T0 + timedelta(seconds=1))
    mark_document_ready(db, doc_id, total_chunks=3, now=T0 + timedelta(seconds=2))
    doc = load_document(db, doc_id)
    assert doc is not None
    assert doc.status == "ready"
    assert doc.failure_reason is None


def test_documents_module_does_not_import_from_web() -> None:
    import ast

    tree = ast.parse(Path("parsem/store/documents.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("parsem.web"), (
                f"forbidden import: from {node.module}"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("parsem.web"), (
                    f"forbidden import: {alias.name}"
                )
