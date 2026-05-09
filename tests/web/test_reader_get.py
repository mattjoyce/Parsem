"""Tests for GET /reader. Spec: parsem-spec.md §22, beads Parsem-wym."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.web.state import ReaderState


def test_get_reader_returns_200_html(client: TestClient) -> None:
    response = client.get("/documents/1/reader")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_get_reader_renders_first_chunk_text(client: TestClient, state: ReaderState) -> None:
    response = client.get("/documents/1/reader")
    assert state.chunks[0].text.strip().split("\n")[0] in response.text


def test_get_reader_returns_full_html_document(client: TestClient) -> None:
    response = client.get("/documents/1/reader")
    assert "<html" in response.text


def test_reader_renders_chunks_with_data_chunk_position(
    client: TestClient, state: ReaderState
) -> None:
    """Section "The token bucket" spans chunks 4..7; current=7 fills
    the section-clamped window (Parsem-apa) with [4, 5, 6, 7]."""
    state.current_position = 7
    response = client.get("/documents/1/reader")
    for pos in (4, 5, 6, 7):
        assert f'data-chunk-position="{pos}"' in response.text


def test_reader_marks_current_chunk_distinctly_from_settled(
    client: TestClient, state: ReaderState
) -> None:
    """Any non-zero position inside a section gives at least one
    settled chunk plus the current one."""
    state.current_position = 7
    response = client.get("/documents/1/reader")
    assert "chunk--current" in response.text
    assert "chunk--settled" in response.text


def test_reader_renders_sticky_heading_for_current_section(
    client: TestClient, state: ReaderState
) -> None:
    # Position the reader inside a non-prologue section: jump to the last chunk
    # of welcome.md, which lives under the final H2 ("Tips for deep reading").
    state.current_position = len(state.chunks) - 1
    response = client.get("/documents/1/reader")
    assert 'id="section-heading"' in response.text
    heading_html = response.text.split('id="section-heading"', 1)[1].split("</header>", 1)[0]
    assert "Tips for deep reading" in heading_html
    assert "##" not in heading_html


def test_reader_renders_three_region_layout(client: TestClient) -> None:
    response = client.get("/documents/1/reader")
    assert 'class="gutter gutter--left"' in response.text
    assert 'class="gutter gutter--right"' in response.text


def test_reader_renders_pin_dot_data_color_for_pinned_chunks(
    client: TestClient, state: ReaderState
) -> None:
    state.pin_colors[0] = 3
    response = client.get("/documents/1/reader")
    assert 'data-pin-color="3"' in response.text


def test_reader_omits_data_color_for_unpinned_chunks(
    client: TestClient, state: ReaderState
) -> None:
    response = client.get("/documents/1/reader")
    assert "data-pin-color" not in response.text


def test_reader_renders_rating_prompt_with_five_buttons(client: TestClient) -> None:
    response = client.get("/documents/1/reader")
    for r in (1, 2, 3, 4, 5):
        assert f'data-rating="{r}"' in response.text


def test_reader_never_renders_countdown_ui(client: TestClient, state: ReaderState) -> None:
    """Empty-bucket UX is now a motion effect (Parsem-0if), not a text
    banner. Anchor on the markup classes, not substrings — welcome.md
    content quotes the old banner text inside an example."""
    response = client.get("/documents/1/reader")
    assert 'class="countdown"' not in response.text
    assert 'class="countdown-reminders"' not in response.text


def test_reveal_response_never_renders_countdown_ui(client: TestClient, state: ReaderState) -> None:
    from tests.web.conftest import exhaust_bucket

    exhaust_bucket(client, state)
    response = client.post("/reveal")
    assert 'class="countdown"' not in response.text
    assert 'class="countdown-reminders"' not in response.text


def test_reader_renders_top_bar_with_document_title(client: TestClient) -> None:
    response = client.get("/documents/1/reader")
    assert 'class="top-bar"' in response.text
    assert "Welcome to Parsem" in response.text


def test_reader_top_bar_shows_progress_fraction(client: TestClient, state: ReaderState) -> None:
    state.current_position = 4
    response = client.get("/documents/1/reader")
    total = len(state.chunks)
    assert f"5 / {total}" in response.text or f"5/{total}" in response.text


def test_reader_full_bucket_renders_five_filled_dots(client: TestClient) -> None:
    response = client.get("/documents/1/reader")
    assert response.text.count("dot--filled") == 5
    assert "dot--regen" not in response.text


def test_reader_partially_drained_bucket_renders_regen_dot(
    client: TestClient, state: ReaderState
) -> None:
    # Reveal once → 1 token spent, 4 filled, 1 regen, 0 empty among 5 total dots
    client.post("/reveal")
    response = client.get("/documents/1/reader")
    assert response.text.count("dot--filled") == 4
    assert response.text.count("dot--regen") == 1


def test_reader_renders_blurred_preview_of_next_chunk(
    client: TestClient, state: ReaderState
) -> None:
    response = client.get("/documents/1/reader")
    assert 'class="preview"' in response.text
    next_text = state.chunks[1].text.strip().split("\n")[0]
    assert next_text in response.text


def test_preview_block_has_data_chunk_position_for_next(
    client: TestClient, state: ReaderState
) -> None:
    state.current_position = 3
    response = client.get("/documents/1/reader")
    assert 'class="preview"' in response.text
    assert 'data-chunk-position="4"' in response.text


def test_reader_omits_preview_at_end_of_document(client: TestClient, state: ReaderState) -> None:
    state.current_position = len(state.chunks) - 1
    response = client.get("/documents/1/reader")
    assert 'class="preview"' not in response.text


def test_preview_appears_after_current_chunk_and_rating_prompt(
    client: TestClient,
) -> None:
    response = client.get("/documents/1/reader")
    rating_idx = response.text.find('class="rating-prompt"')
    preview_idx = response.text.find('class="preview"')
    assert rating_idx >= 0 and preview_idx >= 0
    assert preview_idx > rating_idx


def test_reader_full_page_loads_static_js_and_css(client: TestClient) -> None:
    response = client.get("/documents/1/reader")
    assert "/static/reader.js" in response.text
    assert "/static/reader.css" in response.text


def test_reader_partial_does_not_include_script_or_link_tags(client: TestClient) -> None:
    """Partial fragments must NOT include <script>/<link> — otherwise every
    swap re-fetches the JS and double-binds the keydown listener."""
    response = client.post("/reveal")
    assert "<script" not in response.text
    assert "/static/reader.js" not in response.text
    assert "<link" not in response.text
