"""Library route. Spec: parsem-spec.md §9.1, §22.

Beads: Parsem-3z8 (GET /library v1), Parsem-eci (POST /documents/{id}/delete),
Parsem-kwq (POST /documents/{id}/rename), Parsem-pnk (POST /documents/{id}/retry-parse).

Handlers stay thin — the SQL helpers in `parsem.store.documents` carry
ordering and cascade semantics; the parse pipeline lives in
`parsem.web.ingest`. Here we only orchestrate transport and side
effects (file unlink, in-memory state reset on self-delete, partial
fragment rendering on rename, file re-read on retry-parse).
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from parsem.ingest import layout
from parsem.store.documents import (
    delete_document,
    delete_document_chunks_and_sections,
    list_library_rows,
    load_chunk_ratings_dense,
    load_document,
    mark_document_failed,
    progress_percent_for_document,
    rename_document,
)
from parsem.store.projections_cache import (
    get_chunk_piece_hashes_for_document,
    reanchor_reading_state,
)
from parsem.web.ingest import parse_and_persist
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
    # A document is a directory (originals/<id>/) — wipe the whole thing
    # (markdown, source.pdf, extraction.json, images/). ignore_errors so
    # the welcome doc (no dir under originals/) and partial states don't
    # block the delete.
    shutil.rmtree(layout.document_dir(originals_dir, document_id), ignore_errors=True)

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
    chunk_ratings = load_chunk_ratings_dense(conn, document_id, doc.total_chunks)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "_library_row.html",
        {
            "doc": updated,
            "progress_percent": progress,
            "chunk_ratings": chunk_ratings,
            "title_max_len": _TITLE_MAX_LEN,
        },
    )


@router.post("/documents/{document_id}/retry-parse")
def post_retry_parse(document_id: int, request: Request) -> RedirectResponse:
    """Re-run the parse pipeline on the persisted original .md. Wipes
    any partial chunks/sections from the prior attempt, re-parses, and
    flips status back to 'ready' on success. Re-failures stay 'failed'
    with the new reason. Spec §17.2; bead Parsem-pnk."""
    conn = request.app.state.db
    if load_document(conn, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")

    originals_dir: Path = request.app.state.originals_dir
    file_path = layout.markdown_path(originals_dir, document_id)
    now = datetime.now(UTC)
    if not file_path.exists():
        mark_document_failed(
            conn, document_id, reason="Original file missing.", now=now
        )
        return RedirectResponse(url="/library", status_code=302)

    text = file_path.read_text(encoding="utf-8")
    # Capture the OLD chunks' piece-hash sets BEFORE the wipe — needed
    # to re-anchor reading_state after the new chunking_run lands
    # (claude-jtu). Empty list when the doc has never been parsed
    # successfully (no prior chunks); reanchor below short-circuits.
    old_chunk_piece_hashes = get_chunk_piece_hashes_for_document(conn, document_id)
    delete_document_chunks_and_sections(conn, document_id)
    success = parse_and_persist(conn, document_id=document_id, text=text, now=now)
    if success and old_chunk_piece_hashes:
        reanchor_reading_state(
            conn,
            document_id=document_id,
            old_chunks_piece_hashes=old_chunk_piece_hashes,
            now=now,
        )
        conn.commit()
    return RedirectResponse(url="/library", status_code=302)
