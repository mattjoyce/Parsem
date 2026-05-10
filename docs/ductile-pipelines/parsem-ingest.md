# Parsem ingest — Ductile pipeline brief

Brief for the Ductile pipeline developer. Two pipelines route content into Parsem's library: the first watches Parsem's drop folder and dispatches to Marker for PDFs, the second watches Marker's output folder and tells Parsem the conversion is done. Both pipelines are stateless and retry-safe.

This document is the contract Ductile must honour. Parsem's side is shipped at commit `ac6d4fb` (`main`); see `docs/adr/0002-ductile-driven-eventing.md` for the architectural rationale.

---

## TL;DR

| Pipeline | Watches | Calls | When |
|---|---|---|---|
| **PIPELINE 1** | `inbound/raw/` (folderwatch) | Parsem `/ingest/raw-arrived`; conditionally Marker `/submit` | Anything arrives in raw/ — uploads, URL fetches, manual drops |
| **PIPELINE 2** | `inbound/converted/*.md` (filewatch) | Parsem `/ingest/converted-arrived` | Marker finishes a conversion |

You will not need to: hold per-job state, hash files, dedup, decide md-vs-pdf, hold the converting placeholder, retry within Parsem's logic, or manage Marker container IDs after submit.

---

## Folders

All on the NAS share `/mnt/user/Library/parsem-library/` — Parsem and Marker both bind-mount it; Ductile must see the same paths.

```
/mnt/user/Library/parsem-library/
├── inbound/
│   ├── raw/         ← PIPELINE 1 watches here
│   └── converted/   ← PIPELINE 2 watches here; Marker writes here
└── originals/       ← Parsem-internal; Ductile does not touch
```

> [!note] Both watched dirs are write-once-then-rename
> Parsem (raw drops via web/URL/upload) and Marker (converted output) both write into a temp file in the same dir, then rename into place. Watch the **rename event** as well as create — files copied to a network share often arrive as temp + rename; the rename target is what matters. (Standard fsevents convention; mentioned only because NAS shares often surface this.)

---

## Endpoints

### Parsem (the receiver — what Ductile calls)

Base URL configurable. Production target: `http://parsem.local:8000` (or whatever the unRAID container IP/hostname resolves to).

| Endpoint | Method | Body | Returns |
|---|---|---|---|
| `/ingest/raw-arrived` | POST | `{"path": "<abs path inside inbound/raw/>"}` | JSON envelope (see below) |
| `/ingest/converted-arrived` | POST | `{"path": "<abs path inside inbound/converted/>"}` | JSON envelope |

Both require:
- `Content-Type: application/json`
- `Authorization: Bearer <token>` **when** Parsem's `ingest.callback_token` config is set (production: yes; dev: empty = no auth required). Token is shared out-of-band by the operator.

Both are **idempotent**. Calling either twice with the same path produces no duplicate work. Retry without fear.

### Marker (the converter — Ductile calls during PIPELINE 1)

Already documented at `/Volumes/Projects/ductile-plugins/marker/README.md`. Quick reference:

```
POST http://<ductile-host>:8888/plugin/marker/submit
Authorization: Bearer <marker-token>
Content-Type: application/json

{
  "payload": {
    "source":     "<host path to PDF>",       // from Parsem's response
    "output_dir": "/mnt/user/Library/parsem-library/inbound/converted/",
    "doc_id":     "<string from Parsem's response>"
  }
}
```

Marker returns a job envelope; Ductile does NOT need to poll status — PIPELINE 2's filewatch IS the completion signal. Marker writes `<doc_id>.md` last under an atomic-write contract, so the appearance of `<doc_id>.md` in `inbound/converted/` is the definitive "done" event.

---

## PIPELINE 1 — folderwatch on `inbound/raw/`

**Trigger**: file created or moved into `/mnt/user/Library/parsem-library/inbound/raw/` (one level, non-recursive).

**Flow**:

