"""Tests for /ingest/raw-arrived and /ingest/converted-arrived
(the ductile-driven ingest seam — ADR 0002).

Endpoint coverage: action vocabulary, bearer-token auth, and the
PDF→Marker round-trip simulated end-to-end (raw-arrived stages the
PDF; we manually drop a converted .md + sidecar to mimic Marker;
converted-arrived completes the document).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem.config import IngestSettings
from parsem.ingest.arrivals import process_converted_arrival
from parsem.store.db import connect, migrate
from parsem.store.documents import load_document
from parsem.web.app import create_app
from parsem.web.state import empty_reader_state


def _make_app(
    tmp_path: Path, *, callback_token: str = ""
) -> tuple[TestClient, sqlite3.Connection, Path, Path, Path]:
    conn = connect(":memory:")
    migrate(conn)
    library = tmp_path / "library"
    originals = library / "originals"
    raw = library / "inbound" / "raw"
    converted = library / "inbound" / "converted"
    for d in (originals, raw, converted):
        d.mkdir(parents=True, exist_ok=True)
    settings = IngestSettings(
        url_timeout_seconds=30.0,
        url_max_bytes=50 * 1024 * 1024,
        callback_token=callback_token,
    )
    app = create_app(
        empty_reader_state(conn),
        db=conn,
        originals_dir=originals,
        inbound_raw_dir=raw,
        ingest_settings=settings,
    )
    return TestClient(app), conn, originals, raw, converted


@pytest.fixture
def app_open(tmp_path: Path) -> Iterator[tuple[TestClient, sqlite3.Connection, Path, Path, Path]]:
    """No callback token = open auth (dev-mode default)."""
    client, conn, originals, raw, converted = _make_app(tmp_path)
    with client:
        yield client, conn, originals, raw, converted


@pytest.fixture
def app_with_token(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, sqlite3.Connection, Path, Path, Path]]:
    client, conn, originals, raw, converted = _make_app(
        tmp_path, callback_token="s3cret"
    )
    with client:
        yield client, conn, originals, raw, converted


# ─── /ingest/raw-arrived ──────────────────────────────────────────────


def test_raw_arrived_md_returns_ingested(
    app_open: tuple[TestClient, sqlite3.Connection, Path, Path, Path],
) -> None:
    client, _conn, originals, raw, _ = app_open
    src = raw / "doc.md"
    src.write_text("# Hello\n\nBody.\n", encoding="utf-8")
    response = client.post("/ingest/raw-arrived", json={"path": str(src)})
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "ingested"
    assert body["document_id"] is not None
    assert (originals / f"{body['document_id']}.md").exists()


def test_raw_arrived_pdf_returns_submit_to_marker(
    app_open: tuple[TestClient, sqlite3.Connection, Path, Path, Path],
) -> None:
    client, conn, originals, raw, _ = app_open
    src = raw / "paper.pdf"
    src.write_bytes(b"%PDF-1.4 ...")
    response = client.post("/ingest/raw-arrived", json={"path": str(src)})
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "submit_to_marker"
    assert body["doc_id"] == str(body["document_id"])
    assert body["source_path"] == str(originals / f"{body['document_id']}.pdf")
    doc = load_document(conn, body["document_id"])
    assert doc is not None
    assert doc.status == "converting"


def test_raw_arrived_duplicate_returns_existing(
    app_open: tuple[TestClient, sqlite3.Connection, Path, Path, Path],
) -> None:
    client, _conn, _originals, raw, _ = app_open
    body_text = "# Same\n\ncontents.\n"
    src1 = raw / "a.md"
    src1.write_text(body_text, encoding="utf-8")
    first = client.post("/ingest/raw-arrived", json={"path": str(src1)}).json()

    src2 = raw / "b.md"
    src2.write_text(body_text, encoding="utf-8")
    second = client.post("/ingest/raw-arrived", json={"path": str(src2)}).json()

    assert first["action"] == "ingested"
    assert second["action"] == "duplicate"
    assert second["document_id"] == first["document_id"]


def test_raw_arrived_missing_file_is_no_op(
    app_open: tuple[TestClient, sqlite3.Connection, Path, Path, Path],
) -> None:
    client, _, _, raw, _ = app_open
    response = client.post(
        "/ingest/raw-arrived", json={"path": str(raw / "ghost.md")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "unsupported"
    assert body["reason"] == "file not found"


def test_raw_arrived_unknown_extension_records_failed_row(
    app_open: tuple[TestClient, sqlite3.Connection, Path, Path, Path],
) -> None:
    client, conn, _originals, raw, _ = app_open
    src = raw / "thing.xyz"
    src.write_bytes(b"\x00")
    response = client.post("/ingest/raw-arrived", json={"path": str(src)})
    body = response.json()
    assert body["action"] == "unsupported"
    doc = load_document(conn, body["document_id"])
    assert doc is not None
    assert doc.status == "failed"


# ─── auth ────────────────────────────────────────────────────────────


def test_arrivals_with_token_rejects_no_auth(
    app_with_token: tuple[TestClient, sqlite3.Connection, Path, Path, Path],
) -> None:
    client, _, _, raw, _ = app_with_token
    src = raw / "doc.md"
    src.write_text("# x\n\nx.\n", encoding="utf-8")
    response = client.post("/ingest/raw-arrived", json={"path": str(src)})
    assert response.status_code == 401


def test_arrivals_with_token_rejects_wrong_token(
    app_with_token: tuple[TestClient, sqlite3.Connection, Path, Path, Path],
) -> None:
    client, _, _, raw, _ = app_with_token
    src = raw / "doc.md"
    src.write_text("# x\n\nx.\n", encoding="utf-8")
    response = client.post(
        "/ingest/raw-arrived",
        json={"path": str(src)},
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_arrivals_with_token_accepts_correct_token(
    app_with_token: tuple[TestClient, sqlite3.Connection, Path, Path, Path],
) -> None:
    client, _, _, raw, _ = app_with_token
    src = raw / "doc.md"
    src.write_text("# x\n\nbody.\n", encoding="utf-8")
    response = client.post(
        "/ingest/raw-arrived",
        json={"path": str(src)},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "ingested"


# ─── /ingest/converted-arrived ────────────────────────────────────────


def _drop_marker_output(
    converted_dir: Path, *, doc_id: int, md_text: str, sidecar: dict
) -> Path:
    """Simulate Marker's atomic-write contract: write sidecar JSON
    first, then the .md last (in real life Marker also writes an
    images dir; we skip that here since arrivals doesn't read it)."""
    sidecar_path = converted_dir / f"{doc_id}.json"
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    md_path = converted_dir / f"{doc_id}.md"
    md_path.write_text(md_text, encoding="utf-8")
    return md_path


def test_converted_arrived_completes_pdf_round_trip(
    app_open: tuple[TestClient, sqlite3.Connection, Path, Path, Path],
) -> None:
    """Full PDF flow: raw-arrived stages, marker drop, converted-arrived
    flips to ready and writes an extraction_runs row."""
    client, conn, _originals, raw, converted = app_open
    pdf = raw / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n...")
    submit_resp = client.post("/ingest/raw-arrived", json={"path": str(pdf)}).json()
    doc_id = submit_resp["document_id"]
    assert submit_resp["action"] == "submit_to_marker"

    md_path = _drop_marker_output(
        converted,
        doc_id=doc_id,
        md_text="# Paper\n\nConverted body.\n",
        sidecar={
            "doc_id": str(doc_id),
            "status": "ready",
            "source": "/input/in.pdf",
            "output_md": f"/output/{doc_id}.md",
            "marker_version": "1.10.2",
            "duration_seconds": 599.5,
            "image_count": 0,
            "completed_at": "2026-05-10T10:25:55+00:00",
        },
    )
    response = client.post(
        "/ingest/converted-arrived", json={"path": str(md_path)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "ingested"
    assert body["document_id"] == doc_id
    doc = load_document(conn, doc_id)
    assert doc is not None
    assert doc.status == "ready"

    # extraction_runs row carries marker_version + duration
    rows = conn.execute(
        "SELECT extractor_name, extractor_version, params_json"
        " FROM extraction_runs WHERE document_id=?",
        (doc_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["extractor_name"] == "marker"
    assert rows[0]["extractor_version"] == "1.10.2"
    params = json.loads(rows[0]["params_json"])
    assert params["duration_seconds"] == 599.5


def test_converted_arrived_missing_doc_returns_404_shape(
    app_open: tuple[TestClient, sqlite3.Connection, Path, Path, Path],
) -> None:
    """A converted .md with no matching doc row returns missing_doc.
    Response is still 200 (idempotent shape); the action carries the
    semantic info ductile uses to alert/retry."""
    client, _conn, _, _, converted = app_open
    md = converted / "999.md"
    md.write_text("# Orphan\n\nbody.\n", encoding="utf-8")
    response = client.post("/ingest/converted-arrived", json={"path": str(md)})
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "missing_doc"
    assert body["document_id"] == 999


def test_converted_arrived_filename_must_be_int(
    app_open: tuple[TestClient, sqlite3.Connection, Path, Path, Path],
) -> None:
    client, _, _, _, converted = app_open
    md = converted / "not-a-doc-id.md"
    md.write_text("# x\n\nx.\n", encoding="utf-8")
    response = client.post("/ingest/converted-arrived", json={"path": str(md)})
    body = response.json()
    assert body["action"] == "failed"


def test_converted_arrived_duplicate_under_retry(
    app_open: tuple[TestClient, sqlite3.Connection, Path, Path, Path],
) -> None:
    """Ductile filewatch can fire twice on the same file; the second
    call must be a no-op (action=duplicate) since the doc already
    flipped to ready."""
    client, _conn, _, raw, converted = app_open
    pdf = raw / "p.pdf"
    pdf.write_bytes(b"%PDF...")
    doc_id = client.post("/ingest/raw-arrived", json={"path": str(pdf)}).json()[
        "document_id"
    ]
    md = _drop_marker_output(
        converted, doc_id=doc_id, md_text="# t\n\nbody.\n",
        sidecar={"marker_version": "1.10.2", "source": "/input/in.pdf",
                 "duration_seconds": 1.0, "image_count": 0,
                 "completed_at": "2026-05-10T10:00:00+00:00"},
    )
    first = client.post("/ingest/converted-arrived", json={"path": str(md)}).json()
    assert first["action"] == "ingested"
    second = client.post("/ingest/converted-arrived", json={"path": str(md)}).json()
    assert second["action"] == "duplicate"


def test_process_converted_arrival_tolerates_missing_sidecar(
    tmp_path: Path,
) -> None:
    """Marker is allowed to drop a .md without a sidecar; the ingest
    still succeeds, just without the extraction_runs metadata."""
    conn = connect(":memory:")
    migrate(conn)
    library = tmp_path / "library"
    originals = library / "originals"
    converted = library / "inbound" / "converted"
    for d in (originals, converted):
        d.mkdir(parents=True, exist_ok=True)
    # Pre-create a converting doc row at id=1
    from datetime import UTC, datetime

    from parsem.store.documents import insert_document
    doc_id = insert_document(
        conn, title="seeded", original_path=str(originals / "1.pdf"),
        status="converting", source_type="pdf", now=datetime.now(UTC),
    )
    md = converted / f"{doc_id}.md"
    md.write_text("# Title\n\nbody.\n", encoding="utf-8")
    result = process_converted_arrival(md, conn=conn, originals_dir=originals)
    assert result.action == "ingested"
    rows = conn.execute(
        "SELECT count(*) AS n FROM extraction_runs WHERE document_id=?",
        (doc_id,),
    ).fetchone()
    assert rows["n"] == 0  # no sidecar -> no extraction_runs row
