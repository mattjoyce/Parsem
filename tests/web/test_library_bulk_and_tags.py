"""Library v2.1 — multiselect bulk actions, tag-filter chips, and the
version footer. ADR 0005, bd Parsem-7wu.5 + semver surfacing.

Covers:
- The version footer renders `parsem.__version__` on /library.
- The tag-chip row lists every distinct tag and marks active ones.
- `list_library_rows(tags=...)` filters with OR semantics; None → no filter.
- GET /library?tag= narrows the grid and reflects the active chip.
- POST /documents/batch fans delete / rechunk / tag / untag over an id set.
- Batch validation: empty selection and missing tag both 422.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem import __version__
from parsem.domain.materialize import Chunk, Section
from parsem.ingest import layout
from parsem.store.db import connect, migrate
from parsem.store.documents import (
    insert_chunks_and_sections,
    insert_document,
    list_library_rows,
    load_document,
)
from parsem.store.tags import add_tag, list_tags_for_doc
from parsem.web.app import create_app
from parsem.web.state import empty_reader_state

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def app_ctx(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, sqlite3.Connection, Path]]:
    conn = connect(":memory:")
    migrate(conn)
    originals = tmp_path / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    app = create_app(empty_reader_state(conn), db=conn, originals_dir=originals)
    with TestClient(app) as client:
        yield client, conn, originals


def _chunk(position: int) -> Chunk:
    return Chunk(
        position=position,
        source_offset_start=position * 10,
        source_offset_end=position * 10 + 9,
        text=f"chunk {position}",
        lead_token_type="paragraph",
        lead_heading_level=None,
        estimated_read_seconds=1.0,
    )


def _seed(
    conn: sqlite3.Connection,
    originals: Path,
    *,
    title: str,
    tags: tuple[str, ...] = (),
    status: str = "ready",
) -> int:
    """Insert a document (with chunks + an on-disk .md so re-chunk has
    something to read) and optionally tag it. Failed docs skip chunks."""
    doc_id = insert_document(
        conn,
        title=title,
        original_path="placeholder",
        status=status,
        total_chunks=2 if status == "ready" else None,
        failure_reason=None if status == "ready" else "seed failure",
        now=T0,
    )
    if status == "ready":
        insert_chunks_and_sections(
            conn,
            document_id=doc_id,
            chunks=[_chunk(0), _chunk(1)],
            sections=[
                Section(
                    heading_chunk_position=None,
                    heading_level=None,
                    start_chunk_position=0,
                    end_chunk_position=1,
                )
            ],
            now=T0,
        )
    file_path = layout.markdown_path(originals, doc_id)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        f"# {title}\n\nBody paragraph one.\n\nBody paragraph two.\n",
        encoding="utf-8",
    )
    conn.execute(
        "UPDATE documents SET original_path=? WHERE id=?",
        (str(file_path), doc_id),
    )
    conn.commit()
    for tag in tags:
        add_tag(conn, doc_id, tag, now=T0)
    return doc_id


# === D1: version footer ================================================


def test_library_footer_shows_semver(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _conn, _originals = app_ctx
    r = client.get("/library?segment=all")
    assert r.status_code == 200
    assert f"Parsem v{__version__}" in r.text


# === D3: tag-chip row + filtering ======================================


def test_tag_chip_row_lists_distinct_tags(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    _seed(conn, originals, title="Alpha", tags=("ai",))
    _seed(conn, originals, title="Beta", tags=("security",))
    r = client.get("/library?segment=all")
    assert "#ai" in r.text
    assert "#security" in r.text
    # Each chip links to a URL that toggles its tag on.
    assert "tag=ai" in r.text


def test_tag_filter_narrows_grid_and_marks_active(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    _seed(conn, originals, title="HasAI", tags=("ai",))
    _seed(conn, originals, title="HasSec", tags=("security",))
    _seed(conn, originals, title="Untagged")

    r = client.get("/library?segment=all&tag=ai")
    assert "HasAI" in r.text
    assert "HasSec" not in r.text
    assert "Untagged" not in r.text
    # The active chip advertises its state for a11y + styling.
    assert 'aria-pressed="true"' in r.text
    # A Clear affordance appears when a filter is active.
    assert "Clear" in r.text


def test_list_library_rows_tag_filter_or_semantics(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    _client, conn, originals = app_ctx
    a = _seed(conn, originals, title="A", tags=("ai",))
    b = _seed(conn, originals, title="B", tags=("security",))
    c = _seed(conn, originals, title="C")

    only_ai = {row.document.id for row in list_library_rows(conn, segment="all", tags=["ai"])}
    assert only_ai == {a}

    either = {
        row.document.id for row in list_library_rows(conn, segment="all", tags=["ai", "security"])
    }
    assert either == {a, b}

    unfiltered = {row.document.id for row in list_library_rows(conn, segment="all")}
    assert unfiltered == {a, b, c}


def test_unknown_tag_param_is_ignored(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    _seed(conn, originals, title="Alpha", tags=("ai",))
    # A tag that exists on no document degrades to "no filter", not 422.
    r = client.get("/library?segment=all&tag=does-not-exist")
    assert r.status_code == 200
    assert "Alpha" in r.text


# === D2: bulk action bar presence + endpoint ===========================


def test_select_toggle_and_batchbar_render(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    _seed(conn, originals, title="Alpha")
    r = client.get("/library?segment=all")
    assert "library-select-toggle" in r.text
    assert "library-batchbar" in r.text
    assert 'data-batch-action="delete"' in r.text
    assert 'data-batch-action="tag"' in r.text
    # Each tile carries a selection checkbox for the batch bar to read.
    assert "library-tile__checkbox" in r.text


def test_batch_tag_applies_normalised_tag_to_all(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    a = _seed(conn, originals, title="A")
    b = _seed(conn, originals, title="B")
    r = client.post(
        "/documents/batch",
        json={"action": "tag", "document_ids": [a, b], "tag": "Brick Wisdom"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "action": "tag", "affected": 2}
    # Normalisation: "Brick Wisdom" → "brick-wisdom".
    assert list_tags_for_doc(conn, a) == ["brick-wisdom"]
    assert list_tags_for_doc(conn, b) == ["brick-wisdom"]


def test_batch_tag_is_idempotent(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    a = _seed(conn, originals, title="A", tags=("ai",))
    r = client.post(
        "/documents/batch",
        json={"action": "tag", "document_ids": [a], "tag": "ai"},
    )
    # Already tagged → no new row → affected 0.
    assert r.json()["affected"] == 0


def test_batch_untag_removes_tag(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    a = _seed(conn, originals, title="A", tags=("ai", "security"))
    r = client.post(
        "/documents/batch",
        json={"action": "untag", "document_ids": [a], "tag": "ai"},
    )
    assert r.json()["affected"] == 1
    assert list_tags_for_doc(conn, a) == ["security"]


def test_batch_delete_removes_selected_only(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    a = _seed(conn, originals, title="Gone")
    b = _seed(conn, originals, title="Kept")
    r = client.post(
        "/documents/batch",
        json={"action": "delete", "document_ids": [a]},
    )
    assert r.json() == {"ok": True, "action": "delete", "affected": 1}
    assert load_document(conn, a) is None
    assert load_document(conn, b) is not None


def test_batch_rechunk_only_touches_ready_docs(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    ready = _seed(conn, originals, title="Ready", status="ready")
    failed = _seed(conn, originals, title="Failed", status="failed")
    r = client.post(
        "/documents/batch",
        json={"action": "rechunk", "document_ids": [ready, failed]},
    )
    # Only the ready doc is re-chunked; the failed one is skipped.
    assert r.json()["affected"] == 1
    assert load_document(conn, ready).status == "ready"  # type: ignore[union-attr]


def test_batch_unknown_ids_are_skipped_not_404(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    a = _seed(conn, originals, title="A")
    r = client.post(
        "/documents/batch",
        json={"action": "delete", "document_ids": [a, 99999]},
    )
    assert r.status_code == 200
    assert r.json()["affected"] == 1


def test_batch_empty_selection_is_422(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _conn, _originals = app_ctx
    r = client.post(
        "/documents/batch",
        json={"action": "delete", "document_ids": []},
    )
    assert r.status_code == 422


def test_batch_tag_without_tag_is_422(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    a = _seed(conn, originals, title="A")
    r = client.post(
        "/documents/batch",
        json={"action": "tag", "document_ids": [a], "tag": "   "},
    )
    assert r.status_code == 422


def test_batch_rejects_unknown_action(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    a = _seed(conn, originals, title="A")
    r = client.post(
        "/documents/batch",
        json={"action": "explode", "document_ids": [a]},
    )
    # Literal validation rejects the action at the schema layer.
    assert r.status_code == 422
