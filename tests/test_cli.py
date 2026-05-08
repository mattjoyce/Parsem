"""Tests for parsem.cli — Phase 1 entry point (Parsem-4bt)."""

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


@pytest.fixture
def isolated_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Tests calling `build_app()` need an isolated DB and originals
    dir so they don't pollute the project's `data/parsem.db`."""
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", tmp_path / "parsem.db")
    monkeypatch.setattr(cli, "ORIGINALS_DIR", tmp_path / "originals")
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path)
    return tmp_path


def test_build_app_returns_app_serving_reader_route(isolated_build: Path) -> None:
    app = build_app()
    with TestClient(app) as client:
        response = client.get("/documents/1/reader")
        assert response.status_code == 200


def test_build_app_serves_welcome_corpus_first_chunk(isolated_build: Path) -> None:
    app = build_app()
    with TestClient(app) as client:
        response = client.get("/documents/1/reader")
    # First sentence of welcome.md, robust to chunker re-runs.
    assert "Parsem is a reading chamber" in response.text


def test_build_app_seeds_welcome_doc_idempotently(isolated_build: Path) -> None:
    """Booting twice on the same DB results in exactly one welcome row."""
    build_app()  # first boot — seeds welcome
    build_app()  # second boot — must not duplicate
    from parsem.store.db import connect

    conn = connect(str(isolated_build / "parsem.db"))
    rows = conn.execute(
        "SELECT id FROM documents WHERE original_path='data/welcome.md'"
    ).fetchall()
    assert len(rows) == 1


def test_main_defaults_host_to_localhost(isolated_build: Path) -> None:
    runner = _RecordingRunner()
    main(_runner=runner)
    assert runner.kwargs["host"] == "127.0.0.1"


def test_main_defaults_port_to_8000(isolated_build: Path) -> None:
    runner = _RecordingRunner()
    main(_runner=runner)
    assert runner.kwargs["port"] == 8000


def test_main_hands_runner_a_built_fastapi_app(isolated_build: Path) -> None:
    runner = _RecordingRunner()
    main(_runner=runner)
    assert isinstance(runner.app, FastAPI)


def test_python_dash_m_parsem_runs_main() -> None:
    """`python -m parsem` invokes parsem.__main__ which delegates to
    cli.main. The `if __name__ == "__main__"` guard suppresses execution
    on import, so we can safely confirm the wiring."""
    assert dunder_main.main is cli.main
