"""Tests for parsem.ingest.layout — the on-disk document-directory
policy. Pure path computation; the traversal guard on `asset_path`
is the only behaviour worth pinning down here (bd claude-5h0)."""

from __future__ import annotations

from pathlib import Path

from parsem.ingest import layout


def test_document_dir_is_originals_slash_doc_id(tmp_path: Path) -> None:
    assert layout.document_dir(tmp_path, 42) == tmp_path / "42"


def test_markdown_and_source_and_sidecar_and_images_paths(tmp_path: Path) -> None:
    assert layout.markdown_path(tmp_path, 7) == tmp_path / "7" / "document.md"
    assert layout.source_path(tmp_path, 7, ".pdf") == tmp_path / "7" / "source.pdf"
    assert layout.extraction_json_path(tmp_path, 7) == tmp_path / "7" / "extraction.json"
    assert layout.images_dir(tmp_path, 7) == tmp_path / "7" / "images"


def test_asset_path_resolves_a_normal_relative_file(tmp_path: Path) -> None:
    resolved = layout.asset_path(tmp_path, 3, "fig.jpeg")
    assert resolved == (tmp_path / "3" / "images" / "fig.jpeg").resolve()


def test_asset_path_allows_nested_subdirs(tmp_path: Path) -> None:
    resolved = layout.asset_path(tmp_path, 3, "sub/nested.png")
    assert resolved == (tmp_path / "3" / "images" / "sub" / "nested.png").resolve()


def test_asset_path_rejects_parent_traversal(tmp_path: Path) -> None:
    # Climbing out of images/ (to source.pdf, the doc dir, or beyond)
    # must be refused.
    assert layout.asset_path(tmp_path, 3, "../source.pdf") is None
    assert layout.asset_path(tmp_path, 3, "../../9/document.md") is None
    assert layout.asset_path(tmp_path, 3, "a/../../b") is None


def test_asset_path_rejects_absolute_and_empty(tmp_path: Path) -> None:
    assert layout.asset_path(tmp_path, 3, "") is None
    assert layout.asset_path(tmp_path, 3, "/etc/passwd") is None
