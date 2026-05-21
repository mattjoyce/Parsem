"""Tests for parsem.store.tags. ADR 0005, bd Parsem-7wu.1."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from parsem.store.db import connect, migrate
from parsem.store.documents import insert_document
from parsem.store.tags import (
    add_tag,
    list_all_tags,
    list_tags_for_doc,
    load_tags_for_documents,
    normalise_tag,
    remove_tag,
)
from tests.conftest import T0


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


@pytest.fixture
def doc_id(db: sqlite3.Connection) -> int:
    return insert_document(
        db,
        title="welcome",
        original_path="data/originals/1.md",
        status="ready",
        total_chunks=10,
        now=T0,
    )


# === normalise_tag — input rules =====================================


def test_normalise_lowercases() -> None:
    assert normalise_tag("Wisdom") == "wisdom"


def test_normalise_strips_outer_whitespace() -> None:
    assert normalise_tag("  brick  ") == "brick"


def test_normalise_collapses_interior_whitespace_to_hyphens() -> None:
    assert normalise_tag("Brick Wisdom") == "brick-wisdom"
    assert normalise_tag("  Many   spaces   here ") == "many-spaces-here"


def test_normalise_rejects_empty_after_strip() -> None:
    with pytest.raises(ValueError, match="empty"):
        normalise_tag("   ")
    with pytest.raises(ValueError, match="empty"):
        normalise_tag("")


def test_normalise_rejects_overlong() -> None:
    with pytest.raises(ValueError, match="32 chars"):
        normalise_tag("a" * 33)


def test_normalise_accepts_at_limit() -> None:
    assert normalise_tag("a" * 32) == "a" * 32


# === add_tag ===========================================================


def test_add_tag_persists_and_returns_true(
    db: sqlite3.Connection, doc_id: int
) -> None:
    written = add_tag(db, doc_id, "Wisdom", now=T0)
    assert written is True
    assert list_tags_for_doc(db, doc_id) == ["wisdom"]


def test_add_tag_is_idempotent(db: sqlite3.Connection, doc_id: int) -> None:
    add_tag(db, doc_id, "wisdom", now=T0)
    written = add_tag(db, doc_id, "wisdom", now=T0 + timedelta(seconds=1))
    assert written is False
    assert list_tags_for_doc(db, doc_id) == ["wisdom"]


def test_add_tag_normalises_before_insert(
    db: sqlite3.Connection, doc_id: int
) -> None:
    add_tag(db, doc_id, "  Brick Wisdom  ", now=T0)
    assert list_tags_for_doc(db, doc_id) == ["brick-wisdom"]


def test_add_tag_rejects_invalid(db: sqlite3.Connection, doc_id: int) -> None:
    with pytest.raises(ValueError):
        add_tag(db, doc_id, "  ", now=T0)


# === remove_tag ========================================================


def test_remove_tag_deletes_and_returns_true(
    db: sqlite3.Connection, doc_id: int
) -> None:
    add_tag(db, doc_id, "wisdom", now=T0)
    removed = remove_tag(db, doc_id, "wisdom")
    assert removed is True
    assert list_tags_for_doc(db, doc_id) == []


def test_remove_tag_normalises_input(
    db: sqlite3.Connection, doc_id: int
) -> None:
    add_tag(db, doc_id, "brick-wisdom", now=T0)
    removed = remove_tag(db, doc_id, "  Brick Wisdom  ")
    assert removed is True


def test_remove_tag_returns_false_when_absent(
    db: sqlite3.Connection, doc_id: int
) -> None:
    assert remove_tag(db, doc_id, "wisdom") is False


# === list_tags_for_doc =================================================


def test_list_tags_for_doc_is_sorted(
    db: sqlite3.Connection, doc_id: int
) -> None:
    add_tag(db, doc_id, "wisdom", now=T0)
    add_tag(db, doc_id, "brick", now=T0)
    add_tag(db, doc_id, "stratechery", now=T0)
    assert list_tags_for_doc(db, doc_id) == ["brick", "stratechery", "wisdom"]


def test_list_tags_for_doc_returns_empty_for_untagged(
    db: sqlite3.Connection, doc_id: int
) -> None:
    assert list_tags_for_doc(db, doc_id) == []


# === list_all_tags =====================================================


def test_list_all_tags_returns_distinct_sorted(
    db: sqlite3.Connection,
) -> None:
    d1 = insert_document(
        db, title="a", original_path="a.md", status="ready",
        total_chunks=1, now=T0,
    )
    d2 = insert_document(
        db, title="b", original_path="b.md", status="ready",
        total_chunks=1, now=T0,
    )
    add_tag(db, d1, "wisdom", now=T0)
    add_tag(db, d2, "wisdom", now=T0)  # same tag, different doc
    add_tag(db, d1, "brick", now=T0)
    assert list_all_tags(db) == ["brick", "wisdom"]


# === load_tags_for_documents (bulk) ====================================


def test_bulk_load_returns_empty_lists_for_untagged_docs(
    db: sqlite3.Connection,
) -> None:
    d1 = insert_document(
        db, title="a", original_path="a.md", status="ready",
        total_chunks=1, now=T0,
    )
    d2 = insert_document(
        db, title="b", original_path="b.md", status="ready",
        total_chunks=1, now=T0,
    )
    add_tag(db, d1, "wisdom", now=T0)
    result = load_tags_for_documents(db, [d1, d2])
    assert result == {d1: ["wisdom"], d2: []}


def test_bulk_load_groups_tags_by_doc(db: sqlite3.Connection) -> None:
    d1 = insert_document(
        db, title="a", original_path="a.md", status="ready",
        total_chunks=1, now=T0,
    )
    d2 = insert_document(
        db, title="b", original_path="b.md", status="ready",
        total_chunks=1, now=T0,
    )
    add_tag(db, d1, "wisdom", now=T0)
    add_tag(db, d1, "brick", now=T0)
    add_tag(db, d2, "stratechery", now=T0)
    result = load_tags_for_documents(db, [d1, d2])
    assert result[d1] == ["brick", "wisdom"]
    assert result[d2] == ["stratechery"]


def test_bulk_load_with_empty_id_list_returns_empty_dict(
    db: sqlite3.Connection,
) -> None:
    assert load_tags_for_documents(db, []) == {}


# === cascade behaviour =================================================


def test_cascade_delete_removes_tags(db: sqlite3.Connection) -> None:
    d1 = insert_document(
        db, title="a", original_path="a.md", status="ready",
        total_chunks=1, now=T0,
    )
    add_tag(db, d1, "wisdom", now=T0)
    db.execute("DELETE FROM documents WHERE id = ?", (d1,))
    db.commit()
    assert list_tags_for_doc(db, d1) == []
