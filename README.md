<p align="center">
  <img src="docs/images/parsem-logo-a4.svg" alt="Parsem" width="120">
</p>

# Parsem

Self-hosted, local-first deep-reading app for Markdown documents. Progressive Reveal Reading turns a document into a paced sequence of chunks — you pull each one in deliberately, the reader keeps the settled prose above so you can scroll back, and a per-document reading economy tracks where you've been.

The point isn't speed. It's attention. Reading happens at the rate you grant tokens; the document grows beneath your eye instead of falling away above it.

## Status

Phase 1 — reading mechanic + ingest pipeline. See `bd ready` for the live work queue, or browse `docs/adr/` for architectural decisions. Active work is captured in [beads](https://github.com/SteveGT96/beads) issues prefixed `claude-axx*` (chunking substrate + cursor engine epic).

## Quick start

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# First boot writes a default config to ~/.config/parsem/config.yaml
uv run parsem serve
# -> http://127.0.0.1:8000/
```

The CLI also supports:

```bash
uv run parsem add <file.md>          # drop into inbound/raw/ for ingest
uv run parsem add https://...        # URL → markdown via the firecrawl ductile plugin
uv run parsem rechunk <id>           # re-run the chunker on a stored doc
uv run parsem rechunk --all          # re-chunk everything (e.g. after a strategy change)
```

## How it reads

A document is parsed into **atomic pieces** (sentences, list runs, code blocks, images, …), then composed into **chunks** by a named, versioned **chunking strategy**. The default strategy is `current_reading_time`: it packs prose into ~30-second reading budgets, keeps lists and code blocks intact, and never leaves a heading orphaned from its content.

The reader reveals one chunk at a time. A small **bucket** (the dots in the top bar) regenerates over time and gates reveal — you can't gulp a long document; you have to come back to it. Already-revealed chunks stay visible above the current one so you can scroll back, search, copy, or rate any chunk you've passed.

Pins (colour-coded) and per-chunk effort ratings give a lightweight margin annotation that survives re-chunking via piece-set re-anchoring.

## Ingest paths

| Source | Path |
|---|---|
| Direct upload | Library page → "Add file" |
| URL | Library page → "Add URL" → Parsem forwards to a [firecrawl](https://firecrawl.dev) plugin on the [ductile](https://github.com/mattjoyce/ductile) gateway; markdown lands in `inbound/converted/` |
| PDF | drop into `inbound/raw/` → folder-watch routes to a docling + LLM-polish plugin on ductile → markdown lands in `inbound/converted/` |
| NAS folder | folder-watch on `inbound/raw/` knocks Parsem to ingest each arrival (ADR 0002 — Parsem is a pure receiver) |

The ingest seam is deliberately one-way: Parsem doesn't fetch; ductile (or you) puts files in front of it.

## Architecture

```
text  →  DocumentRevision  →  ParsedBlock[]  →  AtomicPiece[]
     →  PreprocessedPiece[]  →  ChunkPlan  →  Chunk[]  →  Section[]
     →  persist (revision, pieces, run, chunks, sections)
```

Two layers worth knowing:

- **Atomic chunking substrate** (`parsem/domain/chunking/`, `parsem/domain/atomic.py`, `parsem/domain/preprocessed.py`) — immutable, source-faithful units with offsets, line spans, hashes. Same input always produces the same output; chunks materialise from the revision's source text by slicing.
- **Cursor engine** (`parsem/domain/chunking/cursor.py`) — single-pass walker that consults priority-ordered **rule values** (frozen dataclasses) and emits a `ChunkPlan`. Strategies are hashable tuples of rules; pluggable **annotators** decorate pieces with per-piece values (e.g. semantic-density scores) that rules consult via a `requires`/`produces` contract validated at boot.

Reader, storage, and routes live under `parsem/web/`, `parsem/store/`, and `parsem/web/routes/`.

## Stack

- **Python 3.11+** (developed on 3.14)
- **FastAPI** + **uvicorn** for the web layer
- **SQLite** for storage (`data/parsem.db`)
- **pysbd** for sentence segmentation, **markdown-it-py** for parse
- **[loaden](https://github.com/mattjoyce/loaden)** for YAML config with env-var expansion
- **uv** for env + package management
- **ruff** + **mypy** + **pytest**
- **[ductile](https://github.com/mattjoyce/ductile)** as the integration gateway for URL/PDF ingest plugins (separate repo)

## Development

```bash
uv run pytest              # 600+ tests, ~6s
uv run ruff check .
uv run mypy parsem
```

### Conventions

- **Issues**: tracked in [beads](https://github.com/SteveGT96/beads). Run `bd ready` to find work; `bd show <id>` for details. New work files a bead before code lands.
- **Architectural decisions**: `docs/adr/` (Architecture Decision Records). Anything that meaningfully constrains future work earns an ADR.
- **Spec**: `parsem-spec.md` is the long-form source of truth for the reader mechanic and substrate invariants. `AtomicChunkingPhase1.md` covers the chunking pipeline.
- **Coding standards**: `Agent Coding Standards - Python.md` is the conventions reference.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).