```
1. POST parsem:/ingest/raw-arrived  {"path": "<event_path>"}

2. Parse the response. The body is:
     {
       "action":       "ingested" | "submit_to_marker" | "duplicate" | "unsupported",
       "document_id":  <int or null>,
       "doc_id":       <string or null>,    // only on submit_to_marker
       "source_path":  <string or null>,    // only on submit_to_marker
       "reason":       <string or null>     // present on unsupported / failed cases
     }

3. Branch on `action`:
     "ingested"        → done. (.md was parsed in place; nothing more to do.)
     "duplicate"       → done. (Same content already in library.)
     "unsupported"     → done. (Parsem recorded a fail-row; user sees it in library.)
     "submit_to_marker" → POST marker:/submit with:
                            source     = response.source_path
                            output_dir = "/mnt/user/Library/parsem-library/inbound/converted/"
                            doc_id     = response.doc_id
                          done. (PIPELINE 2 will catch the eventual .md.)
```

**Sequence diagram**:

```
fs → ductile                folderwatch event for raw/<file>
ductile → parsem            POST /ingest/raw-arrived {path}
parsem → ductile            {action, document_id, [doc_id, source_path]}
ductile (if submit_to_marker):
ductile → marker            POST /submit {source, output_dir, doc_id}
marker → ductile            {job_id, state: "running"}    ← Ductile may discard this
                                                            (PIPELINE 2 is the trigger)
```

---

## PIPELINE 2 — filewatch on `inbound/converted/*.md`

