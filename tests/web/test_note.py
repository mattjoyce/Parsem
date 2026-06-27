"""Tests for POST /note + notes-export (notes-export)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.notes_export import note_file_name
from parsem.web.state import ReaderState
from parsem.web.view import document_title


def _notes(state: ReaderState) -> list[object]:
    return [
        e
        for e in state.event_log.events_for_document(state.document_id)
        if e.event_type in ("note_set", "note_clear")
    ]


def test_note_logs_note_set_event(client: TestClient, state: ReaderState) -> None:
    response = client.post("/note", json={"text": "a margin thought"})
    assert response.status_code == 200
    events = [e for e in _notes(state) if e.event_type == "note_set"]
    assert len(events) == 1
    assert events[0].chunk_id == state.current_position
    assert events[0].payload == {"note": "a margin thought"}


def test_note_updates_state_chunk_notes(client: TestClient, state: ReaderState) -> None:
    client.post("/note", json={"text": "hello"})
    assert state.chunk_notes[state.current_position] == "hello"


def test_note_trims_whitespace(client: TestClient, state: ReaderState) -> None:
    client.post("/note", json={"text": "  padded  "})
    assert state.chunk_notes[state.current_position] == "padded"


def test_empty_text_clears_existing_note(client: TestClient, state: ReaderState) -> None:
    client.post("/note", json={"text": "temp"})
    response = client.post("/note", json={"text": "   "})
    assert response.status_code == 200
    assert state.current_position not in state.chunk_notes
    assert [e for e in _notes(state) if e.event_type == "note_clear"]


def test_empty_text_on_unnoted_chunk_is_silent_noop(
    client: TestClient, state: ReaderState
) -> None:
    response = client.post("/note", json={"text": ""})
    assert response.status_code == 200
    assert _notes(state) == []


def test_note_does_not_advance_position(client: TestClient, state: ReaderState) -> None:
    pos_before = state.current_position
    client.post("/note", json={"text": "x"})
    assert state.current_position == pos_before


def test_note_returns_partial_fragment(client: TestClient) -> None:
    response = client.post("/note", json={"text": "x"})
    assert response.text.lstrip().startswith("<main")


def test_note_past_frontier_is_rejected(client: TestClient, state: ReaderState) -> None:
    """Free-mode chunks past high_water are view-only — pins/ratings/notes
    all 422 there (mirrors _reject_past_frontier)."""
    state.current_position = state.high_water_position + 1
    response = client.post("/note", json={"text": "nope"})
    assert response.status_code == 422


def test_note_writes_export_file_with_prose_and_backlink(
    client: TestClient, state: ReaderState
) -> None:
    pos = state.current_position
    client.post("/note", json={"text": "my exported note"})
    notes_dir = client.app.state.notes_dir
    path = notes_dir / note_file_name(state.document_id, document_title(state.chunks))
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "my exported note" in content
    assert f"?chunk={pos})" in content  # deep link back into the reader
    # the chunk's prose is blockquoted into the file
    assert "> " in content


def test_clearing_last_note_removes_export_file(
    client: TestClient, state: ReaderState
) -> None:
    client.post("/note", json={"text": "temp"})
    notes_dir = client.app.state.notes_dir
    path = notes_dir / note_file_name(state.document_id, document_title(state.chunks))
    assert path.exists()
    client.post("/note", json={"text": ""})
    assert not path.exists()


def test_top_bar_notes_link_hidden_until_a_note_exists(client: TestClient) -> None:
    before = client.get("/documents/1/reader").text
    assert "top-bar__notes--empty" in before  # reserved slot, hidden
    after = client.post("/note", json={"text": "now there's a note"}).text
    assert "top-bar__notes--empty" not in after
    assert 'class="top-bar__notes"' in after


def test_saved_note_renders_beneath_its_chunk(client: TestClient) -> None:
    response = client.post("/note", json={"text": "visible margin note"})
    assert "chunk-note" in response.text
    assert "visible margin note" in response.text


def test_note_survives_round_trip_through_db(client: TestClient, state: ReaderState) -> None:
    """The note projection persists: a freshly-built ReaderState for the
    same document sees the note (seeded via get_notes_for_document)."""
    from parsem.cli import RESUME_WARM_CHUNKS_DEFAULT
    from parsem.web.state import build_reader_state_for_document

    pos = state.current_position
    client.post("/note", json={"text": "durable"})
    reloaded = build_reader_state_for_document(
        client.app.state.db, document_id=1, warm_chunks=RESUME_WARM_CHUNKS_DEFAULT
    )
    assert reloaded is not None
    assert reloaded.chunk_notes[pos] == "durable"
