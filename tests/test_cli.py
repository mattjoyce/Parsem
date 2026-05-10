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