**Trigger**: file created or moved into `/mnt/user/Library/parsem-library/inbound/converted/` with a `.md` extension. (Marker also writes a sidecar `.json` and an `<doc_id>_images/` dir — those are NOT triggers; the `.md` lands LAST per Marker's atomic-write contract.)

**Flow**:

```
1. POST parsem:/ingest/converted-arrived  {"path": "<event_path>"}

2. Parse the response:
     {
       "action":      "ingested" | "duplicate" | "missing_doc" | "failed",
       "document_id": <int or null>,
       "reason":      <string or null>
     }

3. Branch:
     "ingested"    → done. (Parsem parsed the .md, flipped doc to ready.)
     "duplicate"   → done. (Filewatch fired twice; second call is a no-op.)
     "missing_doc" → log a warning. The .md filename did not match any
                     converting doc Parsem knows about. Likely a stray file
                     from a manual marker run, or a Parsem db reset that
                     orphaned an in-flight job. No retry will help.
     "failed"      → log an error with response.reason. Parsem flipped the
                     doc to status=failed; user will see it. No retry.
```

---

## Failure handling & retry policy

| Condition | Ductile action |
|---|---|
| Parsem unreachable (connection refused, timeout) | Retry with exponential backoff. Files queue in `inbound/raw/` or `inbound/converted/` until Parsem is back. |
| Parsem returns 5xx | Retry. (Bug in Parsem; alert via standard channels.) |
| Parsem returns 401 | **Stop retrying.** Token is wrong; alert operator. |
| Parsem returns 200 with any `action` | **Done.** Even `failed` and `missing_doc` are normal outcomes — they communicate state, not transport errors. Do not retry. |
| Parsem returns 4xx other than 401 (e.g. malformed JSON) | Log + alert. No retry. |
| Marker `/submit` returns 5xx or unreachable | Retry the marker call, but **do not re-call Parsem `/ingest/raw-arrived`** — the converting doc row is already in place. Eventually give up + DLQ; operator can manually re-submit. |
| Marker container dies mid-conversion | No `.md` ever appears in `converted/`. Ductile's job ends after `/submit`. Cycle 3 of Parsem will add a "stuck converting" detector + retry UI. |

The fundamental property: **Parsem's endpoints are idempotent; retries cannot create duplicates.** Any retry policy you find sensible for your other pipelines is safe here.

---

## What Ductile does NOT do

Explicit non-concerns, to keep the contract small:

- **No content sniffing.** Ductile passes paths; Parsem dispatches. (Adding `.epub` later is a Parsem-only change.)
- **No dedup.** Parsem hashes file bytes server-side and short-circuits duplicates.
- **No job-state tracking.** Marker's container ID is irrelevant — Ductile can discard it after submit. The completion signal is filesystem-based.
- **No `converting` placeholder management.** Parsem creates the row at submit-to-marker time and flips it to ready when the converted .md arrives.
- **No file moves.** Parsem moves files out of `inbound/raw/`; Marker writes to `inbound/converted/`. Ductile's filesystem operations are read-only.
- **No content type knowledge** beyond "anything in raw/, .md in converted/."

---

## Dev story

There is no dev Marker — `marker:latest` only runs on unRAID. ThinkPad Ductile against the same NAS share serves as the dev gateway. To exercise the seam end-to-end:

1. Start Parsem locally (`uv run parsem` from `~/Projects/Parsem`) with `ingest.callback_token` empty so auth is off.
2. Wire up PIPELINE 1 against `/mnt/user/Library/parsem-library/inbound/raw/` (or a dev mount), pointing at the local Parsem.
3. Drop a `.md` file → expect `action=ingested`, file appears in `/library`.
4. For PDF testing without Marker: drop a `.pdf` → expect `action=submit_to_marker` + a "converting" row in Parsem's library. Then manually drop a `<doc_id>.md` (and optionally a `<doc_id>.json` sidecar) into `inbound/converted/` to fake Marker's output. PIPELINE 2 should fire and Parsem should flip to ready.

PDF→Markdown via real Marker only happens in production. Acceptable: the seam is small and the failure modes locate cleanly to one of {Ductile DSL, Parsem endpoint, Marker container}.

---

## Sidecar metadata (for awareness; nothing to do)

Marker writes `<doc_id>.json` next to `<doc_id>.md` with:

```json
{
  "doc_id":           "<same as submit>",
  "status":           "ready",
  "marker_version":   "1.10.2",
  "duration_seconds": 599.5,
  "image_count":      0,
  "completed_at":     "2026-05-10T10:25:55.612512+00:00",
  "source":           "/input/in.pdf",
  "output_md":        "/output/<doc_id>.md",
  "images_dir":       "/output/<doc_id>_images"
}
```

Ductile does not read the sidecar — Parsem reads it during `/ingest/converted-arrived` and persists relevant fields into its `extraction_runs` table. Mentioned only so you know Marker also writes a `.json` and a `_images/` directory; **only the `.md` is the trigger**.

---

## Open considerations (not blocking)

- **Polling fallback** — if NAS fsevents prove unreliable under load, add a periodic sweep (e.g. every 60s) over both watched directories. Each found file is treated as if a fresh event fired; Parsem's idempotency makes this safe.
- **Large drops** — bulk-paste of N markdown files into `raw/` will fan out N parallel `/ingest/raw-arrived` calls. Parsem's sqlite is single-writer; if this causes 5xx storms, gate Ductile's parallelism (e.g. concurrency=4) on these pipelines.
- **Authorization scheme** — bearer token is the simplest credential. If Ductile already has a richer scheme (mTLS, signed-request), Parsem can grow to match in a future cycle. Bearer is fine for the home-network deploy target.

---

## Quick checklist for the implementer

- [ ] PIPELINE 1 wired: folderwatch on `inbound/raw/`, calls Parsem, branches on `action`, conditionally calls Marker
- [ ] PIPELINE 2 wired: filewatch on `inbound/converted/*.md`, calls Parsem
- [ ] Bearer token configured (matches Parsem's `ingest.callback_token`)
- [ ] Retry policy: 5xx and connection errors retry; 401 alerts; 200 is final
- [ ] ThinkPad dev exercise of both pipelines against a local Parsem completes without manual intervention
- [ ] Production deploy: pipelines watch the real NAS share, pointed at the production Parsem container

Done = a `.md` dropped in `raw/` shows up in Parsem's library within a second; a `.pdf` dropped in `raw/` shows as "converting" within a second and flips to "ready" when Marker finishes.
