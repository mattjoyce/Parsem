"""Reader routes. Spec: parsem-spec.md §22 (simplified Phase 1 paths per Parsem-wym).

Routes are pure transport: each handler reads/mutates ReaderState and calls
domain helpers. No bucket math, no chunking, no business rules in here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from parsem.domain.economy import cycle_pin, try_reveal
from parsem.notes_export import render_notes_markdown, write_notes_file
from parsem.store.documents import load_chunks_for_document, load_document
from parsem.store.projections_cache import get_notes_for_document
from parsem.web.state import ReaderState, build_reader_state_for_document
from parsem.web.view import build_reader_context, document_title

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


class SetCurrentPositionBody(BaseModel):
    position: int


class NoteBody(BaseModel):
    # Empty / whitespace-only text clears the note on the current chunk.
    text: str


router = APIRouter()


def _state(request: Request) -> ReaderState:
    return request.app.state.reader


def _render_full(request: Request, state: ReaderState) -> HTMLResponse:
    templates = request.app.state.templates
    context = build_reader_context(state)
    # The no-FOUC bootstrap + "Aa" panel are page-level (live in
    # reader.html, outside the #reader-main partial), so only the full
    # render needs the presentation defaults — claude-rdk, spec §15.3.
    context["presentation"] = request.app.state.presentation
    return templates.TemplateResponse(request, "reader.html", context)


def _render_partial(request: Request, state: ReaderState) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "_reader_main.html", build_reader_context(state)
    )


@router.get("/documents/{document_id}/reader", response_class=HTMLResponse)
def get_document_reader(
    document_id: int,
    request: Request,
    chunk: int | None = Query(None),
) -> HTMLResponse:
    """Open the requested document. If `app.state.reader` is already
    on this doc, render with the in-memory state (preserves session
    fields like `last_active_pin_color`); otherwise rebuild from DB
    and swap. 404 when the doc does not exist.

    The optional ``?chunk=N`` deep-link param sets ``current_position``
    to N before rendering. Out-of-range values silently clamp to the
    nearest valid revealed chunk (``[0, high_water_position]``) — a
    shared URL must never 404 the receiver, even if their session is
    behind the linker's frontier. Forward-of-frontier deep-link is
    intentionally not supported (would bypass §8a.1 and the bucket).

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
    if chunk is not None:
        state.current_position = max(0, min(chunk, state.high_water_position))
    # Free Mode is page-load-scoped (Parsem-ci5). Every GET resets it
    # so a tab reload always returns to paced reading — the deliberate
    # default. The escape hatch is explicit and never sticky.
    state.free_mode = False
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


def _reject_past_frontier(state: ReaderState) -> None:
    """Free Mode (Parsem-ci5) lets Space advance ``current_position``
    past ``high_water``; the user can still SEE those chunks but the
    'view-only past frontier' rule forbids committing pin / rating
    state to them. 422 so the JS short-circuits silently."""
    if state.current_position > state.high_water_position:
        raise HTTPException(
            status_code=422,
            detail=(
                f"position {state.current_position} is past high_water "
                f"({state.high_water_position}); free-mode chunks are view-only"
            ),
        )


@router.post("/pin", response_class=HTMLResponse)
def post_pin(request: Request) -> HTMLResponse:
    state = _state(request)
    _reject_past_frontier(state)
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
    or None when no jump is available. No wrap-at-ends — `]` past the
    last pin and `[` before the first pin are both no-ops. Earlier
    wrap behaviour read as inverted in UAT (claude-axx.3).

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
        return ahead[0] if ahead else None
    behind = [p for p in positions if p < current]
    return behind[-1] if behind else None


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


