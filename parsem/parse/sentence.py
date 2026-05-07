"""Sentence boundary detection. Spec: parsem-spec.md §11.5.

Pure-Python wrapper around pysbd. Returns sentence spans with character
offsets so the chunker can fill chunks with whole sentences without
splitting them. Pysbd handles common abbreviations (e.g., "Dr. Smith")
out of the box for English.
"""

from __future__ import annotations

from dataclasses import dataclass

import pysbd


@dataclass(frozen=True)
class Sentence:
    """A sentence with its character offsets into the source text."""

    text: str
    char_start: int
    char_end: int


_SEGMENTER = pysbd.Segmenter(language="en", clean=False, char_span=True)


def split_sentences(text: str) -> list[Sentence]:
    """Split text into sentences with character-offset spans."""
    if not text:
        return []
    return [
        Sentence(text=span.sent, char_start=span.start, char_end=span.end)
        for span in _SEGMENTER.segment(text)
    ]
