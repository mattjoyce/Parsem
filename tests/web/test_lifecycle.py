"""Tests for open_document/close_document event logging.

Spec §18.1, §21; bead Parsem-8wj.

GET /documents/{id}/reader logs an open_document event. POST
/documents/{id}/close logs a close_document event (fired by the
client's pagehide/beforeunload sendBeacon). Close is best-effort: a
stale beacon for a deleted doc returns 204 silently.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.store.events import EventLog
from parsem.web.state import ReaderState


def _events_of_type(state: ReaderState, event_type: str) -> list:
    return [
        e for e in state.event_log.events_for_document(state.document_id)
        if e.event_type == event_type
    ]


def test_get_reader_logs_open_document_event(
    client: TestClient, state: ReaderState
) -> None:
    response = client.get(f"/documents/{state.document_id}/reader")
    assert response.status_code == 200
    opens = _events_of_type(state, "open_document")
    assert len(opens) == 1
    assert opens[0].chunk_id is None
    assert opens[0].document_id == state.document_id


def test_open_event_repeats_on_each_reader_visit(
    client: TestClient, state: ReaderState
) -> None:
    """A page refresh or re-navigation logs a fresh open_document each
    time — the event log is the source of truth, not in-memory state."""
    client.get(f"/documents/{state.document_id}/reader")
    client.get(f"/documents/{state.document_id}/reader")
    opens = _events_of_type(state, "open_document")
    assert len(opens) == 2


def test_reader_response_carries_document_id_in_body_dataset(
    client: TestClient, state: ReaderState
) -> None:
    """The lifecycle JS reads document.body.dataset.documentId to know
    which doc to send the close beacon for."""
    body = client.get(f"/documents/{state.document_id}/reader").text
    assert f'data-document-id="{state.document_id}"' in body


def test_close_route_logs_close_document_event(
    client: TestClient, state: ReaderState
) -> None:
    response = client.post(f"/documents/{state.document_id}/close")
    assert response.status_code == 204
    closes = _events_of_type(state, "close_document")
    assert len(closes) == 1
    assert closes[0].chunk_id is None


def test_close_route_for_unknown_doc_returns_204_silently(
    client: TestClient, state: ReaderState
) -> None:
    """sendBeacon discards the response, so a stale close for a deleted
    doc must not surface as a 404 to the user. The route swallows it."""
    response = client.post("/documents/999/close")
    assert response.status_code == 204
    closes = _events_of_type(state, "close_document")
    assert len(closes) == 0


def test_close_route_for_known_doc_with_no_open_still_logs(
    client: TestClient, state: ReaderState
) -> None:
    """No 'open' precondition required — the bead notes that close is
    best-effort and the projection layer can reconcile."""
    response = client.post(f"/documents/{state.document_id}/close")
    assert response.status_code == 204
    closes = _events_of_type(state, "close_document")
    assert len(closes) == 1


def test_lifecycle_js_is_served(client: TestClient) -> None:
    response = client.get("/static/reader_lifecycle.js")
    assert response.status_code == 200
    assert "sendBeacon" in response.text
    assert "/close" in response.text


def test_open_event_logs_for_doc_being_opened_not_prior_state(
    client: TestClient, state: ReaderState
) -> None:
    """Defensive: even if app.state.reader holds a different doc on
    arrival, the open event must reference the URL's doc id."""
    # Force the in-memory state onto a different doc id; the welcome
    # doc fixture only has document_id=1, so a temp swap is enough.
    original_doc_id = state.document_id
    state.document_id = -42
    state.event_log = EventLog(state.event_log._conn)  # silence projection hooks
    client.get(f"/documents/{original_doc_id}/reader")
    new_state = client.app.state.reader
    opens = _events_of_type(new_state, "open_document")
    assert len(opens) == 1
    assert opens[0].document_id == original_doc_id
