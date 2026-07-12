"""Ductile-driven ingest callbacks. Spec: ADR 0002.

Two endpoints — both thin adapters over `parsem.ingest.arrivals`:

- `POST /ingest/raw-arrived`        — ductile folderwatch knock for inbound/raw/
- `POST /ingest/converted-arrived`  — ductile filewatch knock for inbound/converted/

Auth: optional bearer token. When `app.state.ingest_callback_token` is
the empty string we accept any caller (dev). When set, requests must
present `Authorization: Bearer <token>` matching exactly. The choice
to make auth opt-in keeps the dev story painless while letting
production turn it on with a single config edit.

Idempotency: the underlying `process_*_arrival` functions are safe to
call repeatedly with the same path — content-hash dedup on the raw side,
status-check on the converted side. Ductile retries can fire freely.
"""

from __future__ import annotations

import secrets
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from parsem.ingest.arrivals import (
    process_converted_arrival,
    process_raw_arrival,
)
from parsem.web.db_session import DbConn

router = APIRouter()


class ArrivalBody(BaseModel):
    """Both endpoints accept the same shape: an absolute path the
    server can stat. Ductile passes paths because the file already
    lives on the shared NAS mount; no need to upload bytes."""

    path: str


def _check_token(request: Request) -> None:
    """Bearer-token gate. Empty configured token = open. Mismatched
    token = 401. We use `secrets.compare_digest` to keep the comparison
    constant-time, since the token is a long-lived secret."""
    expected: str = getattr(request.app.state, "ingest_callback_token", "") or ""
    if not expected:
        return
    header = request.headers.get("authorization", "")
    presented = header[len("Bearer ") :] if header.startswith("Bearer ") else ""
    if not presented or not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="invalid or missing token")


@router.post("/ingest/raw-arrived")
def post_raw_arrived(
    request: Request,
    body: ArrivalBody,
    conn: DbConn,
    _: None = Depends(_check_token),
) -> JSONResponse:
    """Ductile knocks here when something lands in inbound/raw/. The
    response carries the closed action vocabulary (`ingested`,
    `submit_to_docling`, `duplicate`, `unsupported`); the ductile DSL
    branches on it. Path is read from the bind-mounted NAS share —
    Parsem and ductile must agree on the path namespace.

    Runs on a per-request connection (`conn`): a batch drop firing many
    of these concurrently is exactly what corrupted a shared connection
    (bd Parsem-7wu.5 follow-up)."""
    result = process_raw_arrival(
        Path(body.path),
        conn=conn,
        originals_dir=request.app.state.originals_dir,
    )
    return JSONResponse(content=asdict(result))


@router.post("/ingest/converted-arrived")
def post_converted_arrived(
    request: Request,
    body: ArrivalBody,
    conn: DbConn,
    _: None = Depends(_check_token),
) -> JSONResponse:
    """Ductile knocks here after Marker writes <doc_id>.md into
    inbound/converted/. The atomic-write contract guarantees the .md
    is the LAST artifact to land, so by here the sidecar JSON and any
    images dir are already in place. Per-request connection — see
    `post_raw_arrived`."""
    result = process_converted_arrival(
        Path(body.path),
        conn=conn,
        originals_dir=request.app.state.originals_dir,
    )
    return JSONResponse(content=asdict(result))
