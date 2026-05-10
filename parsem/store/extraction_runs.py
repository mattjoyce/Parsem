"""Persistence for source→markdown extraction provenance.

Spec: parsem-spec.md (PDF-readiness hooks, claude-axx.7); ADR 0002
(ductile-driven eventing). One row per Marker conversion (or any other
extractor we add later — epub, docx). Lets a future re-conversion
compare extractor versions and decide whether to redo the work.

Pure SQL. The arrivals layer hands in the values it parsed from
Marker's sidecar JSON; no extractor knowledge lives here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime


def insert_extraction_run(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    source_type: str,
    extractor_name: str,
    extractor_version: str,
    source_path: str,
    params: dict[str, object] | None = None,
    now: datetime,
) -> int:
    """Insert an extraction_runs row; return the new id.

    `params` is a free-form dict (duration_seconds, image_count, ...);
    it serializes to JSON so the schema stays stable as Marker (or
    other extractors) add fields. NULL when caller has nothing to stash.
    """
    cur = conn.execute(
        "INSERT INTO extraction_runs "
        "(document_id, source_type, extractor_name, extractor_version,"
        " source_path, params_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            document_id,
            source_type,
            extractor_name,
            extractor_version,
            source_path,
            json.dumps(params) if params is not None else None,
            now.isoformat(),
        ),
    )
    new_id = cur.lastrowid
    assert new_id is not None
    conn.commit()
    return new_id
