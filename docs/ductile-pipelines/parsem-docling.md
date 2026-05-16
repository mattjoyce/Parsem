# Parsem ingest (docling-pdf) — Ductile pipeline brief

Brief for the Ductile pipeline developer. Supersedes the Marker section of `parsem-ingest.md`: PDFs now route to the `docling-pdf` plugin instead of `marker`. The eventing shape (ADR 0002) is unchanged; see `docs/adr/0004-pdf-conversion-engine-docling-polish.md` for the rationale.

Two pipelines route content into Parsem's library. The first watches the drop folder and dispatches PDFs to `docling-pdf`; the second watches the output folder and tells Parsem the conversion is done. Both pipelines are stateless and retry-safe.

---

## TL;DR

| Pipeline | Watches | Calls | When |
|---|---|---|---|
| **PIPELINE 1** | `inbound/raw/` (folderwatch) | Parsem `/ingest/raw-arrived`; conditionally `docling-pdf` `/handle` | Anything arrives in raw/ |
| **PIPELINE 2** | `inbound/converted/*.md` (filewatch) | Parsem `/ingest/converted-arrived` | docling-pdf finishes a conversion |

You will not need to: hold per-job state, hash files, dedup, decide md-vs-pdf, hold the converting placeholder, or retry within Parsem's logic.

---

## Folders

All on the NAS share `/mnt/user/Library/parsem-library/` — Parsem and the `docling-pdf` plugin both see it; Ductile must see the same paths.

```
/mnt/user/Library/parsem-library/
├── inbound/
│   ├── raw/         ← PIPELINE 1 watches here
│   └── converted/   ← PIPELINE 2 watches here; docling-pdf writes here
└── originals/       ← Parsem-internal; Ductile does not touch
```

Both watched dirs are write-once-then-rename; watch the **rename** event as well as create.

---

## Endpoints

### Parsem (the receiver)

| Endpoint | Method | Body | Returns |
|---|---|---|---|
| `/ingest/raw-arrived` | POST | `{"path": "<abs path inside inbound/raw/>"}` | JSON envelope (below) |
| `/ingest/converted-arrived` | POST | `{"path": "<abs path inside inbound/converted/>"}` | JSON envelope |

Both require `Authorization: Bearer <token>` when Parsem's `ingest.callback_token` is set, and `Content-Type: application/json`. Both are idempotent — retry without fear.

### docling-pdf (the converter — Ductile calls during PIPELINE 1)

Documented at `/Volumes/Projects/ductile-plugins/docling-pdf/README.md`. Quick reference:

```
POST http://<ductile-host>:8888/plugin/docling-pdf/handle
Authorization: Bearer <docling-pdf-token>
Content-Type: application/json

{
  "payload": {
    "source":     "<host path to PDF>",        // = Parsem's response.source_path
    "doc_id":     "<string from Parsem's response>",
    "output_dir": "/mnt/user/Library/parsem-library/inbound/converted/"
  }
}
```

The plugin writes `<doc_id>.json` then `<doc_id>.md` atomically (the `.md` lands LAST). Ductile does NOT poll the plugin — PIPELINE 2's filewatch on `<doc_id>.md` IS the completion signal.

---

## PIPELINE 1 — folderwatch on `inbound/raw/`

**Trigger**: file created or moved into `inbound/raw/` (one level, non-recursive).

```
1. POST parsem:/ingest/raw-arrived  {"path": "<event_path>"}

2. Response body:
     {
       "action":      "ingested" | "submit_to_docling" | "duplicate" | "unsupported",
       "document_id": <int or null>,
       "doc_id":      <string or null>,    // only on submit_to_docling
       "source_path": <string or null>,    // only on submit_to_docling
       "reason":      <string or null>     // present on unsupported / failed
     }

3. Branch on `action`:
     "ingested"          → done.
     "duplicate"         → done.
     "unsupported"       → done. (Parsem recorded a fail-row.)
     "submit_to_docling" → POST docling-pdf:/handle with:
                             source     = response.source_path
                             doc_id     = response.doc_id
                             output_dir = "<library>/inbound/converted/"
                           done. (PIPELINE 2 catches the eventual .md.)
```

