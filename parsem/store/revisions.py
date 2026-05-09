"""DocumentRevision persistence — the immutable substrate.

Spec: AtomicChunkingPhase1.md §Library Ownership. Every chunking run, every
atomic piece, every chunk traces back to exactly one revision. The revision
is canonical: derived records (pieces, plans, chunks, sections) may be
discarded and rebuilt from `full_text` plus a versioned ruleset.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from parsem.parse.line_index import LineIndex


@dataclass(frozen=True)
class DocumentRevision:
    id: int
    document_id: int
    full_text: str
    content_hash: str
    line_index: LineIndex
    created_at: datetime


def compute_content_hash(text: str) -> str:
    """SHA-256 over exact UTF-8 bytes. Stable across platforms."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def insert_revision(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    full_text: str,
    now: datetime,
) -> DocumentRevision:
    """Create an immutable revision row. Returns the persisted record."""
    line_index = LineIndex.from_text(full_text)
    content_hash = compute_content_hash(full_text)
    cur = conn.execute(
        "INSERT INTO document_revisions"
        " (document_id, full_text, content_hash, line_index_json, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            document_id,
            full_text,
            content_hash,
            line_index.to_json(),
            now.isoformat(),
        ),
    )
    revision_id = cur.lastrowid
    assert revision_id is not None
    return DocumentRevision(
        id=revision_id,
        document_id=document_id,
        full_text=full_text,
        content_hash=content_hash,
        line_index=line_index,
        created_at=now,
    )


def load_latest_revision(
    conn: sqlite3.Connection, document_id: int
) -> DocumentRevision | None:
    """Most recent revision for a document, or None if it has none."""
    row = conn.execute(
        "SELECT id, document_id, full_text, content_hash, line_index_json,"
        " created_at FROM document_revisions"
        " WHERE document_id=? ORDER BY id DESC LIMIT 1",
        (document_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_revision(row)


def load_revision(
    conn: sqlite3.Connection, revision_id: int
) -> DocumentRevision | None:
    row = conn.execute(
        "SELECT id, document_id, full_text, content_hash, line_index_json,"
        " created_at FROM document_revisions WHERE id=?",
        (revision_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_revision(row)


def _row_to_revision(row: sqlite3.Row) -> DocumentRevision:
    return DocumentRevision(
        id=row["id"],
        document_id=row["document_id"],
        full_text=row["full_text"],
        content_hash=row["content_hash"],
        line_index=LineIndex.from_json(row["line_index_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
