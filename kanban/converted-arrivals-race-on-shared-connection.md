---
id: 6
status: todo
priority: High
blocked_by: []
assignee: "@matt"
tags: [ingest, concurrency, sqlite, reliability]
---

# Converted-arrivals race on the process-global SQLite connection

**Job Story:** When two converted PDFs land at the same time, I want each to ingest
independently and correctly, so a batch drop doesn't corrupt or fail ingests.

**Observed live (2026-06-04).** Two PDFs (docs 24, 25) finished docling conversion
together; ductile fired two `POST /ingest/converted-arrived` jobs **simultaneously**
(22:33:52 and :53). Both failed with `sqlite3.IntegrityError: FOREIGN KEY constraint
failed` — doc 25 at the `atomic_pieces` insert, doc 24 at the `chunks` insert — and
left **partial state**: doc 24 got a committed `document_revisions` row (id 15) with
no chunks; doc 25 got no revision row at all. A single document ingested moments
earlier (doc 23, alone) succeeded. Running `parsem rechunk 24` then `25`
**sequentially** (each a short-lived process with its own connection) recovered both
cleanly (108 and 100 chunks).

**Root cause.** Parsem serves ingest on a **process-global shared SQLite connection**
(`app.state.reader`, the documented single-tab assumption — see Parsem-2rp). Two
concurrent ingest requests interleave their transactions on that one connection:
`insert_revision` + `insert_chunking_artifacts` are not isolated, so one request's
in-flight revision is invisible/clobbered for the other → FK failures and torn
writes. `insert_chunking_artifacts` claims "wraps all writes in a single transaction"
but a shared connection has no per-request transaction boundary.

This is structural (Hickey: *place* — a single mutable connection shared as global
state; Armstrong: *isolation* — requests cannot fail independently; one writer needed,
or per-request connections/serialized writes).

## Acceptance Criteria
- Two (or N) converted-arrivals landing together each ingest correctly, no FK errors,
  no partial state.
- Decide the model: per-request connection, a serialized single-writer queue for
  ingest, or an explicit transaction boundary per request on a connection pool.
- `insert_chunking_artifacts`'s "single transaction" claim is actually enforced
  per-request.
- A regression test fires two concurrent converted-arrivals and asserts both ready.

## Narrative
- 2026-06-04: Surfaced by re-driving two orphaned PDFs at once during the card #5
  cleanup (my batching caused it — a useful accident). Directly extends the known
  single-tab limitation (Parsem-2rp) from "reads" to "concurrent ingest writes".
  Recovery path that works today: ingest/rechunk strictly one document at a time.
  (by @assistant)
