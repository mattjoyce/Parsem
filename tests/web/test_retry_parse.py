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

from parsem.ingest import layout
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
    file_path = layout.markdown_path(originals, doc_id)
    file_path.parent.mkdir(parents=True, exist_ok=True)
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
    layout.markdown_path(originals, doc_id).write_text("   \n", encoding="utf-8")
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
    layout.markdown_path(originals, doc_id).unlink()
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
    """Rename (and Re-chunk) only make sense for ready docs; the failed
    branch shows Retry instead."""
    client, conn, originals = app_ctx
    doc_id = _seed_failed(conn, originals)
    row_html = _row_html(client.get("/library").text, doc_id)
    assert "library-rename" not in row_html
    assert "library-rechunk" not in row_html
    assert "library-retry" in row_html


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


# ─── Re-chunk: the "Re-chunk" button + the retry-parse endpoint on a
#     ready doc + the original_path fallback (claude-m4l) ──────────────


def test_ready_row_renders_rechunk_button(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    doc_id = _seed_ready_with_state(
        conn, originals, body="# T\n\nA paragraph.\n", current=0, high_water=0
    )
    row_html = _row_html(client.get("/library").text, doc_id)
    assert "library-rechunk" in row_html
    assert f'action="/documents/{doc_id}/retry-parse"' in row_html


def test_retry_parse_rechunks_a_ready_doc_keeping_it_ready(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    doc_id = _seed_ready_with_state(
        conn, originals, body="# T\n\nA paragraph.\n\nAnother one.\n", current=0, high_water=0
    )
    client.post(f"/documents/{doc_id}/retry-parse")
    doc = load_document(conn, doc_id)
    assert doc is not None
    assert doc.status == "ready"
    chunk_count = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id=?", (doc_id,)
    ).fetchone()[0]
    assert chunk_count >= 1


def test_reparse_document_falls_back_to_original_path(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """A doc whose source is a repo-relative path with no
    originals/<id>/document.md (the welcome-doc shape) re-chunks via the
    recorded original_path rather than failing."""
    from parsem.cli import WELCOME_ORIGINAL_PATH
    from parsem.web.ingest import reparse_document

    _, conn, originals = app_ctx
    doc_id = insert_document(
        conn,
        title="welcome-style",
        original_path=WELCOME_ORIGINAL_PATH,  # "data/welcome.md", relative to repo root
        status="processing",
        now=T0,
    )
    # No originals/<id>/document.md on disk — the fallback must kick in.
    assert reparse_document(conn, document_id=doc_id, originals_dir=originals, now=T0) is True
    doc = load_document(conn, doc_id)
    assert doc is not None and doc.status == "ready"
    assert doc.total_chunks is not None and doc.total_chunks >= 1


def _row_html(library_html: str, doc_id: int) -> str:
    """Slice out one document's full `<article>…</article>` tile from
    the library HTML. Migrated from row to tile markup in Parsem-7wu.2
    (ADR 0005)."""
    start = library_html.index(f'<article id="library-tile-{doc_id}"')
    end = library_html.index("</article>", start) + len("</article>")
    return library_html[start:end]


# ─── Reading-state re-anchor on new chunking_run (claude-jtu) ─────────


def _seed_ready_with_state(
    conn: sqlite3.Connection,
    originals: Path,
    *,
    body: str,
    current: int,
    high_water: int,
) -> int:
    """Insert a 'ready' document with a real chunking_run AND a
    reading_state row at (current, high_water). For testing re-anchor
    on retry-parse — we need the substrate present before the wipe."""
    from parsem.web.ingest import parse_and_persist

    doc_id = insert_document(
        conn,
        title="ready-with-state",
        original_path="placeholder",
        status="failed",
        now=T0,
    )
    file_path = layout.markdown_path(originals, doc_id)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(body, encoding="utf-8")
    conn.execute(
        "UPDATE documents SET original_path=? WHERE id=?",
        (str(file_path), doc_id),
    )
    conn.commit()
    assert parse_and_persist(conn, document_id=doc_id, text=body, now=T0)
    conn.execute(
        "INSERT INTO reading_state (document_id, current_position,"
        " high_water_position, updated_at) VALUES (?, ?, ?, ?)",
        (doc_id, current, high_water, T0.isoformat()),
    )
    conn.commit()
    return doc_id


def _read_reading_state(conn: sqlite3.Connection, doc_id: int) -> tuple[int, int]:
    row = conn.execute(
        "SELECT current_position, high_water_position FROM reading_state"
        " WHERE document_id=?",
        (doc_id,),
    ).fetchone()
    assert row is not None
    return row["current_position"], row["high_water_position"]


def test_retry_preserves_reading_state_when_content_unchanged(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Re-running the parse with byte-identical content gives an
    identical chunking — re-anchor should land every old position
    exactly on its new namesake. Reading state is preserved."""
    client, conn, originals = app_ctx
    body = (
        "# Title\n\n"
        "First paragraph here.\n\n"
        "Second paragraph here.\n\n"
        "## Section\n\n"
        "Third paragraph.\n\n"
        "Fourth paragraph.\n"
    )
    doc_id = _seed_ready_with_state(
        conn, originals, body=body, current=0, high_water=1
    )
    before = _read_reading_state(conn, doc_id)
    client.post(f"/documents/{doc_id}/retry-parse")
    after = _read_reading_state(conn, doc_id)
    assert after == before, (
        f"identical-content re-parse should preserve reading state; "
        f"before={before} after={after}"
    )


def test_retry_with_no_reading_state_is_silent_noop(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """A doc whose user has never opened it has no reading_state row.
    Re-anchor must NOT create one — it's a no-op for that case
    (matches the spec §18.5 invariant: reading_state is materialised
    on first GET /reader, not earlier)."""
    client, conn, originals = app_ctx
    doc_id = _seed_failed(conn, originals)
    client.post(f"/documents/{doc_id}/retry-parse")
    row = conn.execute(
        "SELECT 1 FROM reading_state WHERE document_id=?", (doc_id,)
    ).fetchone()
    assert row is None


def test_retry_with_no_prior_chunks_does_not_crash(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """A doc that's never successfully parsed has no chunks to
    capture pre-wipe — old_chunk_piece_hashes is empty and
    re-anchor short-circuits. Sanity: this path doesn't blow up
    even when reading_state happens to exist (defensive)."""
    client, conn, originals = app_ctx
    doc_id = _seed_failed(conn, originals)
    # Inject a stray reading_state row (shouldn't happen normally
    # without chunks, but defends against drift).
    conn.execute(
        "INSERT INTO reading_state (document_id, current_position,"
        " high_water_position, updated_at) VALUES (?, 0, 0, ?)",
        (doc_id, T0.isoformat()),
    )
    conn.commit()
    client.post(f"/documents/{doc_id}/retry-parse")
    # Reading state row still exists, untouched by re-anchor (no
    # old chunks to anchor from).
    row = conn.execute(
        "SELECT current_position, high_water_position FROM reading_state"
        " WHERE document_id=?",
        (doc_id,),
    ).fetchone()
    assert row is not None
    assert row["current_position"] == 0
    assert row["high_water_position"] == 0


def test_retry_clamps_current_to_high_water_on_inversion(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Defensive clamp: even if the re-anchor produces new_current
    greater than new_high_water (pathological strategy change),
    current is pulled back to high_water. Same body re-parse can't
    actually trigger this in practice, so this test sets up the
    inverted state directly and asserts the clamp via the primitive."""
    from parsem.store.projections_cache import reanchor_reading_state

    _client, conn, originals = app_ctx
    body = (
        "# Title\n\n"
        "First paragraph here.\n\n"
        "Second paragraph here.\n\n"
        "## Section\n\n"
        "Third paragraph.\n\n"
        "Fourth paragraph.\n"
    )
    # Seed reading_state where current > high_water — invalid combo
    # but the right shape to verify the clamp. Both positions in
    # range (the body produces 2 chunks; current=1, hw=0 gives an
    # in-range inversion).
    doc_id = _seed_ready_with_state(
        conn, originals, body=body, current=1, high_water=0
    )
    # Re-anchor in-place against the SAME chunking_run (no wipe).
    # Old hashes equal new hashes → every position re-anchors to
    # itself → new_current=2, new_high_water=0 → clamp to 0.
    from parsem.store.projections_cache import (
        get_chunk_piece_hashes_for_document,
    )

    old_hashes = get_chunk_piece_hashes_for_document(conn, doc_id)
    reanchor_reading_state(
        conn,
        document_id=doc_id,
        old_chunks_piece_hashes=old_hashes,
        now=T0,
    )
    conn.commit()
    cur, hw = _read_reading_state(conn, doc_id)
    assert cur == hw == 0
