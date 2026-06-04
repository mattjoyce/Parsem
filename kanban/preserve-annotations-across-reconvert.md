---
id: 4
status: backlog
priority: Normal
blocked_by: [1]
assignee: "@matt"
tags: [ingest, reader, chunks, annotations]
---

# Preserve pins / reading-state across a reconvert (chunk identity vs position)

**Job Story:** When a document is reconverted and its text shifts, I want my pins,
ratings, and reading position to stay attached to the right content, so reprocessing
doesn't silently scramble my annotations.

Card #1 makes reprocess overwrite `document.md` while keeping `doc_id`. But the
projection trio (reading_state, chunk_ratings, pins) is largely position-keyed.
If reconversion shifts chunk positions, position-keyed pins/ratings drift onto the
wrong content. This is the chunk-identity-vs-position question — out of scope for
card #1's mechanics but a real correctness risk once reprocess+overwrite ships.

## Acceptance Criteria
- Decide and document: do annotations survive a reconvert, reset, or migrate by a
  stable chunk identity?
- If migrating: a chunk-identity scheme that survives re-chunking, not raw position.
- Reprocess does not silently move a pin/rating onto unrelated content.

## Narrative
- 2026-06-04: Flagged during the reprocess design as the one real follow-up risk.
  Blocked by #1 (the overwrite-on-reprocess path) since that's what surfaces the
  drift. Relates to the existing position→chunks.id translation at the cache layer
  (1na ratings, pv8 pins). (by @assistant)
