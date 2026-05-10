"""Tests for parsem.config — loaden-backed config + back-compat
env-var resolve_paths (claude-mwx.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from parsem.config import (
    DEFAULT_CONFIG_PATH,
    ensure_default_config,
    ensure_library_layout,
    load_settings,
    resolve_config_path,
    resolve_paths,
)


# Loaden-backed config
# ────────────────────


def test_load_settings_reads_yaml_and_resolves_paths(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"paths:\n"
        f"  data: {tmp_path / 'd'}\n"
        f"  library: {tmp_path / 'l'}\n"
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 9000\n"
        "ingest:\n"
        "  url_timeout_seconds: 5\n"
        "  url_max_bytes: 1024\n",
        encoding="utf-8",
    )
    settings = load_settings(config, auto_create_default=False)
    assert settings.paths.data_dir == tmp_path / "d"
    assert settings.paths.library_dir == tmp_path / "l"
    assert settings.server.host == "0.0.0.0"
    assert settings.server.port == 9000
    assert settings.ingest.url_timeout_seconds == 5.0
    assert settings.ingest.url_max_bytes == 1024


def test_load_settings_expands_env_vars_in_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`${VAR}` substitution flows through loaden — env vars stay a
    clean override surface even when the config file is in play."""
    monkeypatch.setenv("PARSEM_DATA_DIR_OVERRIDE", str(tmp_path / "from-env"))
    config = tmp_path / "config.yaml"
    config.write_text(
        "paths:\n"
        "  data: ${PARSEM_DATA_DIR_OVERRIDE:-./fallback}\n"
        f"  library: {tmp_path / 'l'}\n",
        encoding="utf-8",
    )
    settings = load_settings(config, auto_create_default=False)
    assert settings.paths.data_dir == tmp_path / "from-env"


def test_load_settings_uses_defaults_for_missing_sections(tmp_path: Path) -> None:
    """A YAML with only `paths` still loads — server/ingest pick up
    bundled defaults via loaden's `get(..., default)`."""
    config = tmp_path / "config.yaml"
    config.write_text(
        f"paths:\n"
        f"  data: {tmp_path / 'd'}\n"
        f"  library: {tmp_path / 'l'}\n",
        encoding="utf-8",
    )
    settings = load_settings(config, auto_create_default=False)
    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 8000
    assert settings.ingest.url_timeout_seconds == 30.0


def test_load_settings_raises_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(FileNotFoundError):
        load_settings(missing, auto_create_default=False)


def test_resolve_config_path_explicit_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "x.yaml"
    assert resolve_config_path(explicit) == explicit.resolve()


def test_resolve_config_path_default_under_xdg() -> None:
    assert resolve_config_path() == DEFAULT_CONFIG_PATH


def test_ensure_default_config_writes_template_only_once(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    ensure_default_config(target)
    assert target.exists()
    written = target.read_text(encoding="utf-8")
    target.write_text("custom\n", encoding="utf-8")
    ensure_default_config(target)  # idempotent — must not overwrite
    assert target.read_text(encoding="utf-8") == "custom\n"
    # Sanity check: the original template carried the schema markers.
    assert "paths:" in written
    assert "server:" in written
    assert "ingest:" in written


# Back-compat env-var resolve_paths
# ─────────────────────────────────


def test_resolve_paths_env_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PARSEM_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PARSEM_LIBRARY_DIR", str(tmp_path / "Library"))
    paths = resolve_paths()
    assert paths.data_dir == tmp_path / "appdata"
    assert paths.library_dir == tmp_path / "Library"


def test_resolve_paths_explicit_args_beat_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PARSEM_DATA_DIR", "/somewhere/else")
    paths = resolve_paths(data_dir=tmp_path)
    assert paths.data_dir == tmp_path


def test_derived_paths_match_contract(tmp_path: Path) -> None:
    paths = resolve_paths(
        data_dir=tmp_path / "data", library_dir=tmp_path / "lib"
    )
    assert paths.db_path == tmp_path / "data" / "parsem.db"
    assert paths.originals_dir == tmp_path / "lib" / "originals"
    assert paths.inbound_raw_dir == tmp_path / "lib" / "inbound" / "raw"
    assert paths.inbound_converted_dir == tmp_path / "lib" / "inbound" / "converted"


def test_ensure_library_layout_is_idempotent(tmp_path: Path) -> None:
    paths = resolve_paths(
        data_dir=tmp_path / "d", library_dir=tmp_path / "l"
    )
    ensure_library_layout(paths)
    ensure_library_layout(paths)  # second call must not raise
    assert paths.originals_dir.is_dir()
    assert paths.inbound_raw_dir.is_dir()
    assert paths.inbound_converted_dir.is_dir()
