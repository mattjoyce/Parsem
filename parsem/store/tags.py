"""Document tags persistence. ADR 0005 (Library v2); bd Parsem-7wu.

Manual tags only in v2.0. LLM-suggested tags are deferred to a later
phase — at that point this module gets a `source` column on the table
and a `source='auto' | 'manual'` distinction. For now: one shape, one
source, simplest possible.

Names: a tag is a normalised lowercase string. The store is the
authority for normalisation — every write goes through `normalise_tag`
and every read returns already-normalised values. Callers (routes,
templates) treat tags as opaque strings.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime

_MAX_LEN = 32
_WHITESPACE_RE = re.compile(r"\s+")


def normalise_tag(raw: str) -> str:
    """Coerce a user-typed string into a canonical tag value.

    Rules:
    - Strip leading/trailing whitespace.
    - Lowercase.
    - Collapse interior whitespace runs to single hyphens.
    - Reject if empty after normalisation or longer than 32 chars.

    Raises ValueError on invalid input — the route layer surfaces the
    detail as a 422. Hyphens-not-spaces is a deliberate "let it crash
    early" choice: a user typing "Brick Wisdom" gets `brick-wisdom`,
    not silent splitting.
    """
    s = (raw or "").strip().lower()
    if not s:
        raise ValueError("Tag must not be empty.")
    s = _WHITESPACE_RE.sub("-", s)
    if len(s) > _MAX_LEN:
        raise ValueError(f"Tag must be {_MAX_LEN} chars or fewer.")
    return s


def add_tag(
    conn: sqlite3.Connection,
    document_id: int,
    tag: str,
    *,
    now: datetime,
) -> bool:
    """Tag a document. Normalises before insert. Idempotent — re-adding
    an existing tag returns False without raising.

    Returns True when a new row was written, False when the (doc, tag)
    pair already existed. Raises ValueError when the tag is malformed.
    """
    canonical = normalise_tag(tag)
    cur = conn.execute(
        "INSERT OR IGNORE INTO document_tags (document_id, tag, created_at)"
        " VALUES (?, ?, ?)",
        (document_id, canonical, now.isoformat()),
    )
    conn.commit()
    return cur.rowcount > 0


def remove_tag(
    conn: sqlite3.Connection,
    document_id: int,
    tag: str,
) -> bool:
    """Untag a document. Normalises before lookup so the route can pass
    whatever the URL carried.

    Returns True when a row was deleted, False when no such tag existed.
    Does not raise on a non-existent (doc, tag) pair — the route can
    treat that as a no-op 200.
    """
    canonical = normalise_tag(tag)
    cur = conn.execute(
        "DELETE FROM document_tags WHERE document_id=? AND tag=?",
        (document_id, canonical),
    )
    conn.commit()
    return cur.rowcount > 0


def list_tags_for_doc(
    conn: sqlite3.Connection, document_id: int
) -> list[str]:
    """Return a doc's tags, sorted alphabetically for stable rendering."""
    rows = conn.execute(
        "SELECT tag FROM document_tags WHERE document_id=? ORDER BY tag",
        (document_id,),
    ).fetchall()
    return [row["tag"] for row in rows]


def list_all_tags(conn: sqlite3.Connection) -> list[str]:
    """Return every distinct tag in the library, alphabetically. Powers
    the control-strip chip row when zero filters are active.
    """
    rows = conn.execute(
        "SELECT DISTINCT tag FROM document_tags ORDER BY tag"
    ).fetchall()
    return [row["tag"] for row in rows]


def load_tags_for_documents(
    conn: sqlite3.Connection, document_ids: list[int]
) -> dict[int, list[str]]:
    """Bulk-load tags for a batch of documents. Used by
    `list_library_rows` to avoid an N+1 query against `document_tags`.

    Returns a dict keyed by document_id; documents with no tags get an
    empty list (every input id appears in the result).
    """
    result: dict[int, list[str]] = {doc_id: [] for doc_id in document_ids}
    if not document_ids:
        return result
    placeholders = ",".join("?" * len(document_ids))
    rows = conn.execute(
        f"SELECT document_id, tag FROM document_tags"
        f" WHERE document_id IN ({placeholders})"
        f" ORDER BY document_id, tag",
        document_ids,
    ).fetchall()
    for row in rows:
        result[row["document_id"]].append(row["tag"])
    return result
