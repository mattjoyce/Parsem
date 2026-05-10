"""Tests for GET /reader. Spec: parsem-spec.md §22, beads Parsem-wym."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.web.state import ReaderState


def test_get_reader_returns_200_html(client: TestClient) -> None:
    response = client.get("/documents/1/reader")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_get_reader_renders_first_chunk_text(client: TestClient, state: ReaderState) -> None:
    """Chunk content reaches the page. Strip the leading markdown
    heading markers since chunks are rendered to HTML (Parsem-kli)."""
    response = client.get("/documents/1/reader")
    first_line = state.chunks[0].text.strip().split("\n")[0].lstrip("#").strip()
    assert first_line in response.text


def test_get_reader_returns_full_html_document(client: TestClient) -> None:
    response = client.get("/documents/1/reader")
    assert "<html" in response.text


def test_reader_renders_chunks_with_data_chunk_position(
    client: TestClient, state: ReaderState
) -> None:
    """At current=7 the growing-document model (Parsem-kli) shows ALL
    revealed chunks 0..7 in the DOM — not just the prior K-window
    (the Parsem-apa section-clamp is gone)."""
    state.current_position = 7
    state.high_water_position = 7
    response = client.get("/documents/1/reader")
    for pos in range(0, 8):
        assert f'data-chunk-position="{pos}"' in response.text


def test_reader_marks_current_chunk_distinctly_from_settled(
    client: TestClient, state: ReaderState
) -> None:
    """Any non-zero position inside a section gives at least one
    settled chunk plus the current one."""
    state.current_position = 7
    state.high_water_position = 7
    response = client.get("/documents/1/reader")
    assert "chunk--current" in response.text
    assert "chunk--settled" in response.text


def test_reader_renders_sticky_heading_for_current_section(
    client: TestClient, state: ReaderState
) -> None:
    # Position the reader inside a non-prologue section: jump to the last chunk
    # of welcome.md, which lives under the final H2 ("Tips for deep reading").
    state.current_position = len(state.chunks) - 1
    state.high_water_position = len(state.chunks) - 1
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
    state.high_water_position = 4
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
    next_text = state.chunks[1].text.strip().split("\n")[0].lstrip("#").strip()
    assert next_text in response.text


def test_preview_block_has_data_chunk_position_for_next(
    client: TestClient, state: ReaderState
) -> None:
    state.current_position = 3
    state.high_water_position = 3
    response = client.get("/documents/1/reader")
    assert 'class="preview"' in response.text
    assert 'data-chunk-position="4"' in response.text


def test_reader_omits_preview_at_end_of_document(client: TestClient, state: ReaderState) -> None:
    state.current_position = len(state.chunks) - 1
    state.high_water_position = len(state.chunks) - 1
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


def test_reader_after_click_back_keeps_all_paid_chunks_visible(
    client: TestClient, state: ReaderState
) -> None:
    """Click-back (claude-axx.3) drops current_position behind
    high_water_position. The growing-document model (Parsem-kli, §15)
    keeps every paid chunk in the DOM regardless of where the cursor
    sits — the reading trail must not shorten when reviewing."""
    state.high_water_position = 7
    state.current_position = 3  # reader clicked back from 7 to 3
    response = client.get("/documents/1/reader")
    for pos in range(0, 8):
        assert f'data-chunk-position="{pos}"' in response.text


def test_reader_after_click_back_renders_rating_prompt_below_current(
    client: TestClient, state: ReaderState
) -> None:
    """Spec §9.5: rating prompt sits 'below the current chunk and above
    the preview gutter.' When current_position < high_water_position
    (click-back state), the rating prompt must follow the current
    chunk in the DOM — not the last visible chunk."""
    state.high_water_position = 7
    state.current_position = 3
    response = client.get("/documents/1/reader")
    rating_idx = response.text.find('class="rating-prompt"')
    chunk_3_idx = response.text.find('data-chunk-position="3"')
    chunk_4_idx = response.text.find('data-chunk-position="4"')
    assert rating_idx > chunk_3_idx
    assert rating_idx < chunk_4_idx


def test_reader_after_click_back_preview_targets_post_high_water(
    client: TestClient, state: ReaderState
) -> None:
    """Preview shows the chunk past the FRONTIER, not past the cursor.
    With current=3 and high_water=7, Space resumes to 7 then advances
    to 8 — so the preview must show chunk 8."""
    state.high_water_position = 7
    state.current_position = 3
    response = client.get("/documents/1/reader")
    assert 'class="preview"' in response.text
    assert 'data-chunk-position="8"' in response.text


def test_reader_renders_reveal_symbol_at_current_chunk(
    client: TestClient, state: ReaderState
) -> None:
    """Inline reveal glyph (§8a.4, claude-axx.8, claude-jvs) — pointer-
    mode peer of Space. Renders inside the current chunk when there's
    a chunk past the frontier to advance into. Filled diamond ♦
    (U+2666) chosen over » so the affordance reads as a chunky symbol
    rather than trailing punctuation."""
    state.high_water_position = 2
    state.current_position = 2
    response = client.get("/documents/1/reader")
    assert "reveal-symbol" in response.text
    assert "&#9830;" in response.text or "♦" in response.text


def test_reader_omits_reveal_symbol_at_end_of_document(
    client: TestClient, state: ReaderState
) -> None:
    """Last chunk has no next_chunk — reveal symbol should hide
    entirely so the reader knows reading is over."""
    state.high_water_position = len(state.chunks) - 1
    state.current_position = len(state.chunks) - 1
    response = client.get("/documents/1/reader")
    assert "reveal-symbol" not in response.text


def test_reader_reveal_symbol_empty_class_when_bucket_drained(
    client: TestClient, state: ReaderState
) -> None:
    """Empty-bucket: symbol renders with --empty modifier so CSS
    ghosts it and JS swaps the click handler to play the rejection
    motion instead of POSTing /reveal."""
    from tests.web.conftest import exhaust_bucket

    exhaust_bucket(client, state)
    response = client.get("/documents/1/reader")
    assert "reveal-symbol--empty" in response.text


def test_reader_renders_rating_bar_for_rated_chunk(
    client: TestClient, state: ReaderState
) -> None:
    """Per-chunk rating bar (spec §14.3, claude-yda) — a rated chunk
    renders a <div class="rating-bar rating-bar--N"> at its bottom
    edge, tinted by the latest rating."""
    state.high_water_position = 4
    state.current_position = 4
    state.chunk_ratings[2] = 4
    response = client.get("/documents/1/reader")
    assert "rating-bar--4" in response.text


def test_reader_omits_rating_bar_for_unrated_chunks(
    client: TestClient, state: ReaderState
) -> None:
    """Chunks with no rating render no rating-bar element — the
    template guards on chunk_ratings membership so unrated chunks
    don't carry an empty bar."""
    client.get("/documents/1/reader")  # ensure doc opened
    state.high_water_position = 4
    state.current_position = 4
    state.chunk_ratings.clear()
    response = client.get("/documents/1/reader")
    assert "rating-bar--" not in response.text


