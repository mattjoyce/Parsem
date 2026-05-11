"""Document asset serving — images extracted alongside a converted PDF.

Spec: ADR 0001 (directory contract); bd claude-5h0.

A converted document's markdown references its figures by the relative
path `images/<file>`. The reader renders that into `<img
src="images/<file>">`, which the browser resolves against the reader
page URL — `/documents/{id}/reader` + `images/<file>` →
`/documents/{id}/images/<file>`. This route serves that path from
`originals/<doc_id>/images/<file>`.

Read-only, no auth (single-user local-first — same posture as the rest
of the reader surface). Path-traversal is the only thing guarded: the
resolved path must stay inside the document's images directory, and the
document row must exist.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from parsem.ingest import layout
from parsem.store.documents import load_document

router = APIRouter()


@router.get("/documents/{document_id}/images/{asset_path:path}")
def get_document_image(
    document_id: int, asset_path: str, request: Request
) -> FileResponse:
    """Serve `originals/<doc_id>/images/<asset_path>`. 404 on unknown
    document, traversal attempt, or missing file."""
    conn = request.app.state.db
    if load_document(conn, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")

    originals_dir: Path = request.app.state.originals_dir
    resolved = layout.asset_path(originals_dir, document_id, asset_path)
    if resolved is None or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(resolved)
