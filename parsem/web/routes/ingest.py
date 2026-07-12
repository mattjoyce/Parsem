"""Ingest routes — drop points for the ingest pipeline.

Two endpoints:

- `POST /ingest` (multipart/form-data, `file=`): direct file upload
  from the library page. Writes to `inbound/raw/<safe-filename>`. A
  `.md` upload is then self-ingested inline via `process_raw_arrival`
  so a standalone `parsem serve` with no ductile watcher still turns
  the upload into a document; idempotent under content-hash dedup. A
  `.pdf` (or anything else) is left QUEUED for ductile's folderwatch.
  Redirects to `/library`.

  The legacy JSON `{url}` branch of this route has been retired and
  now returns **410 Gone** with a hint pointing at `/ingest/url`.

- `POST /ingest/url` (application/json, `{url: string}`): user-
  initiated URL submission. Inserts a `converting` documents row,
  submits to ductile's firecrawl plugin via `submit_url`, returns
  202 with `{document_id, doc_id, action}`. Per ADR 0003 the
  outbound call is synchronous and bounded by this request.

  bd: claude-5fp.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from parsem.ingest.arrivals import process_raw_arrival
from parsem.ingest.paths import unique_inbound_path
from parsem.ingest.url_submit import UrlSubmitError, submit_url
from parsem.web.db_session import DbConn

router = APIRouter()


@router.post("/ingest", response_model=None)
async def post_ingest(
    request: Request, conn: DbConn, file: UploadFile | None = None
) -> RedirectResponse | JSONResponse:
    """Form-file upload only. The legacy JSON `{url}` branch was
    retired in favour of `/ingest/url` (bd claude-5fp); JSON callers
    receive 410 Gone."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        raise HTTPException(
            status_code=410,
            detail="POST /ingest with JSON body is retired — use POST /ingest/url",
        )

    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")

    inbound_raw_dir: Path = request.app.state.inbound_raw_dir
    raw_bytes = await file.read()
    target = unique_inbound_path(inbound_raw_dir, file.filename)
    target.write_bytes(raw_bytes)
    # Self-ingest .md only: a standalone server has no ductile folderwatch
    # draining inbound/raw/, so run the arrival handler now (idempotent —
    # a later ductile knock for the same file dedups on content hash).
    # A .pdf is left queued: process_raw_arrival on it returns
    # `submit_to_docling` (and moves it to originals/<id>/source.pdf) —
    # and only ductile can dispatch the docling-pdf plugin. claude-als.
    if target.suffix.lower() == ".md":
        process_raw_arrival(
            target,
            conn=conn,
            originals_dir=request.app.state.originals_dir,
        )
    return RedirectResponse(url="/library", status_code=302)


@router.post("/ingest/url")
async def post_ingest_url(request: Request, conn: DbConn) -> JSONResponse:
    """User-initiated URL submission. Inserts a `converting` row, then
    calls ductile's firecrawl plugin. Returns 202 with the new
    document_id on success; 400 on bad input; 502 on ductile failure.

    ADR 0003: this outbound call is bounded by this request — no
    background retries, no queued state. If ductile is unreachable,
    the user sees a synchronous error and can retry."""
    try:
        body_data = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
    if not isinstance(body_data, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    url = str(body_data.get("url") or "").strip()

    try:
        result = submit_url(
            url,
            conn=conn,
            settings=request.app.state.ductile_settings,
            inbound_converted_dir=request.app.state.inbound_converted_dir,
        )
    except UrlSubmitError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"error": exc.kind, "reason": exc.reason},
        ) from exc

    return JSONResponse(
        status_code=202,
        content={
            "document_id": result.document_id,
            "doc_id": result.doc_id,
            "action": "submitted",
        },
    )
