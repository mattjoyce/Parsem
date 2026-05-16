# ADR 0004: PDF conversion engine — docling + LLM polish

- **Status:** accepted
- **Date:** 2026-05-16
- **Tracking:** bd `claude-fro` (this work); supersedes `claude-08g` (Marker PRD) and resolves `claude-8db` (PDF quality)
- **Supersedes:** ADR 0001 §"Ingest flow" (the Marker dispatch line) and ADR 0002 §"`/ingest/raw-arrived`" (the `submit_to_marker` action). The eventing architecture of ADR 0002 is otherwise unchanged.

## Context

ADR 0001/0002 routed PDFs to a `marker:latest` Docker container: `inbound/raw/` folderwatch → Parsem `/ingest/raw-arrived` returns `submit_to_marker` → ductile spawns a transient marker container → marker writes `<doc_id>.md` to `inbound/converted/` → filewatch → `/ingest/converted-arrived` finishes the doc.

Marker is CPU-bound on the current Unraid GPU budget and its transformer pipeline struggled on dense, table-heavy PDFs (the McKinsey-style documents that surfaced `claude-8db`): broken table extractions, flattened heading hierarchy, detached footnotes. Wall-clock was also poor (single conversions in the hundreds of seconds).

Two changes are made together:

1. **Parser swap:** [IBM docling](https://github.com/DS4SD/docling) replaces Marker. docling has strong layout/table awareness and is much faster.
2. **LLM polish pass:** docling's raw markdown is cleaned by a cloud LLM (Gemini 2.5 Pro by default, Claude optionally) inside the same plugin, scoped narrowly to the three things PDF extractors get wrong.

## Decision

**PDF→Markdown is a two-stage, in-plugin pipeline: docling parse, then LLM polish. The ductile `docling-pdf` plugin owns both stages.**

### Parsem side

The `/ingest/raw-arrived` action vocabulary changes one member: `submit_to_marker` → `submit_to_docling`. Same payload shape (`document_id`, `doc_id`, `source_path`). Same PDF-staging behaviour (converting row, move to `originals/<id>/source.pdf`). `/ingest/converted-arrived` is **unchanged in contract** — it still receives `<doc_id>.md`, relocates the cluster, parses, and records an `extraction_runs` row. Only the sidecar field names it reads change (`marker_version` → `docling_version`, plus the new docling fields). A missing sidecar remains non-fatal.

This is a clean rename, not a dual-action coexistence period. Running docling alongside Marker happens at the **ductile DSL layer** (route the new action to either plugin during cutover), not in Parsem code.

### Plugin side

`docling-pdf` (in `ductile-plugins`, mirrors the `firecrawl` plugin shape — stdin/stdout JSON, atomic write, not the marker docker-spawn shape):

1. docling `DocumentConverter` parses the source PDF → raw markdown.
2. LLM polish over a **versioned prompt** (`polish_prompt_v1.txt`), low temperature, scoped to: tables, heading/structure, footnotes/references.
3. Atomic write `<doc_id>.json` (first) then `<doc_id>.md` (last) — the .md is the filewatch completion signal, identical contract to Marker.

The LLM provider is configurable (`gemini` default, `claude`, or `none` for parse-only). The sidecar records which engine and model ran, the prompt version, page count, and per-stage durations, so every conversion is auditable.

### Polish scope (v1)

In scope: tables, headings & structure, footnotes & references.

**Equations / mathematical notation are explicitly out of scope for v1.** The polish prompt instructs the model to leave math exactly as docling produced it. If equation handling is needed later it gets its own bead and a `polish_prompt_v2`.

## Why this carve-up

- **First-principles, not a bolt-on.** Marker's weakness was structural (CPU transformer pipeline, weak tables). docling fixes the parse; the LLM pass fixes the residual structural damage that *any* extractor leaves. We are not caching around a slow query — we replaced the query.
- **Seam preserved.** The `inbound/converted/*.md` filewatch contract is untouched, so the consumer side (`/ingest/converted-arrived`, cluster relocation, `extraction_runs`) needed only field-name changes. ADR 0002's eventing model stands.
- **Auditable.** Versioned prompt + sidecar provenance (`docling_version`, `llm_model`, `polish_prompt_version`, durations) means a bad conversion can be traced to a specific engine/model/prompt combination.
- **Reversible cutover.** Because Parsem just renames the action, the operator can point `submit_to_docling` at the old marker plugin during migration and flip to `docling-pdf` once a real reading session converts cleanly.

## What changes in code

- `parsem/ingest/arrivals.py` — `RawAction` literal, `_stage_pdf_for_docling` (was `_stage_pdf_for_marker`), `_relocate_converted_cluster` (was `_relocate_marker_cluster`), `extractor_name="docling"`, sidecar reads `docling_version` + the new docling fields.
- `tests/web/test_arrivals.py`, `tests/web/test_ingest.py` — action + sidecar assertions updated.
- `docs/ductile-pipelines/parsem-docling.md` — new plugin contract doc; supersedes the Marker section of `parsem-ingest.md`.
- New ductile plugin `ductile-plugins/docling-pdf/`.

## What does NOT change

- `/ingest/converted-arrived` contract, cluster relocation, image-ref rewrite, `extraction_runs` schema.
- ADR 0002's eventing model: ductile owns all watching; Parsem is a pure receiver.
- The PDF-staging behaviour on `/ingest/raw-arrived` (converting row, `source.pdf`).

## Consequences

### Positive

- Table-heavy PDFs (the `claude-8db` corpus) convert with correct GFM tables and restored structure.
- Much faster wall-clock; docling can use the Unraid RTX 2070.
- Conversion provenance is captured per document.

### Negative / risks

- **LLM cost & latency** in the conversion path. Mitigated: low-temp scoped prompt, configurable model, `none` provider for parse-only, durations logged for review.
- **Polish over-edit risk** — an LLM asked to "fix" can rewrite clean prose. Mitigated by an explicit "leave clean prose byte-for-byte" instruction and out-of-scope guards (esp. equations). Prompt is versioned so regressions are bisectable.
- **Heavier dependency tree** (docling → torch/transformers) on the converter host. Isolated to the plugin; Parsem never imports docling.
- **Migration window** where both engines exist. Bounded by the DSL-layer cutover plan in `claude-fro`; `claude-08g`/`claude-8db` close when cutover completes.

## Related

- ADR 0001, 0002 — Marker decisions superseded as noted; eventing model retained.
- ADR 0003 — unrelated (outbound HTTP); URL ingest still lands via `/ingest/converted-arrived`.
- bd `claude-fro` (this work), `claude-08g` (Marker PRD, superseded), `claude-8db` (PDF quality, resolved by this).
- ductile `docling-pdf` plugin (new); `marker` plugin (retired at cutover).