def test_reader_main_carries_high_water_data_attr(
    client: TestClient, state: ReaderState
) -> None:
    """JS reads current and high_water from #reader-main data attrs to
    decide chunk-click and space-resume behaviour (§8a, claude-axx.3).
    Both must render on every server response."""
    state.high_water_position = 5
    state.current_position = 2
    response = client.get("/documents/1/reader")
    assert 'data-current-position="2"' in response.text
    assert 'data-high-water-position="5"' in response.text


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


# Rating glyph + horizontal pip popout (claude-jvs.4)
# ─────────────────────────────────────────────────────────────


def test_reader_renders_rating_glyph_on_current_chunk(
    client: TestClient, state: ReaderState
) -> None:
    """claude-jvs.4 — current chunk's rating-prompt now resolves to a
    single resting triangle glyph; the 5 pips are wrapped in a
    .rating-pips container revealed on hover via CSS."""
    state.high_water_position = 2
    state.current_position = 2
    response = client.get("/documents/1/reader")
    assert 'class="rating-glyph"' in response.text
    assert 'class="rating-pips"' in response.text
    # Regression: 5 rating-dot buttons still rendered (keyboard +
    # click toggle still depend on them).
    assert response.text.count("rating-dot") >= 5


def test_reader_rating_prompt_carries_current_rating_data_attr(
    client: TestClient, state: ReaderState
) -> None:
    """data-current-rating on .rating-prompt drives CSS-side glyph
    tinting (rating-bar palette: blue/grey/amber/red)."""
    state.high_water_position = 2
    state.current_position = 2
    state.chunk_ratings[2] = 4
    response = client.get("/documents/1/reader")
    assert 'data-current-rating="4"' in response.text


