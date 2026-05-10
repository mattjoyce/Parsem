"""Tests for the ingest pipeline (claude-mwx.1).

The synchronous /upload route was replaced by async POST /ingest +
filesystem-watcher; tests here cover the route surface (write to
inbound/raw/, content-type dispatch, redirect/JSON shapes) and the
watcher's process_file core. Round-trip tests use process_file
directly to avoid threading the watcher into the test setup.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from parsem.cli import RESUME_WARM_CHUNKS_DEFAULT
from parsem.ingest.url_fetch import FetchedFile
from parsem.ingest.watcher import process_file
from parsem.store.db import connect, migrate
from parsem.store.documents import load_document
from parsem.web.app import create_app
from parsem.web.state import build_reader_state_for_document, empty_reader_state


@pytest.fixture
def fresh_app(tmp_path: Path) -> Iterator[tuple[TestClient, sqlite3.Connection, Path]]:
    """Empty in-memory DB + tmp library dir. Watcher disabled so the
    suite stays deterministic — tests call process_file() directly."""
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
        enable_watcher=False,
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


# Watcher integration — process_file is the synchronous test seam
# ─────────────────────────────────────────────────────────────


def test_process_file_ingests_md_and_moves_to_originals(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Drop a .md into raw/, call process_file: doc row inserted,
    chunks parsed, file moved to originals/<doc_id>.md."""
    client, conn, originals = fresh_app
    raw = originals.parent / "inbound" / "raw"
    src = raw / "drop.md"
    src.write_text("# Title\n\nBody paragraph.\n", encoding="utf-8")
    doc_id = process_file(src, conn=conn, originals_dir=originals)
    assert doc_id is not None
    assert not src.exists()  # moved
    assert (originals / f"{doc_id}.md").exists()
    doc = load_document(conn, doc_id)
    assert doc is not None
    assert doc.title == "drop"


def test_process_file_skips_non_md(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Non-.md files are left alone (cycle 2 will route .pdf to Marker)."""
    client, conn, originals = fresh_app
    raw = originals.parent / "inbound" / "raw"
    src = raw / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 ...")
    result = process_file(src, conn=conn, originals_dir=originals)
    assert result is None
    assert src.exists()  # untouched


def test_process_file_marks_empty_md_as_failed(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """An empty .md still inserts a doc row (so the user can see the
    failure in the library) but flips status to 'failed'."""
    client, conn, originals = fresh_app
    raw = originals.parent / "inbound" / "raw"
    src = raw / "empty.md"
    src.write_text("", encoding="utf-8")
    doc_id = process_file(src, conn=conn, originals_dir=originals)
    assert doc_id is not None
    doc = load_document(conn, doc_id)
    assert doc is not None
    assert doc.status == "failed"


def test_dropped_doc_round_trips_through_reader(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """End-to-end: drop file → process_file → /documents/{id}/reader
    serves the chunked document."""
    client, conn, originals = fresh_app
    raw = originals.parent / "inbound" / "raw"
    src = raw / "hello.md"
    src.write_text("# Hello\n\nThis is the body.\n", encoding="utf-8")
    doc_id = process_file(src, conn=conn, originals_dir=originals)
    response = client.get(f"/documents/{doc_id}/reader")
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
    doc_id = process_file(src, conn=conn, originals_dir=originals)
    assert doc_id is not None
    app = create_app(
        empty_reader_state(conn),
        db=conn,
        originals_dir=originals,
        inbound_raw_dir=raw,
        enable_watcher=False,
    )
    with TestClient(app) as client:
        client.get(f"/documents/{doc_id}/reader")  # opens; reader state points here
        client.post("/pin")  # pins current_position with color 1

    state = build_reader_state_for_document(
        _open_db(), document_id=doc_id, warm_chunks=RESUME_WARM_CHUNKS_DEFAULT
    )
    assert state is not None
    assert state.pin_colors == {0: 1}
