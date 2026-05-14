# Parsem URL ingest — Firecrawl plugin brief

Brief for the Ductile plugin developer (the Firecrawl plugin author) and the Parsem-side endpoint author. One new ductile plugin (`firecrawl`) plus one new Parsem endpoint (`POST /ingest/url`) together replace `parsem/ingest/url_fetch.py` with a ductile-driven URL → markdown path.

This document is the contract both sides must honour. The architectural rationale is in `docs/adr/0003-outbound-http-from-parsem.md`; the bead is `claude-5fp`.

---

## TL;DR

| Side | What | Calls | When |
|---|---|---|---|
| **Parsem** | `POST /ingest/url` (new) | ductile `/plugin/firecrawl/scrape` | A user submits a URL via the web form (or programmatic client) |
| **Firecrawl plugin** | Scrapes URL via Firecrawl API; writes `<doc_id>.md` to `inbound/converted/` | Firecrawl HTTPS API | Parsem invokes |
| **Existing filewatch** | `inbound/converted/*.md` → `/ingest/converted-arrived` | Parsem (unchanged) | Plugin completes |

The plugin is the *converter* for URLs. Parsem owns the user-facing endpoint and the `converting` row; the plugin owns the network call to Firecrawl and the atomic write of the resulting markdown. Filewatch closes the loop using the existing seam — no new Parsem ingest code on the receive side.

---

## Folders

Same NAS share as the Marker pipeline:

```
/mnt/user/Library/parsem-library/
├── inbound/
│   ├── raw/         ← unused by THIS pipeline (URLs don't stage on disk)
│   └── converted/   ← firecrawl plugin writes <doc_id>.md here; filewatch ingests
└── originals/       ← Parsem-internal
```

The plugin sees `inbound/converted/` only as a configurable output destination. It does not need to know what else lives on the share.

---

## Parsem endpoint — `POST /ingest/url`

**Path:** `POST /ingest/url`
**Body:** `application/json`

```json
{
  "url": "https://example.gov/regulation"
}
```

**Auth:** `Authorization: Bearer <token>` matching `ingest.callback_token` when set; permissive when unset (dev). Same token Parsem already requires on the other ingest endpoints — there is no separate URL-ingest token.

**Returns:** `202 Accepted` on successful submit to ductile.

```json
{
  "document_id": 42,
  "doc_id": "ab12cd34ef56",
  "action": "submitted"
}
```

**Internally:**

1. Mint a `doc_id` (same scheme as Marker — short URL-safe string).
2. Insert a `documents` row with `status='converting'`, `source_url=<url>`, no `content_hash` yet (the row exists so the library shows a "converting" placeholder; hash is set when the file arrives via filewatch).
3. Make a synchronous outbound `POST` to ductile's firecrawl plugin (see below) with a tight connect timeout. Per **ADR 0003**, this call is bounded by the request lifecycle.
4. On ductile 2xx: return 202 to the user.
5. On ductile 5xx / network error: **rollback the `documents` row** (or mark it `failed` with reason) and return `502` to the user.
6. On ductile 4xx (configuration error — bad URL, missing token, etc.): return `400` or `502` to the user with the upstream reason; the row is rolled back.

The endpoint is **not idempotent on URL** — the same URL submitted twice produces two `converting` rows. Dedup happens at the content-hash layer when the .md lands (existing `/ingest/converted-arrived` semantics). This matches the existing pattern for PDF re-uploads.

### Error response shape

```json
{
  "error": "ductile_unreachable" | "ductile_5xx" | "config" | "bad_url",
  "reason": "<short human string>",
  "ductile_status": 502
}
```

---

## Firecrawl plugin — `/plugin/firecrawl/scrape`

**Path:** `POST /plugin/firecrawl/scrape`
**Body:** ductile-standard envelope:

```json
{
  "payload": {
    "url": "https://example.gov/regulation",
    "doc_id": "ab12cd34ef56",
    "output_dir": "/mnt/user/Library/parsem-library/inbound/converted/"
  }
}
```

