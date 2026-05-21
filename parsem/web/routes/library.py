"""Library route. Spec: parsem-spec.md §9.1, §22.

Beads: Parsem-3z8 (GET /library v1), Parsem-eci (POST /documents/{id}/delete),
Parsem-kwq (POST /documents/{id}/rename), Parsem-pnk + claude-m4l
(POST /documents/{id}/retry-parse — "Retry" on failed, "Re-chunk" on ready).

Handlers stay thin — the SQL helpers in `parsem.store.documents` carry
ordering and cascade semantics; the re-parse/chunk pipeline lives in
`parsem.web.ingest`. Here we only orchestrate transport and side
effects (file unlink, in-memory state reset on self-delete, partial
fragment rendering on rename, delegating the re-parse on retry-parse).
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
    DEFAULT_SEGMENT,
    DEFAULT_SORT,
    VALID_SEGMENTS,
    VALID_SORTS,
    LibraryRow,
    compute_drawer_sections,
    compute_silhouette_buckets,
    delete_document,
    derive_source_domain,
    list_library_rows,
    load_chunk_ratings_dense,
    load_document,
    load_section_layout,
    progress_percent_for_document,
    rename_document,
)
from parsem.store.tags import list_tags_for_doc
from parsem.web.ingest import reparse_document
from parsem.web.state import empty_reader_state

_TITLE_MAX_LEN = 200


class RenameBody(BaseModel):
    title: str


router = APIRouter()


@router.get("/library", response_class=HTMLResponse)
def get_library(
    request: Request,
    segment: str = DEFAULT_SEGMENT,
    sort: str = DEFAULT_SORT,
) -> HTMLResponse:
    """Library v2 (ADR 0005, bd Parsem-7wu.4). Query params drive the
    control strip: `segment` ∈ {all, unread, in_progress, finished}
    picks the reading-state filter; `sort` ∈ {last_opened,
    recently_added, title_az, longest} picks the order.

    Unknown values fall back to the defaults silently — keeps the
    page robust against bookmarked URLs from a future schema and
    against bad localStorage carry-over.
    """
    if segment not in VALID_SEGMENTS:
        segment = DEFAULT_SEGMENT
    if sort not in VALID_SORTS:
        sort = DEFAULT_SORT
    rows = list_library_rows(request.app.state.db, segment=segment, sort=sort)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "rows": rows,
            "title_max_len": _TITLE_MAX_LEN,
            # Library shares the reader's appearance bootstrap + "Aa"
            # panel (claude-rdk, spec §15.3).
            "presentation": request.app.state.presentation,
            "current_segment": segment,
            "current_sort": sort,
        },
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
    # Assemble the same LibraryRow the page loop sees so the partial
    # gets a faithful single-row context (ADR 0005, Parsem-7wu.2).
    progress = progress_percent_for_document(conn, document_id)
    chunk_ratings = load_chunk_ratings_dense(conn, document_id, doc.total_chunks)
    state_row = conn.execute(
        "SELECT current_position, high_water_position,"
        " updated_at AS last_opened_at"
        " FROM reading_state WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    current_position = state_row["current_position"] if state_row else 0
    high_water = state_row["high_water_position"] if state_row else 0
    last_opened = (
        datetime.fromisoformat(state_row["last_opened_at"])
        if state_row and state_row["last_opened_at"]
        else None
    )
    pin_count_row = conn.execute(
        "SELECT COUNT(*) AS n FROM pins WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    total_seconds_row = conn.execute(
        "SELECT COALESCE(SUM(estimated_read_seconds), 0) AS s"
        " FROM chunks WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    section_layout = (
        load_section_layout(conn, document_id)
        if updated.status == "ready" and (updated.total_chunks or 0) > 0
        else []
    )
    row = LibraryRow(
        document=updated,
        progress_percent=progress,
        chunk_ratings=chunk_ratings,
        source_domain=derive_source_domain(
            updated.source_type, updated.original_path
        ),
        ingest_date=updated.created_at,
        last_opened=last_opened,
        pin_count=pin_count_row["n"] if pin_count_row else 0,
        total_reading_seconds=float(total_seconds_row["s"]) if total_seconds_row else 0.0,
        tags=list_tags_for_doc(conn, document_id),
        section_layout=section_layout,
        silhouette_buckets=compute_silhouette_buckets(chunk_ratings, high_water),
        current_position=current_position,
        high_water_position=high_water,
        drawer_sections=compute_drawer_sections(
            section_layout, chunk_ratings, high_water
        ),
    )
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "_library_tile.html",
        {
            "row": row,
            "title_max_len": _TITLE_MAX_LEN,
        },
    )


@router.post("/documents/{document_id}/retry-parse")
def post_retry_parse(document_id: int, request: Request) -> RedirectResponse:
    """Re-run the parse/chunk pipeline on a document's stored markdown.
    Backs the library "Retry" button (failed docs) and the "Re-chunk"
    button (ready docs); the work — wipe, re-parse, re-anchor the
    reading position — lives in `parsem.web.ingest.reparse_document`.
    Spec §17.2; beads Parsem-pnk + claude-m4l."""
    conn = request.app.state.db
    if load_document(conn, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    reparse_document(
        conn,
        document_id=document_id,
        originals_dir=request.app.state.originals_dir,
        now=datetime.now(UTC),
    )
    return RedirectResponse(url="/library", status_code=302)
