"""Upload route. Spec: parsem-spec.md §17.1, §17.2, §22; bead Parsem-cwj.

Synchronous ingestion pipeline:
    upload .md
      → INSERT documents row (status='processing', placeholder path)
      → write file to data/originals/{id}.md
      → UPDATE documents.original_path
      → parse + chunk
      → INSERT chunks + sections, mark ready
      → 302 to /documents/{id}/reader

Empty markdown and parse exceptions are caught and recorded as
status='failed' with a `failure_reason`; the user is redirected to
`/` (which redirects to `/upload` until 3z8 ships the library).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from parsem.domain.chunking import ChunkingConfig, chunk
from parsem.parse.markdown_parse import parse
from parsem.store.documents import (
    insert_chunks_and_sections,
    insert_document,
    mark_document_failed,
    mark_document_ready,
    update_document_original_path,
)

router = APIRouter()


@router.get("/upload", response_class=HTMLResponse)
def get_upload(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "upload.html", {})


@router.post("/upload")
async def post_upload(
    request: Request, file: UploadFile | None = None
) -> RedirectResponse:
    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")
    if not file.filename.lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files are accepted.")

    raw_bytes = await file.read()
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail=f"File is not valid UTF-8: {exc}"
        ) from exc

    title = Path(file.filename).stem
    conn = request.app.state.db
    originals_dir: Path = request.app.state.originals_dir
    now = datetime.now(UTC)

    document_id = insert_document(
        conn,
        title=title,
        original_path=f"data/originals/pending-{title}.md",
        status="processing",
        now=now,
    )
    file_path = originals_dir / f"{document_id}.md"
    file_path.write_text(text, encoding="utf-8")
    update_document_original_path(
        conn, document_id, original_path=str(file_path), now=now
    )

    try:
        output = chunk(parse(text), ChunkingConfig())
    except Exception as exc:
        mark_document_failed(
            conn, document_id, reason=f"Parse failed: {exc}", now=now
        )
        return RedirectResponse(url="/", status_code=302)

    if not output.chunks:
        mark_document_failed(
            conn, document_id, reason="Document is empty.", now=now
        )
        return RedirectResponse(url="/", status_code=302)

    insert_chunks_and_sections(
        conn,
        document_id=document_id,
        chunks=output.chunks,
        sections=output.sections,
        now=now,
    )
    mark_document_ready(
        conn, document_id, total_chunks=len(output.chunks), now=now
    )
    return RedirectResponse(
        url=f"/documents/{document_id}/reader", status_code=302
    )
