"""Tests for parsem.cli — entry point + add subcommand (claude-mwx.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import parsem.__main__ as dunder_main
from parsem import cli
from parsem.cli import build_app, main


class _RecordingRunner:
    """Captures uvicorn.run kwargs without binding a port."""

    def __init__(self) -> None:
        self.app: Any = None
        self.kwargs: dict[str, Any] = {}

    def __call__(self, app: Any, **kwargs: Any) -> None:
        self.app = app
        self.kwargs = kwargs


def _write_test_config(tmp_path: Path) -> Path:
    """Materialize a minimal loaden config pointing at tmp_path so the
    test never touches ~/.config/parsem/ or the project's data/."""
    config = tmp_path / "config.yaml"
    config.write_text(
        f"paths:\n"
        f"  data: {tmp_path / 'appdata'}\n"
        f"  library: {tmp_path / 'library'}\n"
        "server:\n"
        "  host: 127.0.0.1\n"
        "  port: 8000\n"
        "ingest:\n"
        "  url_timeout_seconds: 30\n"
        "  url_max_bytes: 52428800\n",
        encoding="utf-8",
    )
    return config


@pytest.fixture
def isolated_build(tmp_path: Path) -> tuple[Path, Path]:
    """Tmp config + tmp library dir for tests that boot the app."""
    config = _write_test_config(tmp_path)
    return tmp_path, config


def test_build_app_returns_app_serving_reader_route(
    isolated_build: tuple[Path, Path],
) -> None:
    _, config = isolated_build
    app = build_app(cli.load_settings(config))
    with TestClient(app) as client:
        response = client.get("/documents/1/reader")
        assert response.status_code == 200


def test_build_app_serves_welcome_corpus_first_chunk(
    isolated_build: tuple[Path, Path],
) -> None:
    _, config = isolated_build
    app = build_app(cli.load_settings(config))
    with TestClient(app) as client:
        response = client.get("/documents/1/reader")
    assert "Parsem is a reading chamber" in response.text


def test_build_app_seeds_welcome_doc_idempotently(
    isolated_build: tuple[Path, Path],
) -> None:
    """Booting twice on the same DB results in exactly one welcome row."""
    tmp_path, config = isolated_build
    settings = cli.load_settings(config)
    build_app(settings)
    build_app(settings)
    from parsem.store.db import connect

    conn = connect(str(tmp_path / "appdata" / "parsem.db"))
    rows = conn.execute(
        "SELECT id FROM documents WHERE original_path='data/welcome.md'"
    ).fetchall()
    assert len(rows) == 1


def test_main_defaults_host_to_config_value(
    isolated_build: tuple[Path, Path],
) -> None:
    _, config = isolated_build
    runner = _RecordingRunner()
    main(["--config", str(config), "serve"], _runner=runner)
    assert runner.kwargs["host"] == "127.0.0.1"


def test_main_defaults_port_to_config_value(
    isolated_build: tuple[Path, Path],
) -> None:
    _, config = isolated_build
    runner = _RecordingRunner()
    main(["serve", "--config", str(config)], _runner=runner)
    assert runner.kwargs["port"] == 8000


def test_main_hands_runner_a_built_fastapi_app(
    isolated_build: tuple[Path, Path],
) -> None:
    _, config = isolated_build
    runner = _RecordingRunner()
    main(["serve", "--config", str(config)], _runner=runner)
    assert isinstance(runner.app, FastAPI)


def test_main_cli_host_overrides_config(
    isolated_build: tuple[Path, Path],
) -> None:
    """`--host 0.0.0.0` on the CLI beats the config-file value."""
    _, config = isolated_build
    runner = _RecordingRunner()
    main(["serve", "--config", str(config), "--host", "0.0.0.0"], _runner=runner)
    assert runner.kwargs["host"] == "0.0.0.0"


def test_python_dash_m_parsem_runs_main() -> None:
    """`python -m parsem` invokes parsem.__main__ which delegates to
    cli.main."""
    assert dunder_main.main is cli.main


def test_add_subcommand_with_local_file_drops_to_inbound_raw(
    isolated_build: tuple[Path, Path],
) -> None:
    """`parsem add <path>` copies the file to inbound/raw/."""
    tmp_path, config = isolated_build
    src = tmp_path / "sample.md"
    src.write_text("# hello\n", encoding="utf-8")
    rc = main(["add", "--config", str(config), str(src)])
    assert rc == 0
    inbound = tmp_path / "library" / "inbound" / "raw"
    assert (inbound / "sample.md").read_text() == "# hello\n"


# --- parsem rechunk (claude-m4l) ---------------------------------------


def _seed_ready_doc(tmp_path: Path, *, body: str = "# Doc\n\nA paragraph here.\n") -> int:
    """Insert a `ready` document into the test DB with a real
    originals/<id>/document.md on disk, then close the connection so the
    CLI opens its own. Returns the document id."""
    from datetime import UTC, datetime

    from parsem.ingest import layout
    from parsem.store.db import connect, migrate
    from parsem.store.documents import insert_document
    from parsem.web.ingest import parse_and_persist

    db_path = tmp_path / "appdata" / "parsem.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    migrate(conn)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    doc_id = insert_document(
        conn, title="rechunk-me", original_path="placeholder", status="processing", now=now
    )
    md = layout.markdown_path(tmp_path / "library" / "originals", doc_id)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(body, encoding="utf-8")
    conn.execute("UPDATE documents SET original_path=? WHERE id=?", (str(md), doc_id))
    assert parse_and_persist(conn, document_id=doc_id, text=body, now=now)
    conn.commit()
    conn.close()
    return doc_id


def _chunk_count(tmp_path: Path, doc_id: int) -> int:
    from parsem.store.db import connect

    conn = connect(tmp_path / "appdata" / "parsem.db")
    n = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id=?", (doc_id,)
    ).fetchone()[0]
    conn.close()
    return int(n)


def test_rechunk_by_id_recreates_chunks(isolated_build: tuple[Path, Path]) -> None:
    tmp_path, config = isolated_build
    doc_id = _seed_ready_doc(tmp_path)
    assert _chunk_count(tmp_path, doc_id) >= 1
    rc = main(["rechunk", "--config", str(config), str(doc_id)])
    assert rc == 0
    assert _chunk_count(tmp_path, doc_id) >= 1  # re-chunked, still has chunks


def test_rechunk_all_hits_every_document(isolated_build: tuple[Path, Path]) -> None:
    tmp_path, config = isolated_build
    a = _seed_ready_doc(tmp_path, body="# A\n\nfirst doc.\n")
    b = _seed_ready_doc(tmp_path, body="# B\n\nsecond doc.\n")
    rc = main(["rechunk", "--config", str(config), "--all"])
    assert rc == 0
    assert _chunk_count(tmp_path, a) >= 1
    assert _chunk_count(tmp_path, b) >= 1


def test_rechunk_unknown_id_returns_nonzero(isolated_build: tuple[Path, Path]) -> None:
    _, config = isolated_build
    assert main(["rechunk", "--config", str(config), "9999"]) != 0


def test_rechunk_without_id_or_all_returns_usage_error(
    isolated_build: tuple[Path, Path],
) -> None:
    _, config = isolated_build
    assert main(["rechunk", "--config", str(config)]) == 2
