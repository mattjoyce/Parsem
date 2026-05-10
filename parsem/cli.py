"""CLI entry point. Spec: parsem-spec.md §17.1, §25.1; ADR 0001 (cycle 1).

Subcommands:
    parsem               # back-compat — runs the server
    parsem serve         # explicit server start
    parsem add <url|file>  # drop into inbound/raw/ for the watcher

`build_app()` connects to the SQLite DB, migrates the schema,
idempotently seeds the bundled welcome corpus, and returns a
FastAPI app whose `app.state.reader` is opened on the welcome doc.
Path roots come from `parsem.config` — set `PARSEM_DATA_DIR` and
`PARSEM_LIBRARY_DIR` to override defaults in prod.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import uvicorn
from fastapi import FastAPI

from parsem.config import PROJECT_ROOT, Paths, ensure_library_layout, resolve_paths
from parsem.ingest.paths import unique_inbound_path
from parsem.ingest.url_fetch import UrlFetchError, fetch
from parsem.store.db import connect, migrate
from parsem.store.documents import insert_document
from parsem.web.app import create_app
from parsem.web.ingest import parse_and_persist
from parsem.web.state import build_reader_state_for_document

# Spec §20: resume.warm_chunks default. Phase 2 settings.py will read
# this from the settings table; until then it's a module-level default.
RESUME_WARM_CHUNKS_DEFAULT = 2

WELCOME_PATH = PROJECT_ROOT / "data" / "welcome.md"
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
    now = datetime.now(UTC)
    document_id = insert_document(
        conn,
        title="welcome",
        original_path=WELCOME_ORIGINAL_PATH,
        status="processing",
        now=now,
    )
    if not parse_and_persist(conn, document_id=document_id, text=text, now=now):
        raise RuntimeError(
            "Welcome doc failed to ingest — substrate parse pipeline rejected it"
        )
    return document_id


def build_app(paths: Paths | None = None) -> FastAPI:
    """Build the FastAPI app against the file-backed DB at `paths.db_path`.
    Default resolves from env / config. Tests pass an explicit `Paths`
    pointing at tmpdir paths."""
    resolved = paths or resolve_paths()
    ensure_library_layout(resolved)
    conn = connect(resolved.db_path)
    migrate(conn)
    welcome_id = _ensure_welcome_seeded(conn)
    state = build_reader_state_for_document(
        conn, welcome_id, warm_chunks=RESUME_WARM_CHUNKS_DEFAULT
    )
    assert state is not None  # welcome doc was just seeded; must exist
    return create_app(
        state,
        db=conn,
        originals_dir=resolved.originals_dir,
        inbound_raw_dir=resolved.inbound_raw_dir,
    )


def _cmd_serve(args: argparse.Namespace, *, runner: Callable[..., Any]) -> int:
    runner(build_app(), host=args.host, port=args.port)
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    """`parsem add <url|file>` — drop into inbound/raw/ without going
    through the server. The watcher (when running) will ingest it; if
    the server isn't running, the file waits in inbound/raw/ until the
    next startup sweep picks it up."""
    paths = resolve_paths()
    ensure_library_layout(paths)
    target_dir = paths.inbound_raw_dir
    source = args.target

    if source.startswith(("http://", "https://")):
        try:
            fetched = fetch(source)
        except UrlFetchError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        target = unique_inbound_path(target_dir, fetched.suggested_filename)
        target.write_bytes(fetched.content)
        print(f"queued: {target.name}")
        return 0

    from pathlib import Path

    src_path = Path(source).expanduser().resolve()
    if not src_path.is_file():
        print(f"error: not a file: {src_path}", file=sys.stderr)
        return 2
    target = unique_inbound_path(target_dir, src_path.name)
    shutil.copy2(src_path, target)
    print(f"queued: {target.name}")
    return 0


def main(
    argv: list[str] | None = None,
    *,
    _runner: Callable[..., Any] = uvicorn.run,
) -> int:
    """Build the app and hand it to uvicorn (`serve`), or run a CLI
    subcommand. Bare `parsem` keeps back-compat by running the server."""
    parser = argparse.ArgumentParser(prog="parsem")
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="run the web server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    p_add = sub.add_parser("add", help="drop a URL or file into inbound/raw/")
    p_add.add_argument("target", help="URL or path to a local file")

    args = parser.parse_args(argv)
    if args.cmd == "add":
        return _cmd_add(args)
    # Bare `parsem` (no subcommand) is back-compat for `parsem serve`
    # with defaults. argparse's subparsers leave args.cmd=None in
    # that case; we don't reach this branch via mistyped commands
    # because parse_args would have already exited.
    if args.cmd in (None, "serve"):
        if args.cmd is None:
            args.host = "127.0.0.1"
            args.port = 8000
        return _cmd_serve(args, runner=_runner)
    parser.error(f"unknown command: {args.cmd}")
    return 2  # unreachable; argparse exits before
