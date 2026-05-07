"""Reader routes. Spec: parsem-spec.md §22 (simplified Phase 1 paths per Parsem-wym).

Routes are pure transport: each handler reads/mutates ReaderState and calls
domain helpers. No bucket math, no chunking, no business rules in here.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from parsem.domain.economy import cycle_pin, try_reveal
from parsem.web.state import ReaderState


class RateBody(BaseModel):
    rating: int = Field(ge=1, le=5)


router = APIRouter()


def _state(request: Request) -> ReaderState:
    return request.app.state.reader


def _render_reader(
    request: Request,
    state: ReaderState,
    *,
    bucket_empty: bool = False,
) -> HTMLResponse:
    templates = request.app.state.templates
    context = {
        "chunk": state.chunks[state.current_position],
        "bucket_empty": bucket_empty,
        "seconds_until_token": state.bucket_config.regen_seconds,
    }
    return templates.TemplateResponse(request, "reader.html", context)


@router.get("/reader", response_class=HTMLResponse)
def get_reader(request: Request) -> HTMLResponse:
    return _render_reader(request, _state(request))


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
        state.event_log.pin_set(
            document_id=state.document_id,
            chunk_id=chunk_pos,
            color_id=new_color,
            created_at=now,
        )
    return _render_reader(request, state)


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
    return _render_reader(request, state)


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
    return _render_reader(request, state)


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
    return _render_reader(request, state, bucket_empty=outcome.reason == "bucket_empty")
