"""Tests for GET /reader. Spec: parsem-spec.md §22, beads Parsem-wym."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.web.state import ReaderState


def test_get_reader_returns_200_html(client: TestClient) -> None:
    response = client.get("/reader")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_get_reader_renders_first_chunk_text(client: TestClient, state: ReaderState) -> None:
    response = client.get("/reader")
    assert state.chunks[0].text.strip().split("\n")[0] in response.text


def test_get_reader_returns_full_html_document(client: TestClient) -> None:
    response = client.get("/reader")
    assert "<html" in response.text


def test_reader_renders_chunks_with_data_chunk_position(
    client: TestClient, state: ReaderState
) -> None:
    state.current_position = 4  # mid-doc so a window of K=5 is fully populated
    response = client.get("/reader")
    for pos in (0, 1, 2, 3, 4):
        assert f'data-chunk-position="{pos}"' in response.text


def test_reader_marks_current_chunk_distinctly_from_settled(
    client: TestClient, state: ReaderState
) -> None:
    state.current_position = 4
    response = client.get("/reader")
    assert "chunk--current" in response.text
    assert "chunk--settled" in response.text


def test_reader_renders_sticky_heading_for_current_section(
    client: TestClient, state: ReaderState
) -> None:
    # Position the reader inside a non-prologue section: jump to the last chunk
    # of welcome.md, which lives under the final H2 ("Tips for deep reading").
    state.current_position = len(state.chunks) - 1
    response = client.get("/reader")
    assert 'id="section-heading"' in response.text
    heading_html = response.text.split('id="section-heading"', 1)[1].split("</header>", 1)[0]
    assert "Tips for deep reading" in heading_html
    assert "##" not in heading_html


def test_reader_renders_three_region_layout(client: TestClient) -> None:
    response = client.get("/reader")
    assert 'class="gutter gutter--left"' in response.text
    assert 'class="gutter gutter--right"' in response.text


def test_reader_renders_pin_dot_data_color_for_pinned_chunks(
    client: TestClient, state: ReaderState
) -> None:
    state.pin_colors[0] = 3
    response = client.get("/reader")
    assert 'data-pin-color="3"' in response.text


def test_reader_omits_data_color_for_unpinned_chunks(
    client: TestClient, state: ReaderState
) -> None:
    response = client.get("/reader")
    assert "data-pin-color" not in response.text


def test_reader_renders_rating_prompt_with_five_buttons(client: TestClient) -> None:
    response = client.get("/reader")
    for r in (1, 2, 3, 4, 5):
        assert f'data-rating="{r}"' in response.text


def test_reader_omits_countdown_when_bucket_has_tokens(client: TestClient) -> None:
    response = client.get("/reader")
    assert "countdown" not in response.text
    assert "Next reveal in" not in response.text


def test_reader_renders_top_bar_with_document_title(client: TestClient) -> None:
    response = client.get("/reader")
    assert 'class="top-bar"' in response.text
    assert "Welcome to Parsem" in response.text


def test_reader_top_bar_shows_progress_fraction(client: TestClient, state: ReaderState) -> None:
    state.current_position = 4
    response = client.get("/reader")
    total = len(state.chunks)
    assert f"5 / {total}" in response.text or f"5/{total}" in response.text


def test_reader_full_bucket_renders_five_filled_dots(client: TestClient) -> None:
    response = client.get("/reader")
    assert response.text.count("dot--filled") == 5
    assert "dot--regen" not in response.text


def test_reader_partially_drained_bucket_renders_regen_dot(
    client: TestClient, state: ReaderState
) -> None:
    # Reveal once → 1 token spent, 4 filled, 1 regen, 0 empty among 5 total dots
    client.post("/reveal")
    response = client.get("/reader")
    assert response.text.count("dot--filled") == 4
    assert response.text.count("dot--regen") == 1


def test_reader_full_page_loads_static_js_and_css(client: TestClient) -> None:
    response = client.get("/reader")
    assert "/static/reader.js" in response.text
    assert "/static/reader.css" in response.text


def test_reader_partial_does_not_include_script_or_link_tags(client: TestClient) -> None:
    """Partial fragments must NOT include <script>/<link> — otherwise every
    swap re-fetches the JS and double-binds the keydown listener."""
    response = client.post("/reveal")
    assert "<script" not in response.text
    assert "/static/reader.js" not in response.text
    assert "<link" not in response.text
