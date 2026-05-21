"""Tests for the library-v2 row payload extension. ADR 0005, bd Parsem-7wu.1.

Covers:
- `compute_silhouette_buckets` pure function (down-sample logic).
- `derive_source_domain` for URL parsing.
- `list_library_rows` returns the full extended payload.
- `load_section_layout` returns `(heading_text, chunk_count)` pairs.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from parsem.domain.materialize import Chunk, Section
from parsem.store.db import connect, migrate
from parsem.store.documents import (
    SILHOUETTE_BUCKET_COUNT,
    compute_silhouette_buckets,
    derive_source_domain,
    insert_chunks_and_sections,
    insert_document,
    list_library_rows,
    load_section_layout,
)
from parsem.store.tags import add_tag
from tests.conftest import T0

# === compute_silhouette_buckets — pure logic ===========================


def test_silhouette_empty_doc_returns_25_unread() -> None:
    result = compute_silhouette_buckets(chunk_ratings=[], high_water_position=0)
    assert len(result) == SILHOUETTE_BUCKET_COUNT
    assert all(b.kind == "unread" for b in result)


def test_silhouette_exactly_25_chunks_unread_all_unread() -> None:
    result = compute_silhouette_buckets(
        chunk_ratings=[None] * 25, high_water_position=0
    )
    assert len(result) == 25
    assert all(b.kind == "unread" for b in result)
    assert all(b.mean_rating is None for b in result)


def test_silhouette_partial_progress_partitions_at_high_water() -> None:
    # 25 chunks; read 10. Expect buckets 0-9 read_unrated, 10-24 unread.
    result = compute_silhouette_buckets(
        chunk_ratings=[None] * 25, high_water_position=10
    )
    assert [b.kind for b in result[:10]] == ["read_unrated"] * 10
    assert [b.kind for b in result[10:]] == ["unread"] * 15


def test_silhouette_rated_buckets_take_mean_rating() -> None:
    # 25 chunks, all read, ratings = [1,2,3,4,5, ...repeated]
    ratings = [((i % 5) + 1) for i in range(25)]
    result = compute_silhouette_buckets(
        chunk_ratings=ratings, high_water_position=25
    )
    # 1:1 mapping at N=25, so each cell's mean is its own rating
    assert all(b.kind == "rated" for b in result)
    assert [b.mean_rating for b in result] == ratings


def test_silhouette_bucket_mean_rounds_to_palette() -> None:
    # 50 chunks → bucket aggregates 2 chunks. Ratings [3, 4] → mean
    # 3.5 → rounds banker-style; we want at least to land inside [1,5].
    # Pair patterns crafted so we can predict the rounded result.
    ratings = [3, 4] * 25  # bucket aggregates two adjacent chunks
    # bucket i covers chunks [2i, 2i+1] → values 3,4 → mean 3.5 → round-half-even = 4
    result = compute_silhouette_buckets(
        chunk_ratings=ratings, high_water_position=50
    )
    assert all(b.kind == "rated" for b in result)
    # round(3.5) in Python is 4 (banker's), round(4.5) is 4 too — but
    # the mean here is always 3.5, so rounded should be 4 every bucket.
    assert all(b.mean_rating == 4 for b in result)


def test_silhouette_small_doc_spreads_chunks_across_25_with_absent_gaps() -> None:
    # 10 chunks → bucket(j) = j*25//10 → positions 0,2,5,7,10,12,15,17,20,22
    ratings = [None] * 10
    result = compute_silhouette_buckets(
        chunk_ratings=ratings, high_water_position=10
    )
    populated = {0, 2, 5, 7, 10, 12, 15, 17, 20, 22}
    for i, bucket in enumerate(result):
        if i in populated:
            assert bucket.kind == "read_unrated", f"expected populated at {i}"
        else:
            assert bucket.kind == "absent", f"expected absent at {i}"


def test_silhouette_partial_settled_in_bucket_counts_as_read() -> None:
    # 50 chunks (2 per bucket). high_water = 1 → only chunk 0 settled.
    # Bucket 0 covers [0, 1] → has one settled chunk → read_unrated.
    # Bucket 1 covers [2, 3] → none settled → unread.
    ratings = [None] * 50
    result = compute_silhouette_buckets(
        chunk_ratings=ratings, high_water_position=1
    )
    assert result[0].kind == "read_unrated"
    assert result[1].kind == "unread"


def test_silhouette_unrated_chunks_in_rated_bucket_dont_affect_mean() -> None:
    # 50 chunks, every bucket covers 2 chunks. Half rated, half None.
    # ratings: even chunks rated 5, odd chunks unrated.
    ratings: list[int | None] = []
    for i in range(50):
        ratings.append(5 if i % 2 == 0 else None)
    result = compute_silhouette_buckets(
        chunk_ratings=ratings, high_water_position=50
    )
    # Each bucket has one rated (5) and one None → mean of [5] = 5
    assert all(b.kind == "rated" and b.mean_rating == 5 for b in result)


def test_silhouette_always_returns_25_buckets() -> None:
    for n in [0, 1, 5, 10, 24, 25, 26, 100, 500]:
        result = compute_silhouette_buckets(
            chunk_ratings=[None] * n, high_water_position=0
        )
        assert len(result) == SILHOUETTE_BUCKET_COUNT, f"N={n}"


# === derive_source_domain =============================================


def test_derive_source_domain_returns_none_for_file_source() -> None:
    assert derive_source_domain("markdown", "data/originals/1.md") is None
    assert derive_source_domain("pdf", "data/originals/1.pdf") is None


def test_derive_source_domain_pulls_host_from_url() -> None:
    assert derive_source_domain(
        "url", "https://stratechery.com/2026/some-essay/"
    ) == "stratechery.com"


def test_derive_source_domain_lowercases_host() -> None:
    assert derive_source_domain(
        "url", "https://Stratechery.COM/post"
    ) == "stratechery.com"


def test_derive_source_domain_handles_subdomain() -> None:
    assert derive_source_domain(
        "url", "https://blog.example.com/x"
    ) == "blog.example.com"


def test_derive_source_domain_returns_none_for_malformed_url() -> None:
    # urlparse is permissive; bare string with no scheme yields empty
    # hostname → None.
    assert derive_source_domain("url", "not-a-url") is None


# === list_library_rows — extended payload ==============================


def _chunk(position: int, lead: str = "paragraph", seconds: float = 1.0) -> Chunk:
    return Chunk(
        position=position,
        source_offset_start=position * 10,
        source_offset_end=position * 10 + 9,
        text=f"chunk {position}",
        lead_token_type=lead,
        lead_heading_level=2 if lead == "heading" else None,
        estimated_read_seconds=seconds,
    )


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


@pytest.fixture
def md_doc(db: sqlite3.Connection) -> int:
    doc_id = insert_document(
        db, title="welcome", original_path="data/originals/1.md",
        status="ready", total_chunks=5, source_type="markdown", now=T0,
    )
    insert_chunks_and_sections(
        db, document_id=doc_id,
        chunks=[_chunk(i, seconds=2.0) for i in range(5)],
        sections=[Section(
            heading_chunk_position=None, heading_level=None,
            start_chunk_position=0, end_chunk_position=4,
        )],
        now=T0,
    )
    return doc_id


def test_row_includes_ingest_date_and_source_domain_for_file(
    db: sqlite3.Connection, md_doc: int
) -> None:
    [row] = list_library_rows(db, segment="all")
    assert row.ingest_date == T0
    assert row.source_domain is None
    assert row.document.source_type == "markdown"


def test_row_for_url_doc_carries_domain(db: sqlite3.Connection) -> None:
    insert_document(
        db, title="post", original_path="https://stratechery.com/post",
        status="ready", total_chunks=3, source_type="url", now=T0,
    )
    [row] = list_library_rows(db, segment="all")
    assert row.source_domain == "stratechery.com"


def test_row_total_reading_seconds_sums_chunk_estimates(
    db: sqlite3.Connection, md_doc: int
) -> None:
    [row] = list_library_rows(db, segment="all")
    assert row.total_reading_seconds == pytest.approx(10.0)  # 5 x 2.0


def test_row_pin_count_starts_at_zero(
    db: sqlite3.Connection, md_doc: int
) -> None:
    [row] = list_library_rows(db, segment="all")
    assert row.pin_count == 0


def test_row_pin_count_reflects_pins(
    db: sqlite3.Connection, md_doc: int
) -> None:
    chunk_row = db.execute(
        "SELECT id FROM chunks WHERE document_id=? AND position=0",
        (md_doc,),
    ).fetchone()
    chunk_id = chunk_row["id"]
    db.execute(
        "INSERT INTO pins (document_id, chunk_id_start, word_start,"
        " chunk_id_end, word_end, color_id, created_at)"
        " VALUES (?, ?, 0, ?, -1, 1, ?)",
        (md_doc, chunk_id, chunk_id, T0.isoformat()),
    )
    db.commit()
    [row] = list_library_rows(db, segment="all")
    assert row.pin_count == 1


def test_row_tags_alphabetised(
    db: sqlite3.Connection, md_doc: int
) -> None:
    add_tag(db, md_doc, "wisdom", now=T0)
    add_tag(db, md_doc, "brick", now=T0)
    [row] = list_library_rows(db, segment="all")
    assert row.tags == ["brick", "wisdom"]


def test_row_last_opened_is_none_when_never_read(
    db: sqlite3.Connection, md_doc: int
) -> None:
    [row] = list_library_rows(db, segment="all")
    assert row.last_opened is None


def test_row_last_opened_reflects_reading_state(
    db: sqlite3.Connection, md_doc: int
) -> None:
    opened_at = T0 + timedelta(hours=2)
    db.execute(
        "INSERT INTO reading_state (document_id, high_water_position,"
        " current_position, updated_at) VALUES (?, 0, 0, ?)",
        (md_doc, opened_at.isoformat()),
    )
    db.commit()
    [row] = list_library_rows(db, segment="all")
    assert row.last_opened == opened_at


def test_row_silhouette_buckets_is_25_for_any_doc(
    db: sqlite3.Connection, md_doc: int
) -> None:
    [row] = list_library_rows(db, segment="all")
    assert len(row.silhouette_buckets) == SILHOUETTE_BUCKET_COUNT


def test_row_silhouette_partitions_at_high_water_position(
    db: sqlite3.Connection, md_doc: int
) -> None:
    # 5-chunk doc, high_water=3 → bucket(j)=j*25/5 → 0,5,10,15,20.
    # Buckets 0,5,10 are read_unrated (j=0,1,2 < 3). 15,20 unread.
    db.execute(
        "INSERT INTO reading_state (document_id, high_water_position,"
        " current_position, updated_at) VALUES (?, 3, 3, ?)",
        (md_doc, T0.isoformat()),
    )
    db.commit()
    [row] = list_library_rows(db, segment="all")
    assert row.silhouette_buckets[0].kind == "read_unrated"
    assert row.silhouette_buckets[5].kind == "read_unrated"
    assert row.silhouette_buckets[10].kind == "read_unrated"
    assert row.silhouette_buckets[15].kind == "unread"
    assert row.silhouette_buckets[20].kind == "unread"
    # Spans between populated buckets are 'absent' (N<25)
    assert row.silhouette_buckets[1].kind == "absent"


# === load_section_layout ==============================================


def test_section_layout_returns_heading_text_and_chunk_count(
    db: sqlite3.Connection,
) -> None:
    doc_id = insert_document(
        db, title="doc", original_path="d.md", status="ready",
        total_chunks=6, now=T0,
    )
    # Chunk 0 is a heading "## Intro", chunk 3 is a heading "## Body".
    chunks = [
        _chunk(0, lead="heading"),
        _chunk(1), _chunk(2),
        _chunk(3, lead="heading"),
        _chunk(4), _chunk(5),
    ]
    # Replace text for headings with markdown-shaped text so the title-
    # extraction logic can pull a clean label.
    chunks[0] = Chunk(
        position=0, source_offset_start=0, source_offset_end=8,
        text="## Intro", lead_token_type="heading", lead_heading_level=2,
        estimated_read_seconds=1.0,
    )
    chunks[3] = Chunk(
        position=3, source_offset_start=30, source_offset_end=37,
        text="## Body", lead_token_type="heading", lead_heading_level=2,
        estimated_read_seconds=1.0,
    )
    sections = [
        Section(heading_chunk_position=0, heading_level=2,
                start_chunk_position=0, end_chunk_position=2),
        Section(heading_chunk_position=3, heading_level=2,
                start_chunk_position=3, end_chunk_position=5),
    ]
    insert_chunks_and_sections(
        db, document_id=doc_id, chunks=chunks, sections=sections, now=T0,
    )

    layout = load_section_layout(db, doc_id)
    assert layout == [("Intro", 3), ("Body", 3)]


def test_section_layout_returns_empty_for_unparsed_doc(
    db: sqlite3.Connection,
) -> None:
    insert_document(
        db, title="raw", original_path="r.md", status="processing",
        total_chunks=None, now=T0,
    )
    [row] = list_library_rows(db, segment="all")
    assert row.section_layout == []
