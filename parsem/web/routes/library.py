"""Library route. Spec: parsem-spec.md §9.1, §22.

Beads: Parsem-3z8 (GET /library v1), Parsem-eci (POST /documents/{id}/delete),
Parsem-kwq (POST /documents/{id}/rename).

Handlers stay thin — the SQL helpers in `parsem.store.documents` carry
ordering and cascade semantics; here we only orchestrate transport and
side effects (file unlink, in-memory state reset on self-delete, partial
fragment rendering on rename).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from parsem.store.documents import (
    delete_document,
    list_library_rows,
    load_document,
    progress_percent_for_document,
    rename_document,
)
from parsem.web.state import empty_reader_state

_TITLE_MAX_LEN = 200


class RenameBody(BaseModel):
    title: str


router = APIRouter()


@router.get("/library", response_class=HTMLResponse)
def get_library(request: Request) -> HTMLResponse:
    rows = list_library_rows(request.app.state.db)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "library.html",
        {"rows": rows, "title_max_len": _TITLE_MAX_LEN},
    )


@router.post("/documents/{document_id}/delete")
def post_delete(document_id: int, request: Request) -> RedirectResponse:
    """Hard-delete the document and its dependents (cascade) plus the
    original .md file. 404 if the id is unknown. If the document being
    deleted is the one currently held in `app.state.reader`, swap to
    the empty placeholder so the next reader-open rebuilds from DB."""
    conn = request.app.state.db
    if not delete_document(conn, document_id):
        raise HTTPException(status_code=404, detail="Document not found")

    originals_dir: Path = request.app.state.originals_dir
    (originals_dir / f"{document_id}.md").unlink(missing_ok=True)

    if request.app.state.reader.document_id == document_id:
        request.app.state.reader = empty_reader_state(conn)

    return RedirectResponse(url="/library", status_code=302)


@router.post("/documents/{document_id}/rename", response_class=HTMLResponse)
def post_rename(
    document_id: int, request: Request, body: RenameBody
) -> HTMLResponse:
    """Inline-rename a library row. Returns the updated `<tr>` partial
    so the JS layer can outerHTML-swap a single row instead of
    reloading the page. Validation: trim whitespace, then non-empty
    and ≤200 chars; both reject with 422."""
    conn = request.app.state.db
    doc = load_document(conn, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title cannot be empty.")
    if len(title) > _TITLE_MAX_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Title cannot exceed {_TITLE_MAX_LEN} characters.",
        )

    now = datetime.now(UTC)
    rename_document(conn, document_id, title=title, now=now)
    updated = replace(doc, title=title, updated_at=now)
    progress = progress_percent_for_document(conn, document_id)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "_library_row.html",
        {
            "doc": updated,
            "progress_percent": progress,
            "title_max_len": _TITLE_MAX_LEN,
        },
    )
