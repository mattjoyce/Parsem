"""Tests for the inbound watcher's sweep behavior (claude-mwx.1).

The synchronous core (process_file) is tested in tests/web/test_upload.py
in the context of an app fixture; this file covers the multi-file
sweep that runs at startup to catch files dropped while the server
was down.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from parsem.ingest.watcher import sweep
from parsem.store.db import connect, migrate


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = connect(":memory:")
    migrate(c)
    return c


def test_sweep_processes_existing_md_files(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    raw = tmp_path / "raw"
    originals = tmp_path / "originals"
    raw.mkdir()
    originals.mkdir()
    (raw / "a.md").write_text("# a\n\nbody.\n", encoding="utf-8")
    (raw / "b.md").write_text("# b\n\nmore.\n", encoding="utf-8")

    count = sweep(raw, conn=conn, originals_dir=originals)
    assert count == 2
    # Files moved out of raw/, into originals/
    assert not list(raw.iterdir())
    assert len(list(originals.iterdir())) == 2


def test_sweep_skips_non_md(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    raw = tmp_path / "raw"
    originals = tmp_path / "originals"
    raw.mkdir()
    originals.mkdir()
    (raw / "doc.pdf").write_bytes(b"%PDF...")
    (raw / "good.md").write_text("# good\n\nx\n", encoding="utf-8")

    count = sweep(raw, conn=conn, originals_dir=originals)
    assert count == 1
    # The pdf is left alone
    assert (raw / "doc.pdf").exists()
    assert not (raw / "good.md").exists()  # ingested


def test_sweep_returns_zero_on_missing_dir(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    raw = tmp_path / "absent"
    originals = tmp_path / "originals"
    originals.mkdir()
    assert sweep(raw, conn=conn, originals_dir=originals) == 0
