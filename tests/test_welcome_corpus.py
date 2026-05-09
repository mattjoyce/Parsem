"""Structural tests for data/welcome.md (Parsem-6kk).

The Phase 1 prototype loads this file from disk; these tests assert it
exercises the chunker shape required by spec §11.3 and produces a
chunk count appropriate for the default 30s paragraph-sized budget
(Parsem-ew8).
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import chunk_via_substrate

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
    """At the new 30s default + list_handling='block' (Parsem-ew8),
    welcome.md packs into ~12-20 paragraph-sized chunks across its 5
    H2 sections, with the bullet list collapsing to one chunk."""
    chunks, _sections = chunk_via_substrate(_text())
    assert 10 <= len(chunks) <= 20, (
        f"expected 10..20 chunks at 30s budget, got {len(chunks)}"
    )
