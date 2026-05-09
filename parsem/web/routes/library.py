"""Library route. Spec: parsem-spec.md §9.1, §22.

Beads: Parsem-3z8 (GET /library v1), Parsem-eci (POST /documents/{id}/delete).

Handlers stay thin — the SQL helpers in `parsem.store.documents` carry
ordering and cascade semantics; here we only orchestrate transport and
side effects (file unlink, in-memory state reset on self-delete).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from parsem.store.documents import (
    delete_document,
    list_documents_for_library,
    load_document,
)
from parsem.web.state import empty_reader_state

router = APIRouter()


@router.get("/library", response_class=HTMLResponse)
def get_library(request: Request) -> HTMLResponse:
    docs = list_documents_for_library(request.app.state.db)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "library.html", {"docs": docs})


@router.post("/documents/{document_id}/delete")
def post_delete(document_id: int, request: Request) -> RedirectResponse:
    """Hard-delete the document and its dependents (cascade) plus the
    original .md file. 404 if the id is unknown. If the document being
    deleted is the one currently held in `app.state.reader`, swap to
    the empty placeholder so the next reader-open rebuilds from DB."""
    conn = request.app.state.db
    if load_document(conn, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")

    delete_document(conn, document_id)

    originals_dir: Path = request.app.state.originals_dir
    (originals_dir / f"{document_id}.md").unlink(missing_ok=True)

    if request.app.state.reader.document_id == document_id:
        request.app.state.reader = empty_reader_state(conn)

    return RedirectResponse(url="/library", status_code=302)
