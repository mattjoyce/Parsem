# ADR 0002: Ductile-driven eventing for the ingest pipeline

- **Status:** accepted
- **Date:** 2026-05-10
- **Tracking:** bd `claude-mwx` (epic), `claude-mwx.2` (cycle 2 implementation)
- **Supersedes:** ADR 0001 §"Marker trigger — monitor only", §"Ingest flow" (the watcher portion)

## Context

ADR 0001 had Parsem own a `watchdog`-based filesystem watcher on `inbound/raw/`, with a second one to be added on `inbound/converted/` for the Marker integration. The "Marker trigger — monitor only" decision was justified by Marker being a transient `--rm` container with no persistent identity to host a callback endpoint.

Between writing ADR 0001 and starting cycle 2, two things became clear:

1. **Ductile already has `folderwatch` and `filewatch` plugins.** They are first-class integration primitives.
2. **Parsem is the only pipeline that watches its own filesystem.** Email, BirdNET, and Marker itself are all driven through ductile. Parsem speaking filesystem directly is the outlier.

The ADR's "no persistent identity" premise dissolves the moment ductile's filewatch is the persistent caller. Marker still doesn't host a callback — but it doesn't need to. Ductile's filewatch on `inbound/converted/` IS the callback, and it knocks Parsem over HTTP just like every other pipeline.

## Decision

**Ductile owns all eventing for Parsem's ingest pipeline.** Parsem owns no filesystem watchers, opens no outbound HTTP clients, and runs no background threads for I/O. Parsem is a pure receiver — content service behind two endpoints.

### Endpoints (Parsem side)

```
POST /ingest/raw-arrived         body: {"path": "<abs path inside inbound/raw/>"}
POST /ingest/converted-arrived   body: {"path": "<abs path inside inbound/converted/>"}
```

Both require `Authorization: Bearer <token>` matching `ingest.callback_token` from config when that field is set; permissive when unset (dev). Both are idempotent — safe under ductile retries.

### `/ingest/raw-arrived` — internal dispatch

The endpoint reads the file, hashes its bytes for dedup, and dispatches by extension. The response carries a closed vocabulary of `action`:

| `action` | When | Side effects | Response fields |
|---|---|---|---|
| `ingested` | `.md` content, new hash | Parses, moves to `originals/<doc_id>/document.md`, status=ready | `document_id` |
| `submit_to_marker` | `.pdf` content, new hash | Inserts doc (status=converting), moves to `originals/<doc_id>/source.pdf` | `document_id`, `doc_id` (string), `source_path` |
| `duplicate` | Hash already in db | None | `document_id` (the existing one) |
| `unsupported` | Other extension | Inserts a fail-row with reason | `document_id` |

The ductile DSL reads `action` and routes accordingly. `submit_to_marker` is the only one that triggers a downstream call (to the Marker plugin).

### `/ingest/converted-arrived` — finish the converting doc

The filename is `<doc_id>.md`. The endpoint loads that document row (must exist in `status=converting`), then relocates Marker's `inbound/converted/` cluster into the document directory: `<doc_id>_images/` → `originals/<doc_id>/images/`, `<doc_id>.json` → `originals/<doc_id>/extraction.json`, and the markdown — with its image refs rewritten from `<doc_id>_images/` to `images/` — → `originals/<doc_id>/document.md`. It then calls `parse_and_persist` on the rewritten text, flips status to `ready`, and writes an `extraction_runs` row from the sidecar (`marker_version`, `duration_seconds`, `image_count`; schema v3 already migrated). The rewritten refs let the reader's `<img src="images/<f>">` resolve under `GET /documents/{id}/images/`.

### Ductile pipelines (other side of the seam)

```
on folderwatch /mnt/user/Library/parsem-library/inbound/raw/:
  POST parsem:/ingest/raw-arrived {path}
  if response.action == "submit_to_marker":
    POST marker:/submit {source: response.source_path,
                         output_dir: /mnt/user/Library/parsem-library/inbound/converted/,
                         doc_id: response.doc_id}
  # other actions: nothing else to do (Parsem already finished the work)

on filewatch /mnt/user/Library/parsem-library/inbound/converted/*.md:
  POST parsem:/ingest/converted-arrived {path}
```

