"""Tests for parsem.config — env-driven path resolution (claude-mwx.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from parsem.config import ensure_library_layout, resolve_paths


def test_defaults_resolve_under_project_data_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PARSEM_DATA_DIR", raising=False)
    monkeypatch.delenv("PARSEM_LIBRARY_DIR", raising=False)
    paths = resolve_paths()
    assert paths.data_dir.name == "data"
    # Library default lives under data/ for zero-config dev.
    assert paths.library_dir.parent == paths.data_dir
    assert paths.library_dir.name == "library"


def test_env_var_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PARSEM_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PARSEM_LIBRARY_DIR", str(tmp_path / "Library"))
    paths = resolve_paths()
    assert paths.data_dir == tmp_path / "appdata"
    assert paths.library_dir == tmp_path / "Library"


def test_explicit_args_beat_env(
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
