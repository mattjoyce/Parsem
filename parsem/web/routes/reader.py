"""Reader routes. Spec: parsem-spec.md §22 (simplified Phase 1 paths per Parsem-wym).

Routes are pure transport: each handler reads/mutates ReaderState and calls
domain helpers. No bucket math, no chunking, no business rules in here.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from parsem.domain.economy import cycle_pin, try_reveal
from parsem.store.documents import load_document
from parsem.web.state import ReaderState, build_reader_state_for_document
from parsem.web.view import build_reader_context

# Spec §20: resume.warm_chunks default. Mirrored here so the GET
# handler can swap in a freshly-loaded ReaderState without depending
# on parsem.cli (would create an import cycle).
_RESUME_WARM_CHUNKS_DEFAULT = 2


class RateBody(BaseModel):
    rating: int = Field(ge=1, le=5)


class JumpBody(BaseModel):
    direction: Literal["next", "prev"]
    # color_mode controls the pin filter:
    #   "any"               — every pin is a candidate; spec §8 `]` / `[`
    #   "same_as_current"   — only pins matching the colour of the current
    #                          chunk's pin; no-op when the current chunk
    #                          has no pin; spec §8 `}` / `{`
    color_mode: Literal["any", "same_as_current"] = "any"


router = APIRouter()


def _state(request: Request) -> ReaderState:
    return request.app.state.reader


def _render_full(request: Request, state: ReaderState) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "reader.html", build_reader_context(state))


def _render_partial(request: Request, state: ReaderState) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "_reader_main.html", build_reader_context(state))


@router.get("/documents/{document_id}/reader", response_class=HTMLResponse)
def get_document_reader(document_id: int, request: Request) -> HTMLResponse:
    """Open the requested document. If `app.state.reader` is already
    on this doc, render with the in-memory state (preserves session
    fields like `last_active_pin_color`); otherwise rebuild from DB
    and swap. 404 when the doc does not exist.

    Single-tab assumption: this swap is process-global. Two browser
    tabs visiting different docs will silently overwrite each other's
    `app.state.reader`. Multi-tab support is deferred to Parsem-2rp
    (per-document POST routes + version polling)."""
    state = _state(request)
    if state.document_id != document_id:
        new_state = build_reader_state_for_document(
            request.app.state.db,
            document_id,
            warm_chunks=_RESUME_WARM_CHUNKS_DEFAULT,
        )
        if new_state is None:
            raise HTTPException(status_code=404, detail="Document not found")
        request.app.state.reader = new_state
        state = new_state
    state.event_log.open_document(
        document_id=document_id, created_at=state.clock()
    )
    return _render_full(request, state)


@router.post("/documents/{document_id}/close")
def post_document_close(document_id: int, request: Request) -> Response:
    """Best-effort close_document log triggered by the client's
    pagehide/beforeunload sendBeacon. Spec §18.1; bead Parsem-8wj.

    Returns 204 unconditionally — the browser discards the response on
    sendBeacon, and a stale beacon for a deleted doc must not surface
    as an error. We swallow the missing-doc case rather than 404 it."""
    conn = request.app.state.db
    if load_document(conn, document_id) is None:
        return Response(status_code=204)
    state = _state(request)
    state.event_log.close_document(
        document_id=document_id, created_at=state.clock()
    )
    return Response(status_code=204)


@router.post("/pin", response_class=HTMLResponse)
def post_pin(request: Request) -> HTMLResponse:
    state = _state(request)
    chunk_pos = state.current_position
    new_color = cycle_pin(state.pin_colors.get(chunk_pos))
    now = state.clock()
    if new_color is None:
        state.pin_colors.pop(chunk_pos, None)
        state.event_log.pin_clear(
            document_id=state.document_id,
            chunk_id=chunk_pos,
            created_at=now,
        )
    else:
        state.pin_colors[chunk_pos] = new_color
        state.last_active_pin_color = new_color
        state.event_log.pin_set(
            document_id=state.document_id,
            chunk_id=chunk_pos,
            color_id=new_color,
            created_at=now,
        )
    return _render_partial(request, state)


def _find_jump_target(
    state: ReaderState, direction: str, color_mode: str
) -> int | None:
    """Return the chunk position of the next/prev pin under `color_mode`,
    with wrap-around. None when the jump would not move — no pins match,
    or the only matching pin is at `current_position`.

    Spec §8 keyboard table:
      "any"               — `]` / `[`  no colour filter, any pin matches
      "same_as_current"   — `}` / `{`  filter to pins of the same colour
                                       as the current chunk's pin; no-op
                                       when the current chunk has no pin
    """
    pins = state.pin_colors
    if color_mode == "same_as_current":
        current_color = pins.get(state.current_position)
        if current_color is None:
            return None
        pins = {p: c for p, c in pins.items() if c == current_color}
    if not pins:
        return None
    positions = sorted(pins)
    current = state.current_position
    if direction == "next":
        ahead = [p for p in positions if p > current]
        target = ahead[0] if ahead else positions[0]
    else:
        behind = [p for p in positions if p < current]
        target = behind[-1] if behind else positions[-1]
    return None if target == current else target


@router.post("/jump-to-pin", response_class=HTMLResponse)
def post_jump_to_pin(request: Request, body: JumpBody) -> HTMLResponse:
    state = _state(request)
    target = _find_jump_target(state, body.direction, body.color_mode)
    if target is None:
        return _render_partial(request, state)
    state.pre_jump_position = state.current_position
    state.current_position = target
    state.last_active_pin_color = state.pin_colors.get(target, state.last_active_pin_color)
    response = _render_partial(request, state)
    response.headers["X-Reveal-Outcome"] = "advanced_free"
    return response


@router.post("/return", response_class=HTMLResponse)
def post_return(request: Request) -> HTMLResponse:
    state = _state(request)
    if state.pre_jump_position is not None:
        state.current_position = state.pre_jump_position
        state.pre_jump_position = None
        response = _render_partial(request, state)
        response.headers["X-Reveal-Outcome"] = "advanced_free"
        return response
    return _render_partial(request, state)


@router.post("/rate", response_class=HTMLResponse)
def post_rate(request: Request, body: RateBody) -> HTMLResponse:
    state = _state(request)
    try:
        state.event_log.rate_effort(
            document_id=state.document_id,
            chunk_id=state.current_position,
            rating=body.rating,
            created_at=state.clock(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _render_partial(request, state)


@router.post("/conceal", response_class=HTMLResponse)
def post_conceal(request: Request) -> HTMLResponse:
    state = _state(request)
    if state.current_position > 0:
        new_position = state.current_position - 1
        state.event_log.conceal(
            document_id=state.document_id,
            chunk_id=new_position,
            created_at=state.clock(),
        )
        state.current_position = new_position
    return _render_partial(request, state)


@router.post("/reveal", response_class=HTMLResponse)
def post_reveal(request: Request) -> HTMLResponse:
    state = _state(request)
    now = state.clock()
    outcome = try_reveal(
        current_position=state.current_position,
        high_water_position=state.high_water_position,
        chunks_total=len(state.chunks),
        paid_reveal_times=state.paid_reveal_times,
        bucket_config=state.bucket_config,
        now=now,
    )
    if outcome.advanced:
        state.current_position = outcome.new_position
        if outcome.paid:
            state.high_water_position = outcome.new_position
            state.paid_reveal_times.append(now)
        state.event_log.reveal(
            document_id=state.document_id,
            chunk_id=outcome.new_position,
            created_at=now,
        )
    response = _render_partial(request, state)
    # Tells the JS layer how to react: smooth-settle on success, rejection
    # motion on bucket_empty. See spec §12.5 + Parsem-0if.
    response.headers["X-Reveal-Outcome"] = outcome.reason
    return response
