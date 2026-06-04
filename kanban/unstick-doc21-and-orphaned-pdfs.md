---
id: 5
status: todo
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
