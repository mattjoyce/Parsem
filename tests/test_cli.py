"""Tests for parsem.cli — Phase 1 entry point (Parsem-4bt)."""

from __future__ import annotations

from typing import Any

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


def test_build_app_returns_app_serving_reader_route() -> None:
    app = build_app()
    with TestClient(app) as client:
        response = client.get("/reader")
        assert response.status_code == 200


def test_build_app_serves_welcome_corpus_first_chunk() -> None:
    app = build_app()
    with TestClient(app) as client:
        response = client.get("/reader")
    # First sentence of welcome.md, robust to chunker re-runs.
    assert "Parsem is a reading chamber" in response.text


def test_main_defaults_host_to_localhost() -> None:
    runner = _RecordingRunner()
    main(_runner=runner)
    assert runner.kwargs["host"] == "127.0.0.1"


def test_main_defaults_port_to_8000() -> None:
    runner = _RecordingRunner()
    main(_runner=runner)
    assert runner.kwargs["port"] == 8000


def test_main_hands_runner_a_built_fastapi_app() -> None:
    runner = _RecordingRunner()
    main(_runner=runner)
    assert isinstance(runner.app, FastAPI)


def test_python_dash_m_parsem_runs_main() -> None:
    """`python -m parsem` invokes parsem.__main__ which delegates to
    cli.main. The `if __name__ == "__main__"` guard suppresses execution
    on import, so we can safely confirm the wiring."""
    assert dunder_main.main is cli.main
