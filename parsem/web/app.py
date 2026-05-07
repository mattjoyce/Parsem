"""FastAPI app factory. Spec: parsem-spec.md §18, §22; bead Parsem-wym."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from parsem.web.routes.reader import router as reader_router
from parsem.web.state import ReaderState

_WEB_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


def create_app(state: ReaderState) -> FastAPI:
    """Build a FastAPI app wired to a single ReaderState instance.

    Phase 1 has one document-in-memory; the state is held on app.state and
    routes mutate it in place. Phase 2 swaps state for a SQLite-backed
    repository behind the same handler interfaces.
    """
    app = FastAPI(title="Parsem", docs_url=None, redoc_url=None)
    app.state.reader = state
    app.state.templates = Jinja2Templates(directory=_TEMPLATES_DIR)
    app.include_router(reader_router)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    return app
