---
id: 3
status: todo
priority: Normal
blocked_by: []
assignee: "@matt"
tags: [ingest, ductile, folderwatch, reliability]
---

# Folderwatch cursor should advance only on confirmed parsem ingest

**Job Story:** When ductile sees a file but parsem never accepts it, I want the
watcher to re-fire rather than mark it done, so a dropped file is never silently
orphaned.

Two `s41746-*.pdf` files sit in `inbound/raw/` as `tracked` (the ductile log shows
`scanned=4 tracked=4 events=0` every poll) yet have **no document row at all** in
parsem. The watch cursor advanced on first sight, independent of whether parsem
returned a terminal action — so a failed/missed knock strands the file forever.

**Decision (contract at the edge):** the watch cursor advances only when parsem
returns 200 with a terminal action (`ingested` / `submit_to_docling` / `duplicate`
/ `unsupported`). Otherwise the file re-fires next poll.

## Acceptance Criteria
- A file is considered "handled" by the watcher only after parsem confirms a
  terminal action for it.
- A knock that errors / times out leaves the file eligible to re-fire.
- No duplicate documents result from re-fires (relies on parsem's hash dedup —
  card #1).
- The two existing `s41746` PDFs would be picked up under the new rule.

## Narrative
- 2026-06-04: Found alongside the doc-21 stuck case. The watcher's "tracked" state
  is sight-based, not outcome-based — Armstrong's confirm-at-the-edge is violated.
  Lives in the ductile `folder_watch` plugin, not parsem; needs coordination with
  the ductile pipeline. (by @assistant)
