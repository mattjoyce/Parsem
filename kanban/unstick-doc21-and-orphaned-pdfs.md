---
id: 5
status: done
priority: High
blocked_by: []
assignee: "@matt"
tags: [ops, ingest, docling]
---

# Operational: unstick doc 21 + the two orphaned s41746 PDFs

**Job Story:** When files are stranded in the live pipeline, I want them cleared
manually now, so the library is correct while the systemic fixes (cards #1–#3) land.

Current strays on the NAS (`/mnt/user/Library/parsem-library/inbound/raw/`):

- `2411.15594v6-retry.pdf` → tied to doc **21**, stuck `converting` since 2026-05-24.
- `s41746-024-01315-1.pdf` → no document row (orphaned, never ingested).
- `s41746-025-01670-7.pdf` → no document row (orphaned, never ingested).
- `pipeline-test.md` → stale 2026-05-11 test file; likely just delete.

This is a manual remediation, distinct from the systemic fixes. Touches the live
pipeline — confirm with Matt before acting.

## Acceptance Criteria
- Doc 21 either converts successfully or is moved to `failed` and re-submitted.
- Both `s41746` PDFs are ingested as documents (or consciously discarded).
- `pipeline-test.md` resolved (delete or ingest).
- `inbound/raw/` left clean.

## Narrative
- 2026-06-04: Verified live: ductile-docling container healthy (doc 23 converted &
  went `ready` today, proving the pipeline works end-to-end). These four are
  pre-existing strays from late May, held back by the dedup/lifecycle gaps in
  cards #1–#3. Awaiting Matt's go-ahead before touching the live pipeline.
  (by @assistant)
- 2026-06-04: "Proceed as you see fit." Re-drove the two `s41746` orphans by
  bumping mtime (confirmed empirically the folderwatch keys on path+mtime+size, not
  inode — a plain `mv` out-and-back preserved mtime and did NOT re-fire). Both
  converted, but ingest hit a concurrency bug (→ new card #6) because I touched both
  at once. Recovered cleanly by `parsem rechunk` **one at a time**: doc 24 → ready
  (108 chunks), doc 25 → ready (100 chunks). Lesson banked: ingest strictly one doc
  at a time until #6 is fixed. (by @assistant)
- 2026-06-04: Doc 21 has no `document.md` (never converted), so it needs a real
  docling run, not rechunk. Drove the satellite directly at its real address
  `http://192.168.20.4:8889/convert` (NOT container localhost:8000 — that refused;
  the published port is 8889). Single file → single converted-arrival → no race.
  pipeline-test.md still pending (likely just delete). (by @assistant)
- 2026-06-04: Doc 21 converted (HTTP 200, 64 pages, 256s, docling 2.95.0) →
  PIPELINE 2 `action=ingested` → **ready, 273 chunks**. The second filewatch fire
  correctly returned `action=duplicate` (idempotency as designed). Library is now
  13/13 ready, zero converting, zero failed. **Remaining = cleanup deletes only**
  (awaiting Matt's confirmation, destructive): `raw/2411.15594v6-retry.pdf` (now
  redundant), `raw/pipeline-test.md` (stale 2026-05-11 test), and orphan
  `converted/6.*` (output for a doc 6 that no longer exists in the DB). (by @assistant)
- 2026-06-04: Matt confirmed cleanup. Deleted `raw/2411.15594v6-retry.pdf`,
  `raw/pipeline-test.md`, and `converted/6.*`. Both `inbound/raw/` and
  `inbound/converted/` are now empty. Card complete: all stranded docs ready,
  inbound clean. (by @assistant)
