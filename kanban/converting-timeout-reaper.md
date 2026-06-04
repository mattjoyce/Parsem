---
id: 2
status: todo
priority: High
blocked_by: []
assignee: "@matt"
tags: [ingest, docling, reliability]
---

# Add a `converting` timeout/reaper (converting → failed)

**Job Story:** When a docling conversion never produces its `.md`, I want the
document to fall out of `converting` into `failed` after a bounded wait, so I can
see it failed and retry it instead of it hanging forever.

Doc 21 has sat in `converting` for 11 days. A PDF that stages to docling but never
yields a converted `.md` (PIPELINE 2 never fires) has no escape today — there is
no timeout, no dead-letter, nothing flips it to `failed`. That also blocks recovery:
card #1's reprocess path keys off non-`ready` status, but a doc stuck in
`converting` should be reaped to `failed` so a re-drop is unambiguously a retry.

## Acceptance Criteria
- A document in `converting` past a bounded threshold (e.g. N minutes) transitions
  to `failed` with a clear reason ("conversion timed out").
- The reaper is idempotent and safe to run repeatedly.
- A reaped doc is eligible for reprocess via card #1.
- Threshold is a single config value, not scattered constants.

## Narrative
- 2026-06-04: Identified while diagnosing the stuck-files report. This is NOT a
  dedup bug — it's a missing lifecycle terminal state for conversions that never
  complete. Discovered via parsem DB: doc 21 `2411.15594v6` status=converting since
  2026-05-24. (by @assistant)
