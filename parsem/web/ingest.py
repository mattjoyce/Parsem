"""Parse-and-persist pipeline shared by upload and retry-parse.

Spec: parsem-spec.md §17.1, §17.2. Beads: Parsem-cwj, Parsem-pnk.

Tries to parse + chunk + persist; on parse exception or empty input,
marks the document `failed` with a human-readable reason and returns
False. The caller (upload route or retry route) handles any
pre-state cleanup (retry-parse wipes prior chunks/sections; upload
starts from a freshly inserted row).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from parsem.domain.chunking import ChunkingConfig, chunk
from parsem.parse.markdown_parse import parse
from parsem.store.documents import (
    insert_chunks_and_sections,
    mark_document_failed,
    mark_document_ready,
)


def parse_and_persist(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    text: str,
    now: datetime,
) -> bool:
    """Parse → chunk → persist → mark ready. Returns True on success,
    False after recording a failure reason."""
    try:
        output = chunk(parse(text), ChunkingConfig())
    except Exception as exc:
        mark_document_failed(
            conn, document_id, reason=f"Parse failed: {exc}", now=now
        )
        return False
    if not output.chunks:
        mark_document_failed(
            conn, document_id, reason="Document is empty.", now=now
        )
        return False
    insert_chunks_and_sections(
        conn,
        document_id=document_id,
        chunks=output.chunks,
        sections=output.sections,
        now=now,
    )
    mark_document_ready(
        conn, document_id, total_chunks=len(output.chunks), now=now
    )
    return True
