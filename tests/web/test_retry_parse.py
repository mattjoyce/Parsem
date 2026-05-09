"""Tests for POST /documents/{id}/retry-parse and the failed-status row.

Spec §17.2, §22; bead Parsem-pnk.

Re-runs the parse pipeline on the persisted original. Successful
re-parse flips status back to ready and re-populates chunks/sections.
A re-failure stays 'failed' with the new reason.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem.store.db import connect, migrate
from parsem.store.documents import insert_document, load_document
from parsem.web.app import create_app
from parsem.web.state import empty_reader_state

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def app_ctx(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, sqlite3.Connection, Path]]:
    conn = connect(":memory:")
    migrate(conn)
    originals = tmp_path / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    app = create_app(empty_reader_state(conn), db=conn, originals_dir=originals)
    with TestClient(app) as client:
        yield client, conn, originals


def _seed_failed(
    conn: sqlite3.Connection, originals: Path, *, body: str = "valid body"
) -> int:
    """Insert a document in 'failed' state and write a corresponding
    .md file so retry-parse has something to chew on."""
    doc_id = insert_document(
        conn,
        title="failed-doc",
        original_path="placeholder",
        status="failed",
        failure_reason="Original parse failed.",
        now=T0,
    )
    file_path = originals / f"{doc_id}.md"
    file_path.write_text(f"# Heading\n\n{body}\n", encoding="utf-8")
    conn.execute(
        "UPDATE documents SET original_path=? WHERE id=?",
        (str(file_path), doc_id),
    )
    conn.commit()
    return doc_id


def test_retry_redirects_to_library(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    doc_id = _seed_failed(conn, originals)
    response = client.post(
        f"/documents/{doc_id}/retry-parse", follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/library"


def test_retry_success_flips_status_to_ready(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    doc_id = _seed_failed(conn, originals)
    client.post(f"/documents/{doc_id}/retry-parse")
    doc = load_document(conn, doc_id)
    assert doc is not None
    assert doc.status == "ready"
    assert doc.failure_reason is None
    assert doc.total_chunks is not None and doc.total_chunks >= 1


def test_retry_success_inserts_chunks_and_sections(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    doc_id = _seed_failed(conn, originals)
    client.post(f"/documents/{doc_id}/retry-parse")
    chunks_count = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id=?", (doc_id,)
    ).fetchone()[0]
    sections_count = conn.execute(
        "SELECT COUNT(*) FROM sections WHERE document_id=?", (doc_id,)
    ).fetchone()[0]
    assert chunks_count >= 1
    assert sections_count >= 1


def test_retry_wipes_prior_chunks_and_sections(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Pretend a prior attempt left partial rows; retry must clear them
    so chunk positions are unique under the (document_id, position)
    UNIQUE constraint."""
    client, conn, originals = app_ctx
    doc_id = _seed_failed(conn, originals)
    conn.execute(
        "INSERT INTO chunks (document_id, position, source_offset_start,"
        " source_offset_end, text, lead_token_type, estimated_read_seconds,"
        " created_at) VALUES (?, 0, 0, 1, 'stale', 'paragraph', 1.0, ?)",
        (doc_id, T0.isoformat()),
    )
    conn.commit()
    client.post(f"/documents/{doc_id}/retry-parse")
    stale = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id=? AND text='stale'",
        (doc_id,),
    ).fetchone()[0]
    assert stale == 0


def test_retry_with_empty_markdown_marks_failed_with_empty_reason(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    doc_id = _seed_failed(conn, originals)
    (originals / f"{doc_id}.md").write_text("   \n", encoding="utf-8")
    client.post(f"/documents/{doc_id}/retry-parse")
    doc = load_document(conn, doc_id)
    assert doc is not None
    assert doc.status == "failed"
    assert doc.failure_reason == "Document is empty."


def test_retry_when_original_file_missing_marks_failed(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    doc_id = _seed_failed(conn, originals)
    (originals / f"{doc_id}.md").unlink()
    client.post(f"/documents/{doc_id}/retry-parse")
    doc = load_document(conn, doc_id)
    assert doc is not None
    assert doc.status == "failed"
    assert doc.failure_reason == "Original file missing."


def test_retry_unknown_id_returns_404(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = app_ctx
    response = client.post("/documents/999/retry-parse")
    assert response.status_code == 404


def test_failed_row_renders_retry_button_and_reason(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    doc_id = _seed_failed(conn, originals)
    body = client.get("/library").text
    assert f'action="/documents/{doc_id}/retry-parse"' in body
    assert "library-retry" in body
    assert "library-failure-reason" in body
    assert "Original parse failed." in body


def test_failed_row_does_not_render_rename_button(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Rename only makes sense for ready docs; the failed branch shows
    Retry instead of Rename."""
    client, conn, originals = app_ctx
    doc_id = _seed_failed(conn, originals)
    body = client.get("/library").text
    # Find this doc's row and assert there's no rename button inside.
    row_marker = f'id="library-row-{doc_id}"'
    row_idx = body.index(row_marker)
    next_row_idx = body.find('class="library-row"', row_idx + 1)
    row_html = body[row_idx : next_row_idx if next_row_idx != -1 else len(body)]
    assert "library-rename" not in row_html


def test_ready_row_does_not_render_retry_button(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = app_ctx
    insert_document(
        conn,
        title="ready-doc",
        original_path="data/originals/x.md",
        status="ready",
        now=T0,
    )
    body = client.get("/library").text
    assert "library-retry" not in body
