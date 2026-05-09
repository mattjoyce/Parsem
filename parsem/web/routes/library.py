"""Library route. Spec: parsem-spec.md §9.1, §22; bead Parsem-3z8.

Phase 2 v1: title, status, last-opened ordering. No progress %, no
heatmap strip, no per-row buttons yet — those land in Parsem-5oi /
Parsem-8p5 / Parsem-eci / Parsem-kwq / Parsem-pnk.

The handler is a pure read: list_documents_for_library handles the
ordering in SQL; this just renders.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from parsem.store.documents import list_documents_for_library

router = APIRouter()


@router.get("/library", response_class=HTMLResponse)
def get_library(request: Request) -> HTMLResponse:
    docs = list_documents_for_library(request.app.state.db)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "library.html", {"docs": docs})
