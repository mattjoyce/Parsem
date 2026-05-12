"""POST /ingest — drop point for the ingest pipeline.

Spec: ADR docs/adr/0001-nas-ingest-pipeline.md, 0002; bd claude-als.

Single endpoint, two content-types:

- `multipart/form-data` with a `file` field — direct file upload from
  the library page form. Writes to `inbound/raw/<safe-filename>` and
  then runs `process_raw_arrival` inline (the same handler ductile's
  folderwatch knocks): a standalone `parsem serve` has no watcher
  draining `inbound/raw/`, so the web upload self-ingests. Idempotent —
  if ductile *also* knocks for the same file it's a content-hash-dedup
  no-op. Redirects to `/library`.
- `application/json` with `{"url": "..."}` — URL ingest from CLI or
  programmatic clients. Writes to `inbound/raw/` and returns 202 with
  the queued filename (still watcher-driven; CLI clients rely on the
  "queued" contract).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from parsem.ingest.arrivals import process_raw_arrival
from parsem.ingest.paths import unique_inbound_path
from parsem.ingest.url_fetch import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    UrlFetchError,
    fetch,
)

router = APIRouter()


class IngestUrlBody(BaseModel):
    url: str


@router.post("/ingest", response_model=None)
async def post_ingest(
    request: Request, file: UploadFile | None = None
) -> RedirectResponse | JSONResponse:
    """Drop a file or URL into `inbound/raw/`. Form file upload then
    runs `process_raw_arrival` inline so a standalone server still turns
    the upload into a document; it redirects to /library. JSON {url}
    submission writes the fetched bytes and returns 202 with the queued
    filename (watcher-driven)."""
    inbound_raw_dir: Path = request.app.state.inbound_raw_dir
    timeout = getattr(request.app.state, "url_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    max_bytes = getattr(request.app.state, "url_max_bytes", DEFAULT_MAX_BYTES)

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        body_data = await request.json()
        body = IngestUrlBody.model_validate(body_data)
        try:
            fetched = fetch(body.url, timeout_seconds=timeout, max_bytes=max_bytes)
        except UrlFetchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target = unique_inbound_path(inbound_raw_dir, fetched.suggested_filename)
        target.write_bytes(fetched.content)
        return JSONResponse(
            status_code=202, content={"queued": target.name}
        )

    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")
    raw_bytes = await file.read()
    target = unique_inbound_path(inbound_raw_dir, file.filename)
    target.write_bytes(raw_bytes)
    # Self-ingest: no ductile folderwatch in a standalone server, so
    # run the arrival handler now (idempotent — a later ductile knock
    # for the same file dedups on content hash). claude-als.
    process_raw_arrival(
        target,
        conn=request.app.state.db,
        originals_dir=request.app.state.originals_dir,
    )
    return RedirectResponse(url="/library", status_code=302)
