# ADR 0003: User-initiated outbound HTTP from Parsem

- **Status:** accepted
- **Date:** 2026-05-14
- **Tracking:** bd `claude-5fp` (Firecrawl URL ingest — first concrete case)
- **Amends:** ADR 0002 §"Decision" — the "opens no outbound HTTP clients" clause

## Context

ADR 0002 declared that Parsem "owns no filesystem watchers, opens no outbound HTTP clients, and runs no background threads for I/O." The motivation was specific and correct: keep eventing at the integration layer (ductile), keep Parsem a pure request/response service, eliminate the complexity of watchers / clocks / supervisors inside Parsem's process.

`claude-5fp` introduces URL ingest via a Firecrawl ductile plugin. The natural shape is symmetric with Marker — converter writes markdown to `inbound/converted/`, existing filewatch picks it up, `/ingest/converted-arrived` finishes the job. But the *trigger* differs: a URL arrives via Parsem's web form, not via a filesystem event. Three carve-ups were considered:

1. **Trigger file in a watched folder** — Parsem writes a tiny `.url` file into `inbound/raw/` (or a new `inbound/urls/`). Folderwatch fires; ductile reads the URL and calls the firecrawl plugin. Strict ADR 0002 compliance — Parsem makes zero outbound calls. But it turns RPC into filesystem-as-event for a payload too small to need on-disk staging, and the latency / failure modes are worse.

2. **Web UI bypasses Parsem on submit** — the URL submit form POSTs directly to ductile's webhook. Parsem only sees the resulting markdown via filewatch. Strict ADR 0002 compliance — but Parsem's library page can't show a "converting" placeholder because no row exists at submit time, and the coupling moves to the browser.

3. **Parsem makes one synchronous outbound POST to ductile inside the user's request handler.** Simplest UX, smallest code, doc row created at the right moment. Requires a small, principled amendment to ADR 0002.

Option 3 is what this ADR codifies.

## Decision

**Parsem may make outbound HTTP if and only if the call is within the lifecycle of a user-initiated request handler.**

### Permitted

- ✅ HTTP POST to ductile from inside a `POST /ingest/url` handler, synchronously, completing before the response returns to the user.
- ✅ Any future endpoint where the user's action implies an outbound call (e.g. "share this doc to X"), bounded by that request.

### Forbidden

- ❌ Outbound HTTP from any watcher, timer, background thread, FastAPI lifespan hook, asyncio task scheduled outside the request lifecycle, or any periodic / conditional process.
- ❌ Outbound HTTP retries or queues that outlive the request — if the call fails, return the error to the user. Do not silently queue for later.
- ❌ Connection pools or long-lived clients held at module / application scope. Outbound clients are instantiated per request and closed before the response returns.

### The structural test

The rule is structural, not numerical. One outbound call per user request is fine. Many is fine. Zero is fine. What matters is the call's *lifecycle*: **it must be bounded by the user request that triggered it.** If you can imagine the call happening when no user is waiting on a response, the call doesn't belong in Parsem.

## Why this carve-up

- **Spirit-preserving.** The reason ADR 0002 forbade outbound HTTP was to keep Parsem free of autonomous I/O — clocks, watchers, retries, observers, supervisors. None of that applies to a synchronous handler making one downstream call inside a user's request.
- **Symmetric model.** Parsem is now equally a "service" for human users (via its web routes) and a "service" for ductile (via `/ingest/*-arrived`). Either side may originate work; both flows resolve inside their respective request lifecycles. Nothing is "always on" inside Parsem.
- **Operationally safe.** No background queue to babysit, no dangling client state. If ductile is down, the user's request fails synchronously with a clear error — same UX as any other backend dependency.
- **Honest amendment.** ADR 0002's "opens no outbound HTTP clients" is amended to "opens no *autonomous* outbound HTTP clients." The autonomy adjective is doing the work, and naming it makes the rule clearer than it was before.

## What changes in code

- `parsem/config.py` — add `ductile.base_url` and `ductile.api_token` config keys. Both required when outbound-dependent endpoints (URL ingest) are enabled.
- A small outbound helper (probably `parsem/ingest/ductile_client.py`) — instantiates `httpx.Client` per request, configured with the ductile base URL and bearer auth, with a tight connect timeout. Closed before the response returns.
- `parsem/web/routes/ingest.py` — `POST /ingest/url` uses the helper to submit to ductile's firecrawl plugin.

## What does NOT change

- `/ingest/raw-arrived` and `/ingest/converted-arrived` remain pure receivers.
- All filesystem watching stays on the ductile side.
- No new daemons, no startup-launched processes, no lifespan-scoped clients.
- The Marker flow (PDF via `inbound/raw/` → folderwatch) is unaffected.

## Consequences

### Positive

- URL ingest lands cleanly with a clear architectural rule behind it instead of a hand-wave.
- Future user-initiated outbound features have an obvious home pattern.
- The mental model is honest: Parsem is request/response on both sides.

### Negative / risks

- **Slippery-slope risk.** "User-initiated" is a meaningful adjective — once accepted, future contributors may rationalise more permissive interpretations. Mitigation: re-anchor on the lifecycle test. If the call can fire when no user is waiting on a response, it's not user-initiated.
- **Synchronous failure mode.** If ductile is unreachable, URL ingest fails synchronously with a 502 to the user. We explicitly do not provide a background retry — that would smuggle in the autonomous-outbound behaviour this ADR forbids. The escape valve, if ever needed, is option 1 from the Context (trigger file in a watched dir).
- **One more dependency in the critical path of URL submission.** Mitigated by the fact that ductile is the gateway anyway — if it's down, Marker is also broken, and the operator already knows.

## Related

- ADR 0002 — amended in the narrow respect described above; its filesystem-watching and pure-receiver-for-content properties remain in force.
- bd `claude-5fp` — implements this ADR's first concrete case (Firecrawl URL ingest).
- ductile `firecrawl` plugin (new) — the destination of the outbound call.