Ductile holds no Parsem state across calls. Each request self-describes. The DSL is the only place that knows both Parsem and Marker exist; the two services know nothing of each other.

### Why this carve-up

- **Hickey: Parsem-owned watching complects "knowing about content" with "knowing when content arrived."** The eventing concern lives at the integration layer (ductile); the content concern lives in Parsem. Separating them removes a category of complexity from each.
- **Armstrong: idempotent endpoints + supervisor-style retries** at the ductile layer give us the failure semantics for free that the watchdog approach would have to hand-roll. Files queue in `inbound/raw/`; if Parsem is down, ductile retries; if Parsem stays down, files persist on disk.
- **Uniformity:** every Parsem input mode (web form upload, CLI `parsem add`, manual NAS drop, future email-to-Parsem via ductile) ends as a file in `inbound/raw/`. Ductile folderwatch picks all of them up uniformly. New input modes plug in with pipeline DSL, not Parsem code.
- **NAS fsevents flakiness** becomes ductile's problem to solve once, instead of Parsem's problem to solve twice (raw + converted).
- **No threading inside FastAPI.** Parsem is a request/response service; that shape stays clean.

### What survives from ADR 0001

- The directory contract: `/mnt/user/appdata/parsem/parsem.db` + `/mnt/user/Library/parsem-library/{inbound,originals}/`.
- The three input modes (web form, NAS drop, CLI) — all still write to `inbound/raw/`. Ductile folderwatch is now the unifying mechanism instead of Parsem's watcher.
- Hash-keyed dedup, async-only, "library shows converting placeholder."
- The phasing: cycle 2 here, cycle 3 (provenance + re-ingest UI + error retries) unchanged.

### What changes in code

- **Delete** `parsem/ingest/watcher.py` and the watchdog dependency entry in `pyproject.toml`.
- **Delete** the `start_watcher` lifespan wiring in `parsem/web/app.py`.
- **Add** `parsem/ingest/arrivals.py` — pure-core decision functions.
- **Add** `parsem/web/routes/arrivals.py` — thin endpoint adapter with bearer auth.
- **Add** `ingest.callback_token` to config (optional; permissive when unset).
- **Migrate** the existing watcher tests into endpoint tests against the same `process_*_arrival` core.

### Dev story

There is no dev Marker — the marker:latest image only runs on unRAID. Two seams exist:

1. **Parsem side (this ADR):** unit-tested end-to-end via FastAPI TestClient against a fake ductile (just plain HTTP POSTs in tests).
2. **Ductile side:** ThinkPad ductile mounted against the same NAS share serves as the dev gateway. Real Marker calls happen in production only. The DSL logic itself can be exercised on ThinkPad with a mock-marker plugin if needed.

End-to-end PDF→Markdown only happens in production. That's accepted; the seam is well-defined enough that production failures will be locatable to one of: ductile DSL bug, Parsem endpoint bug, or Marker bug.

## Consequences

### Positive

- One less moving part inside Parsem (no observer thread, no event loop bridging).
- Symmetry with email/BirdNET pipelines — debugging is one playbook.
- Free retry / DLQ / scheduling at the ductile layer.
- Future input modes (e.g. email-to-parsem) cost zero Parsem code.
- Smaller dependency footprint: `watchdog` can be dropped.

### Negative / risks

- **Hard dependency on a running ductile gateway** — Parsem alone can't ingest. Mitigated: in dev you run ThinkPad ductile or post directly to the endpoints; in prod ductile is always up (it's the integration spine).
- **Bearer token discipline** — if the token leaks, anyone on the network can drive ingest. Mitigated by network policy (Parsem only accessible inside the home network) and by the token being optional + rotatable.
- **Ductile DSL is now part of the contract.** Lives in a separate repo (or wherever ductile pipelines are defined); the two-side-of-a-seam coupling is real but explicit.

## Related

- ADR 0001 — superseded by this ADR for the eventing section; the directory contract and phasing carry over.
- bd `claude-mwx.2` — implementation of this ADR's Parsem side.
- Ductile `folderwatch` and `filewatch` plugins — the other side of the seam.
- Memory `feedback_velocity_over_micro_iteration.md` — informs accepting this pivot mid-cycle rather than deferring it past mwx.2.
