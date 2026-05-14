"""Tests for the ingest pipeline (claude-mwx.1 + claude-mwx.2 + claude-als + claude-5fp).

`POST /ingest` (multipart) handles file uploads; `.md` is self-ingested,
`.pdf` is queued for ductile/Marker. The legacy JSON `{url}` branch of
this route was retired in claude-5fp and now returns 410 Gone.

`POST /ingest/url` (claude-5fp) is the user-initiated URL submission
endpoint: inserts a `converting` row and POSTs to ductile's firecrawl
plugin via `submit_url`. ADR 0003.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from parsem.cli import RESUME_WARM_CHUNKS_DEFAULT
from parsem.config import DuctileSettings
from parsem.ingest import layout
from parsem.ingest.arrivals import process_raw_arrival
from parsem.ingest.ductile_client import DuctileError
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
    converted = library / "inbound" / "converted"
    for d in (originals, raw, converted):
        d.mkdir(parents=True, exist_ok=True)
    app = create_app(
        empty_reader_state(conn),
        db=conn,
        originals_dir=originals,
        inbound_raw_dir=raw,
        inbound_converted_dir=converted,
        ductile_settings=DuctileSettings(
            base_url="http://fake-ductile:8888", api_token=""
        ),
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


def test_post_ingest_file_self_ingests_and_creates_document(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Form file upload self-ingests (no ductile watcher needed): the
    .md becomes a `ready` document, gets moved into
    originals/<id>/document.md, leaves inbound/raw/ empty, and the
    response redirects to /library. claude-als."""
    client, conn, originals = fresh_app
    md = b"# Hello\n\nThe body.\n"
    response = client.post(
        "/ingest",
        files={"file": ("hello.md", md, "text/markdown")},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/library"
    doc = load_document(conn, 1)
    assert doc is not None
    assert doc.status == "ready"
    assert layout.markdown_path(originals, 1).read_bytes() == md
    raw_dir = originals.parent / "inbound" / "raw"
    assert not (raw_dir / "hello.md").exists()
    # And it shows up in the library listing.
    assert "hello" in client.get("/library").text


def test_post_ingest_file_with_bad_markdown_creates_failed_row(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """An upload that parses to nothing still produces a row (status
    `failed`) so the library shows it with a Retry button — the upload
    never silently vanishes."""
    client, conn, _ = fresh_app
    response = client.post(
        "/ingest",
        files={"file": ("empty.md", b"   \n\n  \n", "text/markdown")},
        follow_redirects=False,
    )
    assert response.status_code == 302
    doc = load_document(conn, 1)
    assert doc is not None
    assert doc.status == "failed"


def test_post_ingest_pdf_is_queued_not_self_ingested(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """A .pdf upload is left in inbound/raw/ for the watcher — NOT run
    through process_raw_arrival here, which would `submit_to_marker`
    (moving the PDF to originals/<id>/source.pdf) but leave nobody to
    dispatch Marker. claude-als."""
    client, conn, originals = fresh_app
    response = client.post(
        "/ingest",
        files={"file": ("doc.pdf", b"%PDF-1.4 ...", "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 302
    # No document row — not ingested, not staged.
    (count,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert count == 0
    # Still sitting in inbound/raw/ for ductile's folderwatch.
    raw_dir = originals.parent / "inbound" / "raw"
    assert (raw_dir / "doc.pdf").read_bytes() == b"%PDF-1.4 ..."


def test_post_ingest_file_with_no_file_returns_400(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = fresh_app
    response = client.post("/ingest")
    assert response.status_code == 400


def test_legacy_post_ingest_json_url_returns_410(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """The legacy JSON `{url}` branch of POST /ingest was retired in
    claude-5fp. Programmatic callers still using it should see 410 Gone
    with a hint pointing at /ingest/url, not silent success."""
    client, conn, _ = fresh_app
    response = client.post("/ingest", json={"url": "https://example.com/article.md"})
    assert response.status_code == 410
    assert "/ingest/url" in response.text
    # No documents row was created.
    (count,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert count == 0


# /ingest/url — user-initiated URL submission via firecrawl (claude-5fp)
# ─────────────────────────────────────────────────────────────


def test_post_ingest_url_happy_path_creates_converting_row(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Valid URL → ductile submit succeeds → 202 with document_id and
    a `converting` documents row exists with source_type='url' and
    original_path set to the submitted URL."""
    client, conn, _ = fresh_app
    with patch(
        "parsem.ingest.url_submit.submit_firecrawl_scrape", return_value=None
    ) as mock_submit:
        response = client.post(
            "/ingest/url", json={"url": "https://example.com/regulation"}
        )
    assert response.status_code == 202
    body = response.json()
    assert "document_id" in body and isinstance(body["document_id"], int)
    assert body["doc_id"] == str(body["document_id"])
    assert body["action"] == "submitted"
    # Plugin was called with the right payload shape.
    mock_submit.assert_called_once()
    call_kwargs = mock_submit.call_args.kwargs
    assert call_kwargs["url"] == "https://example.com/regulation"
    assert call_kwargs["doc_id"] == str(body["document_id"])
    # Row exists with status=converting.
    doc = load_document(conn, body["document_id"])
    assert doc is not None
    assert doc.status == "converting"
    assert doc.source_type == "url"
    assert doc.original_path == "https://example.com/regulation"


def test_post_ingest_url_rolls_back_on_ductile_failure(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Ductile call raises → endpoint returns 502 AND no leaked
    `converting` row in the DB. The rollback is the whole point."""
    client, conn, _ = fresh_app
    with patch(
        "parsem.ingest.url_submit.submit_firecrawl_scrape",
        side_effect=DuctileError("ductile 5xx: 503", kind="response", ductile_status=503),
    ):
        response = client.post(
            "/ingest/url", json={"url": "https://example.com/x"}
        )
    assert response.status_code == 502
    (count,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert count == 0


def test_post_ingest_url_missing_url_returns_400(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Empty body / missing url → 400 BEFORE any DB write."""
    client, conn, _ = fresh_app
    response = client.post("/ingest/url", json={})
    assert response.status_code == 400
    (count,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert count == 0


def test_post_ingest_url_bad_scheme_returns_400(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """file:// and other non-http(s) schemes → 400 before DB write.
    SSRF defence at the input boundary."""
    client, conn, _ = fresh_app
    for bad_url in ("file:///etc/passwd", "ftp://example.com", "javascript:alert(1)"):
        response = client.post("/ingest/url", json={"url": bad_url})
        assert response.status_code == 400, f"expected 400 for {bad_url!r}"
    (count,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert count == 0


def test_post_ingest_url_invalid_json_returns_400(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = fresh_app
    response = client.post(
        "/ingest/url",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_post_ingest_url_with_unconfigured_ductile_returns_502(
    tmp_path: Path,
) -> None:
    """If ductile.base_url is empty, URL ingest is disabled — caller
    gets a 502 with a clear reason rather than silent breakage."""
    conn = connect(":memory:")
    migrate(conn)
    library = tmp_path / "library"
    originals = library / "originals"
    raw = library / "inbound" / "raw"
    converted = library / "inbound" / "converted"
    for d in (originals, raw, converted):
        d.mkdir(parents=True, exist_ok=True)
    app = create_app(
        empty_reader_state(conn),
        db=conn,
        originals_dir=originals,
        inbound_raw_dir=raw,
        inbound_converted_dir=converted,
        ductile_settings=DuctileSettings(base_url="", api_token=""),
    )
    with TestClient(app) as client:
        response = client.post(
            "/ingest/url", json={"url": "https://example.com/x"}
        )
    assert response.status_code == 502
    (count,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert count == 0


def test_post_ingest_dedups_repeated_uploads(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Uploading the same content three times produces ONE document —
    `process_raw_arrival` dedups on content hash, so a fat-fingered
    double-submit doesn't litter the library. claude-als."""
    client, conn, _ = fresh_app
    md = b"# x\n\nbody text.\n"
    for _ in range(3):
        client.post(
            "/ingest",
            files={"file": ("dup.md", md, "text/markdown")},
            follow_redirects=False,
        )
    (count,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert count == 1


def test_post_ingest_sanitizes_filename(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Path traversal attempts get neutralized: any character outside
    [A-Za-z0-9._-] becomes '_'; path separators are stripped first. The
    sanitized .md is then found and ingested."""
    client, conn, originals = fresh_app
    raw_dir = originals.parent / "inbound" / "raw"
    response = client.post(
        "/ingest",
        files={"file": ("../../etc/passwd.md", b"hello content\n", "text/markdown")},
        follow_redirects=False,
    )
    assert response.status_code == 302
    # No file ever created outside raw_dir.
    assert not (raw_dir.parent.parent / "passwd.md").exists()
    # The sanitized .md was found and ingested into a document.
    assert load_document(conn, 1) is not None


# Round-trip via the arrivals core
# ─────────────────────────────────────────────────────────────


def test_process_raw_arrival_ingests_md_and_moves_to_originals(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """Drop a .md into raw/, call the arrivals core: doc row inserted,
    chunks parsed, file moved to originals/<id>/document.md, action=ingested."""
    _client, conn, originals = fresh_app
    raw = originals.parent / "inbound" / "raw"
    src = raw / "drop.md"
    src.write_text("# Title\n\nBody paragraph.\n", encoding="utf-8")
    result = process_raw_arrival(src, conn=conn, originals_dir=originals)
    assert result.action == "ingested"
    assert result.document_id is not None
    assert not src.exists()  # moved
    assert layout.markdown_path(originals, result.document_id).exists()
    doc = load_document(conn, result.document_id)
    assert doc is not None
    assert doc.title == "drop"


def test_process_raw_arrival_pdf_stages_for_marker(
    fresh_app: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    """A .pdf gets a converting row, moves to originals/<id>/source.pdf,
    and returns submit_to_marker so ductile knows to call Marker."""
    _client, conn, originals = fresh_app
    raw = originals.parent / "inbound" / "raw"
    src = raw / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 ...")
    result = process_raw_arrival(src, conn=conn, originals_dir=originals)
    assert result.action == "submit_to_marker"
    assert result.document_id is not None
    assert result.doc_id == str(result.document_id)
    expected_source = layout.source_path(originals, result.document_id, ".pdf")
    assert result.source_path == str(expected_source)
    assert expected_source.exists()
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