def test_reader_rating_glyph_only_on_current_chunk(
    client: TestClient, state: ReaderState
) -> None:
    """rating-prompt + glyph stay current-chunk-only; settled chunks
    render no rating affordance (only chunk-actions / link)."""
    state.high_water_position = 3
    state.current_position = 3
    response = client.get("/documents/1/reader")
    assert response.text.count('class="rating-glyph"') == 1
    assert response.text.count('class="rating-prompt"') == 1


# Deep-link query param + per-chunk action glyphs (claude-jvs.2)
# ─────────────────────────────────────────────────────────────


def test_reader_chunk_query_param_sets_current_position(
    client: TestClient, state: ReaderState
) -> None:
    """?chunk=N sets current_position to N when N is in [0, high_water]."""
    state.high_water_position = 5
    state.current_position = 0
    client.get("/documents/1/reader?chunk=3")
    assert state.current_position == 3


def test_reader_chunk_query_param_clamps_above_high_water(
    client: TestClient, state: ReaderState
) -> None:
    """?chunk=N with N > high_water silently clamps to high_water — a
    shared deep link must never advance past the receiver's frontier
    (§8a.1) or 404 the receiver."""
    state.high_water_position = 4
    state.current_position = 0
    client.get("/documents/1/reader?chunk=999")
    assert state.current_position == 4


def test_reader_chunk_query_param_clamps_negative_to_zero(
    client: TestClient, state: ReaderState
) -> None:
    """?chunk=-3 clamps to 0; defensive guard against malformed URLs."""
    state.high_water_position = 4
    state.current_position = 2
    client.get("/documents/1/reader?chunk=-3")
    assert state.current_position == 0


def test_reader_without_chunk_param_preserves_current_position(
    client: TestClient, state: ReaderState
) -> None:
    """No regression: bare GET (no ?chunk) does not move current."""
    state.high_water_position = 5
    state.current_position = 3
    client.get("/documents/1/reader")
    assert state.current_position == 3


def test_reader_renders_chunk_actions_for_every_visible_chunk(
    client: TestClient, state: ReaderState
) -> None:
    """Per-chunk action glyph (claude-jvs.3) — every visible chunk
    (settled + current) carries a chunk-actions nav with a single
    copy-link button. Native browser select-and-copy handles chunk
    content; the prior copy-text glyph was removed (UAT)."""
    state.high_water_position = 3
    state.current_position = 3
    response = client.get("/documents/1/reader")
    # 4 chunks are visible (positions 0..3); each gets one action stack.
    assert response.text.count('class="chunk-actions"') == 4
    assert response.text.count('data-action="copy-link"') == 4
    assert 'data-action="copy-text"' not in response.text
    assert "data-chunk-text" not in response.text


def test_reader_preview_chunk_has_no_action_stack(
    client: TestClient, state: ReaderState
) -> None:
    """Preview is preparation, not interactive (§9.5) — action glyph
    must not appear on the preview chunk."""
    state.high_water_position = 0
    state.current_position = 0
    response = client.get("/documents/1/reader")
    # Exactly 1 visible chunk (position 0). One action stack expected.
    assert response.text.count('class="chunk-actions"') == 1
