"""Tests for parsem.parse.sentence. Spec: parsem-spec.md §11.5."""

from __future__ import annotations

from itertools import pairwise

from parsem.parse.sentence import split_sentences


def test_empty_string_returns_empty_list() -> None:
    assert split_sentences("") == []


def test_single_sentence_returns_one_span_covering_input() -> None:
    text = "This is a single sentence."
    result = split_sentences(text)
    assert len(result) == 1
    assert result[0].char_start == 0
    assert result[0].char_end == len(text)
    assert text[result[0].char_start : result[0].char_end] == result[0].text


def test_multiple_sentences_split_at_punctuation_boundaries() -> None:
    text = "First sentence. Second sentence! Third sentence?"
    result = split_sentences(text)
    assert len(result) == 3


def test_abbreviation_does_not_split_sentence() -> None:
    text = "Dr. Smith said hello. This is the second sentence."
    result = split_sentences(text)
    assert len(result) == 2
    assert "Dr. Smith" in result[0].text


def test_offsets_let_caller_reconstruct_each_sentence() -> None:
    text = "First. Second. Third."
    result = split_sentences(text)
    for sentence in result:
        assert text[sentence.char_start : sentence.char_end] == sentence.text


def test_offsets_are_non_overlapping_and_in_order() -> None:
    text = "Alpha sentence. Beta sentence. Gamma sentence."
    result = split_sentences(text)
    for previous, current in pairwise(result):
        assert previous.char_end <= current.char_start
