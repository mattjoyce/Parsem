"""CLI entry point. Spec: parsem-spec.md §17.1, §25.1; ADR 0001 (cycle 1).

Subcommands:
    parsem [--config PATH]                # back-compat — runs the server
    parsem serve [--config PATH] [--host H] [--port P]
    parsem add <url|file> [--config PATH]

Config is loaded via `loaden` from `~/.config/parsem/config.yaml`
(or `--config PATH`). The bundled default template is written there
on first run so the user has a file to edit. Env vars referenced via
`${VAR:-default}` inside the YAML remain a clean override surface
for ops (Docker, unRAID).
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from parsem.config import (
    PROJECT_ROOT,
    Paths,
    Settings,
    ensure_library_layout,
    load_settings,
)
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


def build_app(
    settings: Settings | None = None,
    *,
    paths: Paths | None = None,
) -> FastAPI:
    """Build the FastAPI app. Pass `settings` to use the full loaden-
    loaded config, or `paths` for a paths-only build (tests). With
    neither, loads from the default config path."""
    if settings is None and paths is None:
        settings = load_settings()
    resolved_paths = paths or (settings.paths if settings else None)
    assert resolved_paths is not None  # one of the two branches set it
    ensure_library_layout(resolved_paths)
    conn = connect(resolved_paths.db_path)
    migrate(conn)
    welcome_id = _ensure_welcome_seeded(conn)
    state = build_reader_state_for_document(
        conn, welcome_id, warm_chunks=RESUME_WARM_CHUNKS_DEFAULT
    )
    assert state is not None  # welcome doc was just seeded; must exist
    return create_app(
        state,
        db=conn,
        originals_dir=resolved_paths.originals_dir,
        inbound_raw_dir=resolved_paths.inbound_raw_dir,
        ingest_settings=settings.ingest if settings else None,
        presentation_settings=settings.presentation if settings else None,
    )


def _cmd_serve(args: argparse.Namespace, *, runner: Callable[..., Any]) -> int:
    settings = load_settings(args.config)
    # CLI flags override config-file values when explicitly passed.
    host = args.host if args.host is not None else settings.server.host
    port = args.port if args.port is not None else settings.server.port
    runner(build_app(settings), host=host, port=port)
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    """`parsem add <url|file>` — drop into inbound/raw/ without going
    through the server. Ductile's folderwatch on inbound/raw/ knocks
    Parsem to ingest (ADR 0002). If ductile isn't watching, the file
    waits in inbound/raw/ until ductile catches up."""
    settings = load_settings(args.config)
    paths = settings.paths
    ensure_library_layout(paths)
    target_dir = paths.inbound_raw_dir
    source = args.target

    if source.startswith(("http://", "https://")):
        try:
            fetched = fetch(
                source,
                timeout_seconds=settings.ingest.url_timeout_seconds,
                max_bytes=settings.ingest.url_max_bytes,
            )
        except UrlFetchError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        target = unique_inbound_path(target_dir, fetched.suggested_filename)
        target.write_bytes(fetched.content)
        print(f"queued: {target.name}")
        return 0

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
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to YAML config (default: ~/.config/parsem/config.yaml)",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="run the web server")
    p_serve.add_argument(
        "--config", type=Path, default=None,
        help="Path to YAML config (default: ~/.config/parsem/config.yaml)",
    )
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)

    p_add = sub.add_parser("add", help="drop a URL or file into inbound/raw/")
    p_add.add_argument(
        "--config", type=Path, default=None,
        help="Path to YAML config (default: ~/.config/parsem/config.yaml)",
    )
    p_add.add_argument("target", help="URL or path to a local file")

    args = parser.parse_args(argv)
    if args.cmd == "add":
        return _cmd_add(args)
    if args.cmd in (None, "serve"):
        if args.cmd is None:
            args.host = None
            args.port = None
        return _cmd_serve(args, runner=_runner)
    parser.error(f"unknown command: {args.cmd}")
    return 2  # unreachable; argparse exits before
