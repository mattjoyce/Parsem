"""Concurrent-ingest connection isolation. Follow-up to the incident
where a batch drop fired ~6 simultaneous POST /ingest/raw-arrived calls
at a single shared SQLite connection, corrupting doc ids (lastrowid
race) and colliding transactions.

The fix (parsem.web.db_session): stateless routes get a fresh connection
per request. These tests lock:
- the provider mechanism (distinct vs shared connections),
- the wiring (file-backed app → per-request; :memory:/tests → shared),
- integrity under a concurrent batch of raw arrivals.
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem.store.db import connect, migrate
from parsem.web.app import create_app
from parsem.web.db_session import (
    PerRequestConnectionProvider,
    SharedConnectionProvider,
    build_provider,
)
from parsem.web.state import empty_reader_state

# === Provider mechanism (deterministic) ================================


def test_per_request_provider_yields_distinct_connections(tmp_path: Path) -> None:
    db_path = tmp_path / "p.db"
    seed = connect(str(db_path))
    migrate(seed)
    seed.close()

    provider = PerRequestConnectionProvider(str(db_path))
    with provider.acquire() as a, provider.acquire() as b:
        assert a is not b  # each request its own connection

    # The connection is closed once its context exits.
    with provider.acquire() as c:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        c.execute("SELECT 1")


def test_shared_provider_reuses_the_one_connection() -> None:
    conn = connect(":memory:")
    migrate(conn)
    provider = SharedConnectionProvider(conn)
    with provider.acquire() as a, provider.acquire() as b:
        assert a is b is conn
    # Not closed — the caller owns its lifetime.
    assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_build_provider_selects_strategy_by_path(tmp_path: Path) -> None:
    conn = connect(":memory:")
    assert isinstance(build_provider(conn, None), SharedConnectionProvider)
    # A :memory: path must NOT become per-request (a 2nd :memory: conn is
    # a different, empty database).
    assert isinstance(build_provider(conn, ":memory:"), SharedConnectionProvider)
    assert isinstance(
        build_provider(conn, str(tmp_path / "x.db")),
        PerRequestConnectionProvider,
    )


# === Wiring ============================================================


def test_file_backed_app_uses_per_request_provider(tmp_path: Path) -> None:
    db_path = tmp_path / "parsem.db"
    conn = connect(str(db_path))
    migrate(conn)
    app = create_app(
        empty_reader_state(conn),
        db=conn,
        db_path=str(db_path),
        originals_dir=tmp_path / "originals",
    )
    assert isinstance(app.state.db_provider, PerRequestConnectionProvider)


def test_memory_app_uses_shared_provider(tmp_path: Path) -> None:
    conn = connect(":memory:")
    migrate(conn)
    app = create_app(
        empty_reader_state(conn),
        db=conn,
        originals_dir=tmp_path / "originals",
    )
    assert isinstance(app.state.db_provider, SharedConnectionProvider)


# === Integration: concurrent raw arrivals stay consistent ==============


@pytest.fixture
def file_app(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, Path, Path]]:
    """A file-backed app (so per-request connections are live) plus its
    inbound/raw dir and db path."""
    db_path = tmp_path / "parsem.db"
    conn = connect(str(db_path))
    migrate(conn)
    originals = tmp_path / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    raw = tmp_path / "inbound" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    app = create_app(
        empty_reader_state(conn),
        db=conn,
        db_path=str(db_path),
        originals_dir=originals,
        inbound_raw_dir=raw,
    )
    with TestClient(app) as client:
        yield client, raw, db_path


def test_concurrent_raw_arrivals_create_consistent_documents(
    file_app: tuple[TestClient, Path, Path],
) -> None:
    client, raw, db_path = file_app
    n = 12
    paths = []
    for i in range(n):
        p = raw / f"doc-{i:02d}.md"
        p.write_text(
            f"# Document {i}\n\nParagraph one for doc {i}.\n\nParagraph two for doc {i}.\n",
            encoding="utf-8",
        )
        paths.append(p)

    def hit(p: Path) -> tuple[int, str]:
        r = client.post("/ingest/raw-arrived", json={"path": str(p)})
        return r.status_code, r.json().get("action", "")

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        outcomes = list(ex.map(hit, paths))

    assert all(code == 200 for code, _ in outcomes), outcomes
    assert all(action == "ingested" for _, action in outcomes), outcomes

    # Inspect the persisted state from an independent connection.
    check = connect(str(db_path))
    try:
        docs = check.execute("SELECT id, total_chunks FROM documents").fetchall()
        # Exactly n distinct documents, none sharing an id.
        ids = [d["id"] for d in docs]
        assert len(ids) == n
        assert len(set(ids)) == n
        # Every doc has its own chunks, and the count matches total_chunks
        # (a lastrowid race would cross-link or orphan chunks).
        for d in docs:
            cnt = check.execute(
                "SELECT COUNT(*) AS c FROM chunks WHERE document_id = ?",
                (d["id"],),
            ).fetchone()["c"]
            assert cnt > 0
            assert cnt == d["total_chunks"]
        # No chunk points at a non-existent document.
        orphans = check.execute(
            "SELECT COUNT(*) AS c FROM chunks WHERE document_id NOT IN (SELECT id FROM documents)"
        ).fetchone()["c"]
        assert orphans == 0
    finally:
        check.close()
