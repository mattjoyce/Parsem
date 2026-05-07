# Parsem

Self-hosted, local-first deep-reading app for Markdown documents. Progressive Reveal Reading with a per-document reading economy and colour-coded pins.

See [`parsem-spec.md`](parsem-spec.md) for the full specification and [`Agent Coding Standards - Python.md`](Agent%20Coding%20Standards%20-%20Python.md) for code conventions.

## Status

Phase 1 — reading mechanic prototype. Track work via `bd ready`.

## Markdown only

Parsem ingests `.md` files only. PDF or other formats must be converted first:

```bash
pandoc input.pdf -o output.md
```

## Development

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
ruff check .
```
