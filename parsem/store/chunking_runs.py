"""ChunkingRun persistence — provenance for chunking decisions.

Spec: AtomicChunkingPhase1.md §ChunkingRun. A run records *which*
deterministic rules produced *which* chunks. Strategy + version +
rules_hash form the identity; same triple + same revision + same
atomic pieces should yield identical chunks.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ChunkingRun:
    id: int
    revision_id: int
    strategy_name: str
    strategy_version: str
    rules_hash: str
    created_at: datetime


def insert_chunking_run(
    conn: sqlite3.Connection,
    *,
    revision_id: int,
    strategy_name: str,
    strategy_version: str,
    rules_hash: str,
    now: datetime,
) -> ChunkingRun:
    cur = conn.execute(
        "INSERT INTO chunking_runs"
        " (revision_id, strategy_name, strategy_version, rules_hash, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (revision_id, strategy_name, strategy_version, rules_hash, now.isoformat()),
    )
    run_id = cur.lastrowid
    assert run_id is not None
    return ChunkingRun(
        id=run_id,
        revision_id=revision_id,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        rules_hash=rules_hash,
        created_at=now,
    )


def load_latest_chunking_run(
    conn: sqlite3.Connection, document_id: int
) -> ChunkingRun | None:
    """Latest run for a document — joined through document_revisions so
    callers don't need to know the revision id ahead of time."""
    row = conn.execute(
        "SELECT cr.id, cr.revision_id, cr.strategy_name, cr.strategy_version,"
        " cr.rules_hash, cr.created_at"
        " FROM chunking_runs cr"
        " JOIN document_revisions dr ON dr.id = cr.revision_id"
        " WHERE dr.document_id = ?"
        " ORDER BY cr.id DESC LIMIT 1",
        (document_id,),
    ).fetchone()
    if row is None:
        return None
    return ChunkingRun(
        id=row["id"],
        revision_id=row["revision_id"],
        strategy_name=row["strategy_name"],
        strategy_version=row["strategy_version"],
        rules_hash=row["rules_hash"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
