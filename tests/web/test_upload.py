"""Tests for upload route. Spec §17.1, §17.2, §22; bead Parsem-cwj."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem.cli import RESUME_WARM_CHUNKS_DEFAULT
from parsem.store.db import connect, migrate
from parsem.store.documents import load_document
from parsem.web.app import create_app
from parsem.web.state import build_reader_state_for_document, empty_reader_state


def _build_empty_app(
    conn: sqlite3.Connection, originals: Path
) -> TestClient:
    """Tests that exercise upload from a no-doc-open starting point —
    fresh DB, placeholder ReaderState, TestClient ready to POST /upload."""
    app = create_app(empty_reader_state(conn), db=conn, originals_dir=originals)
    return TestClient(app)


@pytest.fixture
def fresh_app(tmp_path: Path) -> Iterator[tuple[TestClient, sqlite3.Connection, Path]]:
    """Empty in-memory DB, no welcome doc, no chunks. Tests that hit
    /upload start from a clean slate so the assigned doc id is
    predictable (always 1)."""
    conn = connect(":memory:")
    migrate(conn)
    originals = tmp_path / "originals"
    with _build_empty_app(conn, originals) as client:
        yield client, conn, originals


def test_get_upload_returns_200_with_form(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = fresh_app
    response = client.get("/upload")
    assert response.status_code == 200
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="file"' in response.text


def test_root_redirects_to_upload(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = fresh_app
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/upload"


def test_post_upload_persists_file_to_originals_dir(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, originals = fresh_app
    md = b"# Title\n\nA paragraph of text to read.\n"
    response = client.post(
        "/upload",
        files={"file": ("notes.md", md, "text/markdown")},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/documents/1/reader"
    assert (originals / "1.md").read_bytes() == md


def test_post_upload_marks_document_ready_with_total_chunks(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = fresh_app
    md = b"# Title\n\nA paragraph of text to read.\n"
    client.post("/upload", files={"file": ("notes.md", md, "text/markdown")})
    doc = load_document(conn, document_id=1)
    assert doc is not None
    assert doc.status == "ready"
    assert doc.total_chunks is not None
    assert doc.total_chunks >= 1


def test_post_upload_default_title_is_filename_stem(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = fresh_app
    md = b"# Title\n\nbody\n"
    client.post("/upload", files={"file": ("My Notes.md", md, "text/markdown")})
    doc = load_document(conn, document_id=1)
    assert doc is not None
    assert doc.title == "My Notes"


def test_post_upload_empty_markdown_marks_failed_with_reason(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = fresh_app
    response = client.post(
        "/upload",
        files={"file": ("empty.md", b"", "text/markdown")},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/"
    doc = load_document(conn, document_id=1)
    assert doc is not None
    assert doc.status == "failed"
    assert doc.failure_reason == "Document is empty."


def test_post_upload_whitespace_only_markdown_treated_as_empty(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = fresh_app
    client.post(
        "/upload",
        files={"file": ("blank.md", b"   \n   \n", "text/markdown")},
    )
    doc = load_document(conn, document_id=1)
    assert doc is not None
    assert doc.status == "failed"
    assert doc.failure_reason == "Document is empty."


def test_post_upload_rejects_non_md_extension(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = fresh_app
    response = client.post(
        "/upload",
        files={"file": ("notes.txt", b"# heading", "text/plain")},
    )
    assert response.status_code == 400


def test_post_upload_with_no_file_returns_400(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = fresh_app
    response = client.post("/upload")
    assert response.status_code in (400, 422)


def test_post_upload_invalid_utf8_returns_400(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = fresh_app
    response = client.post(
        "/upload",
        files={"file": ("bad.md", b"\xff\xfe invalid utf8 \xff", "text/markdown")},
    )
    assert response.status_code == 400


def test_uploaded_doc_round_trips_through_reader(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """End-to-end: upload → 302 → GET /documents/{id}/reader → 200 HTML
    with the doc's first chunk visible."""
    client, _, _ = fresh_app
    md = b"# Hello\n\nThis is the body of the document.\n"
    upload_resp = client.post(
        "/upload",
        files={"file": ("hello.md", md, "text/markdown")},
        follow_redirects=False,
    )
    target = upload_resp.headers["location"]
    reader_resp = client.get(target)
    assert reader_resp.status_code == 200
    assert "<html" in reader_resp.text


def test_get_unknown_document_returns_404(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = fresh_app
    response = client.get("/documents/999/reader")
    assert response.status_code == 404


def test_uploaded_doc_persists_pin_across_app_rebuild(tmp_path: Path) -> None:
    """The pins projection trio earns its keep here: upload → pin →
    rebuild app on same DB → pin still visible."""
    db_path = tmp_path / "parsem.db"
    originals = tmp_path / "originals"
    md = b"# Hello\n\nFirst paragraph.\n\nSecond paragraph.\n"

    def _open_db() -> sqlite3.Connection:
        conn = connect(str(db_path))
        migrate(conn)
        return conn

    # First boot: upload + open + pin
    with _build_empty_app(_open_db(), originals) as client:
        upload_resp = client.post(
            "/upload",
            files={"file": ("hello.md", md, "text/markdown")},
            follow_redirects=False,
        )
        assert upload_resp.status_code == 302
        target = upload_resp.headers["location"]
        client.get(target)  # opens the doc; ReaderState now points to it
        client.post("/pin")  # pins current_position (0) with color 1

    # Second boot: re-open the same doc, pin should still be there
    state = build_reader_state_for_document(
        _open_db(), document_id=1, warm_chunks=RESUME_WARM_CHUNKS_DEFAULT
    )
    assert state is not None
    assert state.pin_colors == {0: 1}
