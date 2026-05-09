"""FastAPI app factory. Spec: parsem-spec.md §17.1, §22; beads Parsem-wym, Parsem-cwj."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from parsem.web.routes.reader import router as reader_router
from parsem.web.routes.upload import router as upload_router
from parsem.web.state import ReaderState

_WEB_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


def create_app(
    state: ReaderState,
    *,
    db: sqlite3.Connection,
    originals_dir: Path,
) -> FastAPI:
    """Build a FastAPI app wired to a ReaderState plus the SQLite
    connection and the directory where uploaded original .md files
    live. The connection is shared with the EventLog (which the state
    already holds) — having it on `app.state` lets the upload pipeline
    and the doc-switch GET handler write/read documents without going
    through the reader's event_log."""
    originals_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="Parsem", docs_url=None, redoc_url=None)
    app.state.reader = state
    app.state.db = db
    app.state.originals_dir = originals_dir
    app.state.templates = Jinja2Templates(directory=_TEMPLATES_DIR)
    app.include_router(reader_router)
    app.include_router(upload_router)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/")
    def root() -> RedirectResponse:
        # Until 3z8 (library) lands, "/" is the upload form. After 3z8
        # it'll redirect to /library instead.
        return RedirectResponse(url="/upload", status_code=302)

    return app
