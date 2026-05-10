# ADR 0001: NAS-backed ingest pipeline with Marker conversion

- **Status:** accepted
- **Date:** 2026-05-10
- **Tracking:** bd `claude-mwx` (epic) + `.1` `.2` `.3` (cycles)

## Context

Parsem needs to ingest both Markdown and PDF documents from URLs, file uploads, and manual file drops. PDFs require conversion via Marker (a separate ML-heavy service), which runs slowly enough (30s–5min per doc) that a synchronous user-facing call is not viable. The library is moving from a single-developer local-disk setup to a NAS-backed home server (unRAID + Docker), and Marker is being deployed as a separate Ductile-orchestrated service on the same NAS.

## Decision

### Storage topology — two NAS paths

- **Parsem runs in a slim Docker container on unRAID.** Application code is the container; data is bind-mounted from two NAS paths:
  - **`/mnt/user/appdata/parsem/`** — app state. Holds `parsem.db` and any config. Standard unRAID appdata convention.
  - **`/mnt/user/Library/parsem-library/`** — content library. Holds originals + inbound dirs. The "Library" share is also mounted by Marker (see `claude-08g` PRD), giving both services a shared view of the corpus.
- **Dev paths** are env-driven: `./data/` for app state, `./data/library/` for content (one variable per path, two values each).

### Directory contract

```
/mnt/user/appdata/parsem/
└── parsem.db                     # SQLite, single source of state

/mnt/user/Library/parsem-library/
├── inbound/
│   ├── raw/                      # drop zone — web upload, manual drop, URL fetch
│   └── converted/                # Marker writes here
└── originals/                    # canonical, post-ingest store
    ├── <doc_id>.md               # always present
    └── <doc_id>.pdf              # present if originally a PDF (cycle 3)
```

### Ingest flow

Three entry points converge on `inbound/raw/`:

1. **Web form** — a library landing page with drop-zone (file) + URL field. Submit → `POST /ingest` → server fetches URL if needed, writes to `inbound/raw/<hash>.{md,pdf}` and returns 202.
2. **Manual NAS drop** — user drops files directly into `inbound/raw/` from any machine that mounts the NAS share. No UI required.
3. **CLI** — `parsem add <url|file>` is the same code path as the web form (or writes directly to `inbound/raw/`).

A filesystem-watcher on `inbound/raw/` is the unifying mechanism. On detection:
- `.md` → ingest in place, then move to `originals/<doc_id>.md`.
- `.pdf` → call the `ductile-marker` plugin (HTTP) with the input path; the plugin spawns a transient `docker run --rm --gpus all marker:latest …` per the `claude-08g` PRD; await output in `inbound/converted/`.

### Marker trigger — monitor only

Parsem learns Marker has finished by watching `inbound/converted/`:

- **Filesystem-watcher** on `inbound/converted/`. Marker writes the converted file atomically; the watcher catches the new file and ingests it.
- **No callback URL.** Marker is a transient `--rm` container with no persistent identity to call from; the Ductile plugin response is just the job state. The watcher IS the trigger; it also catches manual drops and Marker upgrades that bypass the plugin.

Hash-keyed dedup guards against double-ingest (e.g. user re-drops the same file).

(An earlier draft of this ADR proposed both callback and monitor. Reconciling with the `claude-08g` PRD: Marker's transient-container model makes a stable callback endpoint awkward; monitor-only is sufficient and matches the existing design.)

### Why these choices

- **NAS as filesystem (not S3 / object store):** zero protocol surface change, code stays the same as today. SQLite-on-NAS works with WAL on unRAID; tested in similar Ductile services.
- **Two NAS paths (appdata + Library) instead of one:** mirrors unRAID's standard split between service config (`appdata`) and shared content (`Library`). Marker only needs `Library`; Parsem mounts both. Cleaner permissions if a third service ever needs the corpus too.
- **Monitor-only Marker trigger:** the existing PRD's transient-container model rules out a clean callback endpoint. Monitor is the simplest contract and naturally absorbs manual drops + Marker upgrades.
- **Both web form + NAS drop:** the form is essential for URLs (you can't drag a URL into a folder); the NAS drop folder is convenient for files you already have on disk. Same pipeline either way.
- **Async only:** Marker is too slow (30s–3min per PDF, per `claude-08g`) for a sync request. Library shows a "converting…" placeholder; the flip-to-ready happens via the same poll/swap pattern the reader already uses.

### Phasing

Three independently-shippable cycles tracked under bd `claude-mwx`:

1. **Cycle 1 (`.1`)** — directory contract, env config, filesystem-watcher, library landing page, web form, URL fetch. End state: drop a `.md` anywhere → it appears in the library. Marker is not involved yet.
2. **Cycle 2 (`.2`)** — PDF detection, HTTP call to Marker, dual trigger, library "converting…" placeholder. Idempotency via hash-keyed dedup.
3. **Cycle 3 (`.3`)** — provenance (keep original PDF), re-ingest button, error UX for failed conversions.

### Deploy

- **Source of truth:** `github.com/mattjoyce/Parsem` (private).
- **Dev → prod path:** push from Mac → `git clone`/`pull` on `/Volumes/Projects/Parsem/` (Mac mount of an unRAID share) → `rsync` into the Parsem container's bind mount.
- **Image build:** Dockerfile to be added in cycle 1 (slim Python base + `uv` for deps). Container is stateless; only the bind mount carries data.

## Consequences

### Positive

- Three input modes from one pipeline. New input methods (e.g. email-to-Parsem via Ductile) plug in by writing to `inbound/raw/`.
- Stateless container is safe to restart, redeploy, or scale; bind mount is the only contract.
- Marker can fail or be down without Parsem breaking — `inbound/raw/` just queues up, and the monitor will catch up when Marker recovers.
- `originals/<doc_id>.pdf` (cycle 3) preserves the source so a Marker upgrade lets us re-convert without re-fetching.

### Negative / risks

- Filesystem-watcher on a network share has known edge cases (event coalescing, missed events under load). Mitigated by the second watcher on `inbound/converted/` plus a periodic full-directory sweep on startup.
- SQLite on NAS works on unRAID for low-write loads but is not officially recommended for concurrent writers. Single Parsem container is fine; multi-instance would need PostgreSQL.
- Hash-keyed dedup means renaming a file to re-ingest doesn't work; explicit re-ingest endpoint covers this in cycle 3.

## Related

- bd: `claude-08g` (Marker PRD), `claude-d1s` (Marker container + Library share), `claude-m49` (ductile-marker plugin scaffold) — pre-existing Marker work that this pipeline integrates with.
- Memory: `feedback_velocity_over_micro_iteration.md` — informs the three-cycle phasing rather than four.
