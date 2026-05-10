"""FastAPI app factory. Spec: parsem-spec.md §17.1, §22; ADR 0001 (cycle 1)."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from parsem.config import IngestSettings
from parsem.ingest.url_fetch import DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT_SECONDS
from parsem.ingest.watcher import start as start_watcher
from parsem.web.routes.ingest import router as ingest_router
from parsem.web.routes.library import router as library_router
from parsem.web.routes.reader import router as reader_router
from parsem.web.state import ReaderState

_WEB_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


def create_app(
    state: ReaderState,
    *,
    db: sqlite3.Connection,
    originals_dir: Path,
    inbound_raw_dir: Path | None = None,
    ingest_settings: IngestSettings | None = None,
    enable_watcher: bool = True,
) -> FastAPI:
    """Build the FastAPI app wired to a ReaderState plus the SQLite
    connection and the on-disk paths. Callers (CLI + tests) own
    directory creation via `parsem.config.ensure_library_layout`;
    this factory assumes the contract is in place.

    `enable_watcher` is False in tests to keep the suite deterministic
    (the watcher runs in a background thread). Cycle 2 will add the
    `inbound_converted_dir` parameter alongside the Marker integration.
    """
    raw_dir = inbound_raw_dir or originals_dir.parent / "inbound" / "raw"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        observer = None
        if enable_watcher:
            observer = start_watcher(raw_dir, conn=db, originals_dir=originals_dir)
        try:
            yield
        finally:
            if observer is not None:
                observer.stop()
                observer.join(timeout=5)

    app = FastAPI(title="Parsem", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.reader = state
    app.state.db = db
    app.state.originals_dir = originals_dir
    app.state.inbound_raw_dir = raw_dir
    app.state.url_timeout_seconds = (
        ingest_settings.url_timeout_seconds if ingest_settings else DEFAULT_TIMEOUT_SECONDS
    )
    app.state.url_max_bytes = (
        ingest_settings.url_max_bytes if ingest_settings else DEFAULT_MAX_BYTES
    )
    app.state.templates = Jinja2Templates(directory=_TEMPLATES_DIR)
    app.include_router(library_router)
    app.include_router(reader_router)
    app.include_router(ingest_router)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse(url="/library", status_code=302)

    return app
