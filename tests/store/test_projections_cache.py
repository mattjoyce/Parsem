"""Tests for parsem.store.projections_cache. Spec §18.1, §18.5, §21; bead Parsem-3jd."""

from __future__ import annotations

import ast
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from parsem.domain.projections import build_reading_state
from parsem.store.db import connect, migrate
from parsem.store.documents import insert_document
from parsem.store.events import EventLog
from parsem.store.projections_cache import (
    load_reading_state,
    make_event_log,
    rebuild_reading_state,
)
from tests.conftest import T0


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    insert_document(conn, title="d1", original_path="d1.md", status="ready", now=T0)
    insert_document(conn, title="d2", original_path="d2.md", status="ready", now=T0)
    return conn


@pytest.fixture
def hooked_log(db: sqlite3.Connection) -> EventLog:
    """EventLog wired with the production projection composer —
    `make_event_log` fans out to every projection and commits once."""
    return make_event_log(db)


def test_load_returns_none_for_unseen_document(db: sqlite3.Connection) -> None:
    assert load_reading_state(db, document_id=1) is None


def test_apply_upserts_row_on_first_event(db: sqlite3.Connection, hooked_log: EventLog) -> None:
    hooked_log.reveal(document_id=1, chunk_id=3, created_at=T0)
    state = load_reading_state(db, document_id=1)
    assert state is not None
    assert state.high_water_position == 3
    assert state.current_position == 3
    assert state.last_event_id_applied == 1


def test_apply_updates_row_on_subsequent_events(
    db: sqlite3.Connection, hooked_log: EventLog
) -> None:
    hooked_log.reveal(document_id=1, chunk_id=0, created_at=T0)
    hooked_log.reveal(document_id=1, chunk_id=1, created_at=T0 + timedelta(seconds=1))
    hooked_log.reveal(document_id=1, chunk_id=2, created_at=T0 + timedelta(seconds=2))
    state = load_reading_state(db, document_id=1)
    assert state is not None
    assert state.high_water_position == 2
    assert state.current_position == 2


def test_concurrent_documents_track_independently(
    db: sqlite3.Connection, hooked_log: EventLog
) -> None:
    hooked_log.reveal(document_id=1, chunk_id=4, created_at=T0)
    hooked_log.reveal(document_id=2, chunk_id=1, created_at=T0)
    s1 = load_reading_state(db, document_id=1)
    s2 = load_reading_state(db, document_id=2)
    assert s1 is not None
    assert s2 is not None
    assert s1.high_water_position == 4
    assert s2.high_water_position == 1


def test_conceal_moves_current_without_lowering_high_water(
    db: sqlite3.Connection, hooked_log: EventLog
) -> None:
    hooked_log.reveal(document_id=1, chunk_id=0, created_at=T0)
    hooked_log.reveal(document_id=1, chunk_id=1, created_at=T0 + timedelta(seconds=1))
    hooked_log.reveal(document_id=1, chunk_id=2, created_at=T0 + timedelta(seconds=2))
    hooked_log.conceal(document_id=1, chunk_id=1, created_at=T0 + timedelta(seconds=3))
    state = load_reading_state(db, document_id=1)
    assert state is not None
    assert state.high_water_position == 2
    assert state.current_position == 1


def test_non_positional_events_advance_event_cursor_only(
    db: sqlite3.Connection, hooked_log: EventLog
) -> None:
    hooked_log.reveal(document_id=1, chunk_id=3, created_at=T0)
    hooked_log.rate_effort(
        document_id=1, chunk_id=3, rating=4, created_at=T0 + timedelta(seconds=1)
    )
    hooked_log.pin_set(
        document_id=1, chunk_id=3, color_id=2, created_at=T0 + timedelta(seconds=2)
    )
    state = load_reading_state(db, document_id=1)
    assert state is not None
    assert state.high_water_position == 3
    assert state.current_position == 3
    assert state.last_event_id_applied == 3  # all three events applied


def test_rebuild_from_events_matches_incremental(db: sqlite3.Connection) -> None:
    """Spec §18.5 — rebuild produces the same projection as the
    incremental cache that received the same events."""
    incremental = make_event_log(db)
    incremental.reveal(document_id=1, chunk_id=0, created_at=T0)
    incremental.reveal(document_id=1, chunk_id=1, created_at=T0 + timedelta(seconds=1))
    incremental.conceal(document_id=1, chunk_id=0, created_at=T0 + timedelta(seconds=2))
    incremental.reveal(document_id=1, chunk_id=2, created_at=T0 + timedelta(seconds=3))
    incremental.rate_effort(
        document_id=1, chunk_id=2, rating=5, created_at=T0 + timedelta(seconds=4)
    )

    incremental_state = load_reading_state(db, document_id=1)

    # Wipe the cache row and rebuild from the (still-intact) event log.
    db.execute("DELETE FROM reading_state WHERE document_id=1")
    db.commit()
    rebuilt = rebuild_reading_state(db, document_id=1, log=EventLog(db))

    assert rebuilt == incremental_state


def test_rebuild_matches_pure_builder(db: sqlite3.Connection) -> None:
    """Sanity: the cache rebuild and the pure builder agree event-for-event."""
    log = make_event_log(db)
    log.reveal(document_id=1, chunk_id=0, created_at=T0)
    log.reveal(document_id=1, chunk_id=1, created_at=T0 + timedelta(seconds=1))
    log.reveal(document_id=1, chunk_id=2, created_at=T0 + timedelta(seconds=2))
    log.conceal(document_id=1, chunk_id=1, created_at=T0 + timedelta(seconds=3))
    log.reveal(document_id=1, chunk_id=1, created_at=T0 + timedelta(seconds=4))  # free re-reveal

    pure = build_reading_state(
        document_id=1, events=EventLog(db).events_for_document(1)
    )
    cached = load_reading_state(db, document_id=1)
    assert pure == cached


def test_event_log_default_on_event_is_no_op(db: sqlite3.Connection) -> None:
    """ISC-17 regression guard: passing no on_event must leave the
    cache untouched — existing call sites unchanged."""
    log = EventLog(db)
    log.reveal(document_id=1, chunk_id=5, created_at=T0)
    assert load_reading_state(db, document_id=1) is None


def test_projections_cache_does_not_import_from_web() -> None:
    tree = ast.parse(
        Path("parsem/store/projections_cache.py").read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("parsem.web"), (
                f"forbidden import: from {node.module}"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("parsem.web"), (
                    f"forbidden import: {alias.name}"
                )
