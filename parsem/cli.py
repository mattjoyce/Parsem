"""Phase 2 entry point. Spec: parsem-spec.md §17.1, §25.1; beads Parsem-4bt, Parsem-cwj.

`build_app()` connects to the file-backed `data/parsem.db`, migrates the
schema, idempotently seeds the bundled welcome corpus, and returns a
FastAPI app whose `app.state.reader` is opened on the welcome doc.
Subsequent visits to `/documents/{id}/reader` switch the open doc.

Tests target a separate construction path via `tests/web/conftest.py`
that uses `:memory:` to keep the suite isolated and fast — production
ownership of the DB lives here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from parsem.domain.chunking import ChunkingConfig, chunk
from parsem.parse.markdown_parse import parse
from parsem.store.db import connect, migrate
from parsem.store.documents import insert_chunks_and_sections, insert_document
from parsem.web.app import create_app
from parsem.web.state import build_reader_state_for_document

# Spec §20: resume.warm_chunks default. Phase 2 settings.py will read
# this from the settings table; until then it's a module-level default.
RESUME_WARM_CHUNKS_DEFAULT = 2

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = _PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "parsem.db"
ORIGINALS_DIR = DATA_DIR / "originals"
WELCOME_PATH = DATA_DIR / "welcome.md"
WELCOME_ORIGINAL_PATH = "data/welcome.md"  # idempotency key in documents.original_path


def _ensure_welcome_seeded(conn: sqlite3.Connection) -> int:
    """Insert the welcome doc on first boot; return its id either way.
    Idempotency key is `documents.original_path == 'data/welcome.md'`."""
    row = conn.execute(
        "SELECT id FROM documents WHERE original_path=?",
        (WELCOME_ORIGINAL_PATH,),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    text = WELCOME_PATH.read_text(encoding="utf-8")
    output = chunk(parse(text), ChunkingConfig())
    now = datetime.now(UTC)
    document_id = insert_document(
        conn,
        title="welcome",
        original_path=WELCOME_ORIGINAL_PATH,
        status="ready",
        total_chunks=len(output.chunks),
        now=now,
    )
    insert_chunks_and_sections(
        conn,
        document_id=document_id,
        chunks=output.chunks,
        sections=output.sections,
        now=now,
    )
    return document_id


def build_app(db_path: Path | str | None = None) -> FastAPI:
    """Build the FastAPI app against the file-backed DB at `db_path`.
    Default is `DEFAULT_DB_PATH` resolved at call time so tests can
    monkey-patch the module-level constants for isolation."""
    resolved_db = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect(resolved_db)
    migrate(conn)
    welcome_id = _ensure_welcome_seeded(conn)
    state = build_reader_state_for_document(
        conn, welcome_id, warm_chunks=RESUME_WARM_CHUNKS_DEFAULT
    )
    assert state is not None  # welcome doc was just seeded; must exist
    return create_app(state, db=conn, originals_dir=ORIGINALS_DIR)


def main(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    _runner: Callable[..., Any] = uvicorn.run,
) -> None:
    """Build the app and hand it to uvicorn. The `_runner` kwarg is for
    tests — production code uses the default `uvicorn.run`."""
    _runner(build_app(), host=host, port=port)