**Auth:** Bearer token, same scheme as the `marker` plugin. Token defined in ductile's `plugins.yaml` under `firecrawl.token` and shared with Parsem out-of-band (config field `ductile.api_token`).

**Returns (synchronous-on-accept):**

```json
{
  "ok": true,
  "job_id": "<plugin-internal-id>",
  "state": "submitted"
}
```

This is a fast-accept response. The actual scrape may run on for tens of seconds; the plugin returns as soon as the work is queued. The completion signal is **the appearance of `<doc_id>.md` in `output_dir`**, which filewatch handles independently.

### Plugin behaviour

1. Validate payload — `url`, `doc_id`, `output_dir` are all required, non-empty strings; `output_dir` must exist and be writable.
2. Accept the job, return 202 with `job_id`.
3. In a background worker (thread, asyncio task, or subprocess — implementer's choice): call Firecrawl's `scrape` API with `formats=["markdown"]`, `FIRECRAWL_API_KEY` from plugin env.
4. On success: atomic-write `<doc_id>.md` and `<doc_id>.json` sidecar to `output_dir`.
   - Atomic-write contract: write to `.tmp` files, fsync, `os.replace` the `.md` **last** (so filewatch never sees a half-written file).
5. On failure (network error, Firecrawl API error, etc.): write a sidecar `.json` recording the failure (no `.md`), and optionally write `<doc_id>.md` with a single line `# Failed: <reason>` so the user sees the failure in their library. (TBD during implementation — preference is the latter, so failures don't silently vanish.)

### Atomic-write contract

The `.md` must land **last**. Filewatch on `inbound/converted/*.md` is the definitive "done" signal; if other files (sidecars, future image dirs) appear after the `.md`, ingest will race.

```python
# Pseudocode
md_tmp = output_dir / f".{doc_id}.md.tmp"
json_tmp = output_dir / f".{doc_id}.json.tmp"
md_tmp.write_text(markdown)
json_tmp.write_text(json.dumps(sidecar))
os.replace(json_tmp, output_dir / f"{doc_id}.json")  # sidecar first
os.replace(md_tmp, output_dir / f"{doc_id}.md")      # .md last
```

### Sidecar contract — `<doc_id>.json`

```json
{
  "doc_id":             "ab12cd34ef56",
  "status":             "ready",          // or "failed"
  "url":                "<original URL submitted by user>",
  "final_url":          "<URL after redirects, from Firecrawl response>",
  "firecrawl_version":  "<SDK version>",
  "duration_seconds":   12.34,
  "status_code":        200,
  "completed_at":       "2026-05-14T10:25:55.612512+00:00",
  "reason":             null              // or string on failed
}
```

Parsem reads this during `/ingest/converted-arrived` and persists relevant fields into the existing `extraction_runs` table (same path Marker uses).

### Plugin manifest sketch

```yaml
manifest_spec: ductile.plugin
manifest_version: 1
name: firecrawl
version: 0.1.0
protocol: 2
entrypoint: run.py
description: "Scrapes URLs via Firecrawl API and writes clean markdown to a NAS path."
concurrency_safe: true
commands:
  - name: scrape
    type: write
    description: "Submit a URL for scraping; write <doc_id>.md to output_dir when done."
    idempotent: false
    retry_safe: true
    input_schema:
      url: string
      doc_id: string
      output_dir: string
  - name: health
    type: read
    description: "Verifies plugin reachable and FIRECRAWL_API_KEY is valid."
    idempotent: true
    retry_safe: true
config_keys:
  required: [firecrawl_api_key]
  optional: [max_retries, timeout_seconds, default_formats]
```

---

## Existing filewatch — `/ingest/converted-arrived` (UNCHANGED)

Reused as-is from the Marker pipeline. The same atomic-write contract applies. Parsem reads the sidecar `<doc_id>.json`, ingests the markdown, flips the row from `converting` → `ready`, writes an `extraction_runs` row.

No code change required on the receive side.

---

## Failure handling & retry policy

| Condition | Behaviour |
|---|---|
| Parsem → ductile timeout / 5xx | Parsem returns 502 to the user; `documents` row rolled back. No retry — the user can resubmit. |
| Parsem → ductile 401 | Token misconfigured. Surfaces as 502 with `reason=config`. Operator alert. |
| Plugin → Firecrawl rate-limit | Plugin retries internally with backoff (within `max_retries` config). On exhaustion, writes the failure sidecar. |
| Plugin → Firecrawl 4xx (bad URL etc.) | Writes failure sidecar; user sees the failure in library. |
| Plugin crashes mid-scrape | No `.md` ever appears in `converted/`. Cycle 3 of Parsem will add a "stuck converting" detector (per claude-mwx.3). |

The fundamental property: **Parsem's existing converted-arrived endpoint is idempotent under retries**, and the plugin's atomic write guarantees no half-files reach the filewatch. Any retry behaviour internal to the plugin is safe.

---

## What is explicitly NOT in scope

- **PDFs from URLs** — `.pdf` content fetched by Firecrawl is NOT auto-routed to Marker / docling. v1 handles HTML → markdown only. PDFs-from-URLs is a known gap; deferred for later. Open question: should the plugin sniff `Content-Type: application/pdf` and refuse with `reason=use_marker`, or write the PDF bytes to `inbound/raw/` for the existing PDF flow? Defer until we see one fail.
- **JS-rendered pages requiring browser interaction** — Firecrawl's `interact` API is not wired in v1. If a page needs interaction, scrape will succeed but content may be empty / wrong.
- **Authenticated URLs** — no cookie / session handling in v1.
- **Bulk URL submission** — one URL per request. Bulk is a UI concern, not a plugin concern.
- **Re-scraping** — re-running a previously scraped URL is a user action via the existing "re-ingest" UI (claude-mwx.3 territory). The plugin doesn't know about history.

---

## Dev story

1. Local Parsem with `ductile.base_url` pointed at ThinkPad ductile; `ingest.callback_token` empty (no auth).
2. Local firecrawl plugin can run in ThinkPad ductile (slim python image; pulls from `FIRECRAWL_API_KEY` env). Output dir is a local dev folder (e.g. `~/parsem-dev/library/inbound/converted/`).
3. Submit a URL → expect `documents` row with status `converting` in <1s → expect `.md` to appear in dev `converted/` within ~30s → filewatch fires → row flips to `ready`.
4. For testing without a real Firecrawl key: stub the SDK call in `run.py` to write a canned `.md` after a 2s sleep.

Production deploy: plugin baked into ductile on unraid; output dir is the NAS share; Parsem points at unraid ductile.

---

## Quick checklist

### Parsem side
- [ ] `parsem/web/routes/ingest.py` — new `POST /ingest/url` handler
- [ ] `parsem/ingest/ductile_client.py` (new) — per-request `httpx.Client` factory for outbound to ductile
- [ ] `parsem/config.py` — `ductile.base_url` and `ductile.api_token` config keys
- [ ] `parsem/store/documents.py` — confirm `source_url` column exists or add it
- [ ] Tests: endpoint test against a fake ductile (httpx mock); rollback-on-error verified
- [ ] Web UI URL form cuts over to `/ingest/url`; `parsem/ingest/url_fetch.py` removed
- [ ] CHANGELOG / commit message reference ADR 0003 and bead `claude-5fp`

### Ductile / plugin side
- [ ] `/Volumes/Projects/ductile-plugins/firecrawl/manifest.yaml`
- [ ] `/Volumes/Projects/ductile-plugins/firecrawl/run.py`
- [ ] `/Volumes/Projects/ductile-plugins/firecrawl/test_run.py` (mocks Firecrawl SDK)
- [ ] `/Volumes/Projects/ductile-plugins/firecrawl/README.md`
- [ ] Plugin registered in `~/.config/ductile/plugins.yaml` with `firecrawl_api_key` config + bearer token
- [ ] Slim python Dockerfile (matches jina-reader's deployment shape)
- [ ] Filewatch pipeline already covers `inbound/converted/*.md` — no new pipeline needed

Done = a URL pasted into Parsem's web form appears in the library as `converting` within a second, and flips to `ready` (with clean markdown content) shortly after Firecrawl finishes.
