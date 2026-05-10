"""Tests for the ingest pipeline (claude-mwx.1 + claude-mwx.2).

POST /ingest writes drops to inbound/raw/; ductile then knocks the
arrivals endpoints to actually ingest. Tests here cover the route
surface (write to inbound/raw/, content-type dispatch, redirect/JSON
shapes) and round-trip via the synchronous arrivals core
(`process_raw_arrival`) to keep timing deterministic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from parsem.cli import RESUME_WARM_CHUNKS_DEFAULT
from parsem.ingest.arrivals import process_raw_arrival
from parsem.ingest.url_fetch import FetchedFile
from parsem.store.db import connect, migrate
from parsem.store.documents import load_document
from parsem.web.app import create_app
from parsem.web.state import build_reader_state_for_document, empty_reader_state


@pytest.fixture
def fresh_app(tmp_path: Path) -> Iterator[tuple[TestClient, sqlite3.Connection, Path]]:
    """Empty in-memory DB + tmp library dir. Tests call
    `process_raw_arrival` directly to simulate ductile's knock without
    needing the HTTP layer for round-trip assertions."""
    conn = connect(":memory:")
    migrate(conn)
    library = tmp_path / "library"
    originals = library / "originals"
    raw = library / "inbound" / "raw"
    for d in (originals, raw):
        d.mkdir(parents=True, exist_ok=True)
    app = create_app(
        empty_reader_state(conn),
        db=conn,
        originals_dir=originals,
        inbound_raw_dir=raw,
    )
    with TestClient(app) as client:
        yield client, conn, originals


def test_root_redirects_to_library(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = fresh_app
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/library"


def test_old_upload_route_returns_404(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """The synchronous /upload route was retired in claude-mwx.1."""
    client, _, _ = fresh_app
    assert client.get("/upload").status_code == 404
    assert client.post("/upload").status_code == 404


def test_post_ingest_file_writes_to_inbound_raw(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
    tmp_path: Path,
) -> None:
    """Form file upload lands in inbound/raw/ with a sanitized name
    and redirects to /library."""
    client, _, originals = fresh_app
    md = b"# Hello\n\nThe body.\n"
    response = client.post(
        "/ingest",
        files={"file": ("hello.md", md, "text/markdown")},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/library"
    raw_dir = originals.parent / "inbound" / "raw"
    assert (raw_dir / "hello.md").read_bytes() == md


def test_post_ingest_file_with_no_file_returns_400(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = fresh_app
    response = client.post("/ingest")
    assert response.status_code == 400


def test_post_ingest_url_writes_fetched_bytes_to_inbound_raw(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """JSON {url} POST → fetcher invoked → bytes written to inbound/raw/
    → 202 with the queued filename."""
    client, _, originals = fresh_app
    fetched = FetchedFile(content=b"# from-url\n", suggested_filename="article.md")
    with patch("parsem.web.routes.ingest.fetch", return_value=fetched):
        response = client.post(
            "/ingest", json={"url": "https://example.com/article.md"}
        )
    assert response.status_code == 202
    assert response.json() == {"queued": "article.md"}
    raw_dir = originals.parent / "inbound" / "raw"
    assert (raw_dir / "article.md").read_bytes() == b"# from-url\n"


def test_post_ingest_filename_collision_suffixes(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Re-submitting the same filename gets _2, _3 suffixes — keeps
    concurrent submissions from clobbering each other."""
    client, _, originals = fresh_app
    raw_dir = originals.parent / "inbound" / "raw"
    md = b"# x\n"
    for _ in range(3):
        client.post(
            "/ingest",
            files={"file": ("dup.md", md, "text/markdown")},
            follow_redirects=False,
        )
    assert (raw_dir / "dup.md").exists()
    assert (raw_dir / "dup_2.md").exists()
    assert (raw_dir / "dup_3.md").exists()


