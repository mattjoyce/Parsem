"""Structural tests for data/welcome.md (Parsem-6kk).

The Phase 1 prototype loads this file from disk; these tests assert it
exercises the chunker shape required by spec §11.3 and produces a chunk
count in the targeted ~30-chunk range at the default 10s budget.
"""

from __future__ import annotations

import re
from pathlib import Path

from parsem.domain.chunking import ChunkingConfig, chunk
from parsem.parse.markdown_parse import parse

WELCOME = Path(__file__).resolve().parent.parent / "data" / "welcome.md"


def _text() -> str:
    return WELCOME.read_text(encoding="utf-8")


def test_welcome_has_at_least_four_h2_sections() -> None:
    h2_lines = [ln for ln in _text().splitlines() if ln.startswith("## ")]
    assert len(h2_lines) >= 4, f"expected ≥4 H2 headings, found {len(h2_lines)}"


def test_welcome_contains_one_fenced_code_block() -> None:
    fences = re.findall(r"^```", _text(), flags=re.MULTILINE)
    assert len(fences) >= 2 and len(fences) % 2 == 0, "need a closed fenced code block"


def test_welcome_contains_one_bulleted_list() -> None:
    list_lines = [ln for ln in _text().splitlines() if re.match(r"^[-*+] ", ln)]
    assert len(list_lines) >= 2, "need at least two list items"


def test_welcome_contains_one_blockquote() -> None:
    quote_lines = [ln for ln in _text().splitlines() if ln.startswith("> ")]
    assert quote_lines, "need at least one blockquote line"


def test_welcome_paragraph_contains_an_abbreviation() -> None:
    # Spec §11.5 — pysbd handles abbreviations; the corpus should exercise them.
    abbreviations = ("Dr.", "Mr.", "Mrs.", "Ms.", "e.g.", "i.e.", "etc.")
    assert any(a in _text() for a in abbreviations), "no test-worthy abbreviation found"


def test_welcome_chunks_to_target_count_at_default_budget() -> None:
    blocks = parse(_text())
    output = chunk(blocks, ChunkingConfig())
    assert 25 <= len(output.chunks) <= 40, (
        f"expected 25..40 chunks at 10s budget, got {len(output.chunks)}"
    )
