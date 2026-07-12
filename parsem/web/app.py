"""FastAPI app factory. Spec: parsem-spec.md §17.1, §22; ADR 0002.

The eventing model is ductile-driven: ductile folderwatch on
`inbound/raw/` and filewatch on `inbound/converted/` knock the two
`/ingest/{raw,converted}-arrived` endpoints. Parsem owns no watchers
and runs no background threads for I/O.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from parsem.config import DuctileSettings, IngestSettings, PresentationSettings
from parsem.web.db_session import build_provider
from parsem.web.routes.arrivals import router as arrivals_router
from parsem.web.routes.assets import router as assets_router
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
    db_path: str | Path | None = None,
    originals_dir: Path,
    inbound_raw_dir: Path | None = None,
    inbound_converted_dir: Path | None = None,
    ingest_settings: IngestSettings | None = None,
    presentation_settings: PresentationSettings | None = None,
    ductile_settings: DuctileSettings | None = None,
    notes_dir: Path | None = None,
) -> FastAPI:
    """Build the FastAPI app wired to a ReaderState plus the SQLite
    connection and the on-disk paths. Callers (CLI + tests) own
    directory creation via `parsem.config.ensure_library_layout`;
    this factory assumes the contract is in place.

    `db` is the durable connection: it seeds/migrates at startup and
    backs the reader's single-session `EventLog`. `db_path`, when a real
    file path is supplied (production), switches the *stateless* routes
    (ingest, arrivals, library, assets) onto a fresh connection per
    request via `parsem.web.db_session` — the fix for concurrent-ingest
    corruption. Omitting `db_path` (tests / `:memory:`) keeps every
    request on the shared `db`, so the suite is unaffected.

    `presentation_settings` supplies the reader's no-localStorage
    appearance defaults (spec §15.3, claude-rdk); when omitted the
    shipped defaults are used (tests that don't care about it).

    `ductile_settings` is the gateway endpoint for user-initiated URL
    submission via `/ingest/url` (ADR 0003, bd claude-5fp). When the
    base_url is empty, `/ingest/url` returns 502 with a clear reason —
    URL ingest is disabled, but the rest of the app works fine."""
    raw_dir = inbound_raw_dir or originals_dir.parent / "inbound" / "raw"
    converted_dir = inbound_converted_dir or originals_dir.parent / "inbound" / "converted"

    app = FastAPI(title="Parsem", docs_url=None, redoc_url=None)
    app.state.reader = state
    # Durable connection — reader session + startup seed/migrate.
    app.state.db = db
    # Stateless routes acquire connections through this provider: fresh
    # per-request for a real file path, shared otherwise (db_session).
    app.state.db_provider = build_provider(db, db_path)
    app.state.originals_dir = originals_dir
    app.state.inbound_raw_dir = raw_dir
    app.state.inbound_converted_dir = converted_dir
    # Notes-export destination (notes-export). None disables on-disk
    # export — the note still persists to the event log + projection;
    # only the markdown file + its open-link are skipped. Defaults to
    # originals' sibling `notes/` so a paths-only build (tests that don't
    # pass notes_dir) still has a sane writable location.
    app.state.notes_dir = notes_dir or originals_dir.parent / "notes"
    app.state.ingest_callback_token = (
        ingest_settings.callback_token if ingest_settings else ""
    )
    app.state.ductile_settings = ductile_settings or DuctileSettings(
        base_url="", api_token=""
    )
    app.state.presentation = presentation_settings or PresentationSettings.default()
    app.state.templates = Jinja2Templates(directory=_TEMPLATES_DIR)
    # Library v2 tile slug needs date + source-type display helpers
    # (ADR 0005, bd Parsem-7wu.2). Registered once at startup.
    from parsem.web.template_filters import register as _register_filters
    _register_filters(app.state.templates)
    app.include_router(library_router)
    app.include_router(reader_router)
    app.include_router(ingest_router)
    app.include_router(arrivals_router)
    app.include_router(assets_router)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse(url="/library", status_code=302)

    return app
