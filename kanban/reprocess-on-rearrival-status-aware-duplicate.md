---
id: 1
status: todo
priority: High
blocked_by: []
assignee: "@matt"
tags: [ingest, docling, dedup, arrivals]
---

# Make raw-arrival `duplicate` status-aware (reprocess instead of no-op)

**Job Story:** When I re-drop a file whose conversion got stuck or failed, I want
the pipeline to redo it and overwrite the result, so I can recover without
inventing a new document.

Today `process_raw_arrival` (`parsem/ingest/arrivals.py:135-139`) does a blanket
content-hash dedup: any arriving hash that matches an existing document returns
`action="duplicate"` and leaves the file untouched. That is correct for ductile's
create+rename double-fire and 5xx retries — the hash is what makes those safe —
but it also swallows a deliberate re-drop of a stuck document.

**Decision:** keep the content hash as identity (never time-stamp it); make the
`duplicate` branch decide on the *existing doc's status* plus the incoming file's
freshness (mtime/size), not a blanket no-op.

## Acceptance Criteria
- Hash matches an existing doc that is `converting` / `failed` / `processing`
  → **reprocess** (re-run docling on the stored `source.pdf`, overwrite
  `document.md`), keeping the same `doc_id`.
- Hash matches a `ready` doc and the incoming file is **not newer** (mtime ≤ stored)
  → `duplicate` no-op. This is the double-fire / retry path and MUST stay safe.
- Hash matches a `ready` doc and the incoming file **is newer** (mtime >)
  → **reprocess + overwrite**, keeping `doc_id`.
- Reprocess **appends** an `extraction_runs` row (history preserved), overwrites
  the current `document.md`, and preserves `doc_id`.
- Idempotency under ductile retry is unchanged (no duplicate documents from a
  single drop).

## Narrative
- 2026-06-04: Root-caused from the stuck `2411.15594v6-retry.pdf` case. Doc 21 is
  wedged in `converting`; the manual `-retry` re-drop had identical bytes, so the
  content-hash dedup returned `duplicate` and did nothing — the retry was
  swallowed. Rejected the user's "temporal nonce" idea: a nonce braids time into
  identity and would defeat the very idempotency (against ductile's create+rename
  double-fire and 5xx retries) that the hash provides. Resolution: content decides
  *which* document; a newer file-mtime or non-`ready` status decides *whether to
  redo it*; time never decides identity. (by @assistant)