> **Migration note.** During cutover the `submit_to_docling` action may be routed to the legacy `marker` plugin instead (it accepts `source`/`output_dir`/`doc_id` and writes a `marker_version` sidecar). Parsem tolerates either sidecar shape — the sidecar is metadata only. Flip the route to `docling-pdf` once a real reading session converts cleanly, then retire the `marker` plugin/image.

---

## PIPELINE 2 — filewatch on `inbound/converted/*.md`

**Trigger**: `.md` created or moved into `inbound/converted/`. The `.md` lands LAST per the plugin's atomic-write contract — the `.json` sidecar is NOT a trigger.

```
1. POST parsem:/ingest/converted-arrived  {"path": "<event_path>"}

2. Response: {"action": "ingested"|"duplicate"|"missing_doc"|"failed", "document_id":..., "reason":...}

3. Branch:
     "ingested"    → done.
     "duplicate"   → done. (Filewatch fired twice; no-op.)
     "missing_doc" → log warning. No retry will help.
     "failed"      → log error with reason. No retry.
```

---

## Failure handling & retry policy

| Condition | Ductile action |
|---|---|
| Parsem unreachable / 5xx | Retry with backoff. Files queue until Parsem is back. |
| Parsem 401 | **Stop retrying.** Alert operator. |
| Parsem 200 with any `action` | **Done.** Even `failed`/`missing_doc` are normal outcomes. |
| docling-pdf `/handle` returns `error` with `retry:true` (5xx/timeout) | Retry the plugin call; do NOT re-call Parsem `/ingest/raw-arrived` (the converting row already exists). |
| docling-pdf `/handle` returns `error` with `retry:false` (corrupt PDF, auth, bad input) | Stop. DLQ; operator inspects. No `.md` will appear. |

Parsem's endpoints are idempotent; retries cannot create duplicates.

---

## Sidecar metadata (for awareness; nothing to do)

`docling-pdf` writes `<doc_id>.json` next to `<doc_id>.md`:

```json
{
  "doc_id":                  "<same as submit>",
  "status":                  "ready",
  "docling_pdf_version":     "0.1.0",
  "docling_version":         "2.x.y",
  "llm_provider":            "gemini",
  "llm_model":               "gemini-2.5-pro",
  "polish_prompt_version":   "v1",
  "page_count":              17,
  "parse_duration_seconds":  4.231,
  "polish_duration_seconds": 9.874,
  "polish_skipped_reason":   null,
  "source":                  "/abs/in.pdf",
  "started_at":              "<ISO>",
  "completed_at":            "<ISO>"
}
```

Ductile does not read it — Parsem persists relevant fields into `extraction_runs` (`extractor_name="docling"`). Mentioned only so you know a `.json` is written; **only the `.md` is the trigger**.

---

## Quick checklist for the implementer

- [ ] PIPELINE 1 wired: folderwatch on `inbound/raw/`, calls Parsem, branches on `action`, conditionally calls `docling-pdf`
- [ ] PIPELINE 2 wired: filewatch on `inbound/converted/*.md`, calls Parsem
- [ ] Bearer tokens configured (Parsem `ingest.callback_token`; `docling-pdf` plugin token)
- [ ] `docling-pdf` plugin config has `gemini_api_key` (or `llm_provider`/`anthropic_api_key`)
- [ ] Retry policy: 5xx/connection retry; 401 alerts; 200 is final; plugin `retry` flag honoured
- [ ] Cutover: route `submit_to_docling` → `docling-pdf` after a clean real-document conversion; retire `marker`

Done = a `.md` dropped in `raw/` shows in the library within a second; a `.pdf` shows as "converting" then flips to "ready" when docling-pdf finishes.