@router.post("/set-current-position", response_class=HTMLResponse)
def post_set_current_position(
    request: Request, body: SetCurrentPositionBody
) -> HTMLResponse:
    """Move `current_position` backward (or to the frontier) without
    spending a token. Spec §8a.2 — chunk-body click and space-resume
    both land here.

    Validates the position against [0, high_water_position]. Forward
    of the frontier is rejected with 422 (the pointer model never
    lets reading skip ahead — §8a.1).

    State changes:
      - new == old current             : no-op
      - new == high_water_position     : current := new, pre_jump := None
                                          (reader is back at the frontier;
                                          no further `'`/Esc return makes
                                          sense)
      - otherwise                      : current := new; capture
                                          pre_jump_position := old current
                                          ONLY if pre_jump is currently
                                          null (§8a.3 — preserves the
                                          original spine across multiple
                                          back-clicks)

    No event log entry — pointer-only navigation does not write to the
    event log (§8a.3, mirrors `/jump-to-pin` which also does not log).
    """
    state = _state(request)
    if not 0 <= body.position < len(state.chunks):
        raise HTTPException(
            status_code=422,
            detail=f"position {body.position} out of range [0, {len(state.chunks) - 1}]",
        )
    if body.position > state.high_water_position:
        raise HTTPException(
            status_code=422,
            detail=(
                f"position {body.position} is past high_water "
                f"({state.high_water_position}); pointer cannot advance"
            ),
        )
    if body.position == state.current_position:
        return _render_partial(request, state)
    if body.position == state.high_water_position:
        state.current_position = body.position
        state.pre_jump_position = None
    else:
        if state.pre_jump_position is None:
            state.pre_jump_position = state.current_position
        state.current_position = body.position
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
    _reject_past_frontier(state)
    try:
        state.event_log.rate_effort(
            document_id=state.document_id,
            chunk_id=state.current_position,
            rating=body.rating,
            created_at=state.clock(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    state.chunk_ratings[state.current_position] = body.rating
    return _render_partial(request, state)


@router.post("/unrate", response_class=HTMLResponse)
def post_unrate(request: Request) -> HTMLResponse:
    """Clear the current chunk's rating. Mirrors `pin_clear`. Logs a
    `rate_clear` event so projection rebuild reproduces the wiped
    state. No-op when the chunk has no rating to clear (claude-axx.3
    UAT — clicking an empty rating dot must not log spurious events).
    """
    state = _state(request)
    _reject_past_frontier(state)
    chunk_id = state.current_position
    if chunk_id not in state.chunk_ratings:
        return _render_partial(request, state)
    state.event_log.rate_clear(
        document_id=state.document_id,
        chunk_id=chunk_id,
        created_at=state.clock(),
    )
    del state.chunk_ratings[chunk_id]
    return _render_partial(request, state)


def _export_document_notes(request: Request, state: ReaderState) -> str | None:
    """Rewrite the document's notes file from the live note set
    (notes-export). Best-effort: a write failure (e.g. an unmounted
    vault path) must NOT fail the note save — the note is already in the
    event log + projection, which are the source of truth. Returns
    "failed" so the caller can flag it via a response header; None on
    success or when export is disabled (no notes_dir configured)."""
    notes_dir = getattr(request.app.state, "notes_dir", None)
    if notes_dir is None:
        return None
    reader_url = str(
        request.url_for("get_document_reader", document_id=state.document_id)
    )
    try:
        write_notes_file(
            notes_dir=notes_dir,
            document_id=state.document_id,
            title=document_title(state.chunks),
            reader_url=reader_url,
            notes=state.chunk_notes,
            chunks=state.chunks,
            generated_at=state.clock().isoformat(),
        )
    except OSError:
        return "failed"
    return None


@router.get("/documents/{document_id}/notes")
def get_document_notes(document_id: int, request: Request) -> Response:
    """Serve a document's notes as a shareable markdown file (notes-
    export): YAML frontmatter about the parent document, then each noted
    chunk's link, prose, and note. Regenerated from the DB on each
    request (the projection is the source of truth, so this never goes
    stale). Served as text/plain so browsers display it inline and AI
    agents handed the URL get clean markdown. 404 if the doc is gone."""
    conn = request.app.state.db
    if load_document(conn, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    chunks = load_chunks_for_document(conn, document_id)
    notes = get_notes_for_document(conn, document_id)
    reader_url = str(request.url_for("get_document_reader", document_id=document_id))
    body = render_notes_markdown(
        title=document_title(chunks),
        document_id=document_id,
        reader_url=reader_url,
        notes=notes,
        chunks=chunks,
        generated_at=datetime.now(UTC).isoformat(),
    )
    return Response(content=body, media_type="text/plain; charset=utf-8")


@router.post("/note", response_class=HTMLResponse)
def post_note(request: Request, body: NoteBody) -> HTMLResponse:
    """Set or clear the current chunk's note, then rewrite the
    document's exported notes file (notes-export).

    A non-empty body sets/overwrites the note (`note_set`); an emptied
    editor clears it (`note_clear`), with a stale-DOM no-op guard mirror-
    ing /unrate. Notes are frontier-gated like pins/ratings — free-mode
    chunks past high_water are view-only (422). The export side effect is
    best-effort: it never fails the save (see `_export_document_notes`)."""
    state = _state(request)
    _reject_past_frontier(state)
    chunk_id = state.current_position
    now = state.clock()
    text = body.text.strip()
    if text:
        state.event_log.note_set(
            document_id=state.document_id,
            chunk_id=chunk_id,
            note=text,
            created_at=now,
        )
        state.chunk_notes[chunk_id] = text
    elif chunk_id in state.chunk_notes:
        state.event_log.note_clear(
            document_id=state.document_id,
            chunk_id=chunk_id,
            created_at=now,
        )
        del state.chunk_notes[chunk_id]
    export_status = _export_document_notes(request, state)
    response = _render_partial(request, state)
    if export_status == "failed":
        response.headers["X-Note-Export"] = "failed"
    return response


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
    # Free Mode (Parsem-ci5): bypass the bucket. Space advances current
    # by one to the document end without spending a token and without
    # moving high_water. No reveal event is logged — Free Mode is browse,
    # not reading; paced settling is the only thing that counts toward
    # progress.
    if state.free_mode:
        if state.current_position + 1 < len(state.chunks):
            state.current_position += 1
            reason = "advanced_free"
        else:
            reason = "end_of_document"
        response = _render_partial(request, state)
        response.headers["X-Reveal-Outcome"] = reason
        return response
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


@router.post("/free", response_class=HTMLResponse)
def post_free(request: Request) -> HTMLResponse:
    """Toggle Free Mode (Parsem-ci5). Free Mode reveals every chunk,
    suspends the bucket valve, and never advances ``high_water``. Press
    F again (client-side dispatch) to return to paced. On toggle-OFF,
    ``current_position`` clamps back to ``high_water_position`` so the
    user resumes the spine at the frontier rather than past it."""
    state = _state(request)
    state.free_mode = not state.free_mode
    if not state.free_mode and state.current_position > state.high_water_position:
        state.current_position = state.high_water_position
    return _render_partial(request, state)
