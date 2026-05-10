"""POST /ingest — async drop point for the ingest pipeline.

Spec: ADR docs/adr/0001-nas-ingest-pipeline.md.

Single endpoint, two content-types:

- `multipart/form-data` with a `file` field — direct file upload from
  the library page form. Redirects to `/library` on success.
- `application/json` with `{"url": "..."}` — URL ingest from CLI or
  programmatic clients. Returns 202 with a JSON envelope.

Both write to `inbound/raw/<safe-filename>`. The filesystem-watcher
picks up the file and runs `parse_and_persist`. The endpoint never
parses inline — the parse is the watcher's job.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from parsem.ingest.paths import unique_inbound_path
from parsem.ingest.url_fetch import UrlFetchError, fetch

router = APIRouter()


class IngestUrlBody(BaseModel):
    url: str


@router.post("/ingest", response_model=None)
async def post_ingest(
    request: Request, file: UploadFile | None = None
) -> RedirectResponse | JSONResponse:
    """Drop a file or URL into `inbound/raw/`. Form submission redirects
    to /library; JSON submission returns 202 with the queued filename."""
    inbound_raw_dir: Path = request.app.state.inbound_raw_dir

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        body_data = await request.json()
        body = IngestUrlBody.model_validate(body_data)
        try:
            fetched = fetch(body.url)
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
    return RedirectResponse(url="/library", status_code=302)