def test_post_ingest_sanitizes_filename(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Path traversal attempts get neutralized: any character outside
    [A-Za-z0-9._-] becomes '_'; path separators are stripped first."""
    client, _, originals = fresh_app
    raw_dir = originals.parent / "inbound" / "raw"
    response = client.post(
        "/ingest",
        files={"file": ("../../etc/passwd.md", b"x", "text/markdown")},
        follow_redirects=False,
    )
    assert response.status_code == 302
    # No file created outside raw_dir
    assert not (raw_dir.parent.parent / "passwd.md").exists()
    # Sanitized name lands in raw_dir
    assert any(p.name.endswith("passwd.md") for p in raw_dir.iterdir())


# Round-trip via the arrivals core
# ─────────────────────────────────────────────────────────────


def test_process_raw_arrival_ingests_md_and_moves_to_originals(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Drop a .md into raw/, call the arrivals core: doc row inserted,
    chunks parsed, file moved to originals/<doc_id>.md, action=ingested."""
    _client, conn, originals = fresh_app
    raw = originals.parent / "inbound" / "raw"
    src = raw / "drop.md"
    src.write_text("# Title\n\nBody paragraph.\n", encoding="utf-8")
    result = process_raw_arrival(src, conn=conn, originals_dir=originals)
    assert result.action == "ingested"
    assert result.document_id is not None
    assert not src.exists()  # moved
    assert (originals / f"{result.document_id}.md").exists()
    doc = load_document(conn, result.document_id)
    assert doc is not None
    assert doc.title == "drop"


def test_process_raw_arrival_pdf_stages_for_marker(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """A .pdf gets a converting row, moves to originals/<id>.pdf, and
    returns submit_to_marker so ductile knows to call Marker."""
    _client, conn, originals = fresh_app
    raw = originals.parent / "inbound" / "raw"
    src = raw / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 ...")
    result = process_raw_arrival(src, conn=conn, originals_dir=originals)
    assert result.action == "submit_to_marker"
    assert result.document_id is not None
    assert result.doc_id == str(result.document_id)
    assert result.source_path == str(originals / f"{result.document_id}.pdf")
    assert (originals / f"{result.document_id}.pdf").exists()
    doc = load_document(conn, result.document_id)
    assert doc is not None
    assert doc.status == "converting"
    assert doc.source_type == "pdf"


def test_process_raw_arrival_marks_empty_md_as_failed(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """An empty .md still inserts a doc row (so the user can see the
    failure in the library) but flips status to 'failed'."""
    _client, conn, originals = fresh_app
    raw = originals.parent / "inbound" / "raw"
    src = raw / "empty.md"
    src.write_text("", encoding="utf-8")
    result = process_raw_arrival(src, conn=conn, originals_dir=originals)
    assert result.action == "ingested"
    assert result.document_id is not None
    doc = load_document(conn, result.document_id)
    assert doc is not None
    assert doc.status == "failed"


def test_process_raw_arrival_dedups_on_content_hash(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Re-dropping the same bytes returns action=duplicate against the
    same document_id — no second ingest, no second row."""
    _client, conn, originals = fresh_app
    raw = originals.parent / "inbound" / "raw"
    body = "# Same\n\ncontents.\n"

    src1 = raw / "first.md"
    src1.write_text(body, encoding="utf-8")
    first = process_raw_arrival(src1, conn=conn, originals_dir=originals)

    src2 = raw / "second.md"
    src2.write_text(body, encoding="utf-8")
    second = process_raw_arrival(src2, conn=conn, originals_dir=originals)

    assert first.action == "ingested"
    assert second.action == "duplicate"
    assert second.document_id == first.document_id
    # Second drop is left in place for manual inspection
    assert src2.exists()


def test_process_raw_arrival_unsupported_records_failed_row(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Unknown extension → fail-row + action=unsupported. The user
    sees their drop in the library so it doesn't vanish."""
    _client, conn, originals = fresh_app
    raw = originals.parent / "inbound" / "raw"
    src = raw / "weird.xyz"
    src.write_bytes(b"\x00\x01\x02")
    result = process_raw_arrival(src, conn=conn, originals_dir=originals)
    assert result.action == "unsupported"
    assert result.document_id is not None
    doc = load_document(conn, result.document_id)
    assert doc is not None
    assert doc.status == "failed"


def test_dropped_doc_round_trips_through_reader(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """End-to-end: drop file → arrivals → /documents/{id}/reader
    serves the chunked document."""
    client, conn, originals = fresh_app
    raw = originals.parent / "inbound" / "raw"
    src = raw / "hello.md"
    src.write_text("# Hello\n\nThis is the body.\n", encoding="utf-8")
    result = process_raw_arrival(src, conn=conn, originals_dir=originals)
    assert result.document_id is not None
    response = client.get(f"/documents/{result.document_id}/reader")
    assert response.status_code == 200
    assert "<html" in response.text


def test_get_unknown_document_returns_404(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = fresh_app
    assert client.get("/documents/999/reader").status_code == 404


def test_dropped_doc_pin_persists_across_app_rebuild(tmp_path: Path) -> None:
    """End-to-end: drop file → pin → rebuild app on same DB → pin
    still visible. The persistence guarantee survives the route swap."""
    db_path = tmp_path / "parsem.db"
    library = tmp_path / "library"
    originals = library / "originals"
    raw = library / "inbound" / "raw"
    for d in (originals, raw):
        d.mkdir(parents=True, exist_ok=True)

    def _open_db() -> sqlite3.Connection:
        conn = connect(str(db_path))
        migrate(conn)
        return conn

    # First boot: drop + ingest + pin
    conn = _open_db()
    src = raw / "hello.md"
    src.write_text("# Hello\n\nFirst.\n\nSecond.\n", encoding="utf-8")
    result = process_raw_arrival(src, conn=conn, originals_dir=originals)
    assert result.document_id is not None
    app = create_app(
        empty_reader_state(conn),
        db=conn,
        originals_dir=originals,
        inbound_raw_dir=raw,
    )
    with TestClient(app) as client:
        client.get(f"/documents/{result.document_id}/reader")  # opens; reader state points here
        client.post("/pin")  # pins current_position with color 1

    state = build_reader_state_for_document(
        _open_db(), document_id=result.document_id, warm_chunks=RESUME_WARM_CHUNKS_DEFAULT
    )
    assert state is not None
    assert state.pin_colors == {0: 1}
