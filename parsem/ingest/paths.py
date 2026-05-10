"""Filename + path policy for the inbound pipeline.

Single source for two pieces of policy reused by `POST /ingest`,
`parsem add`, and any future drop surface (e.g. email-to-Parsem):

- `sanitize_filename` — neutralize anything that could escape
  inbound/raw/. Path separators are stripped first; everything outside
  `[A-Za-z0-9._-]` collapses to '_'. Empty input falls back to
  `download` so the inbound dir always gets a name.

- `unique_inbound_path` — resolve a writable path inside an inbound
  directory, sanitizing the candidate name and adding `_2`, `_3`, …
  suffixes on collision so concurrent submissions don't clobber.

Both are pure (no I/O on the sanitize path; only `.exists()` stat
on the collision-resolve path) and trust no caller — the same
function runs whether the name came from a browser file picker, a
URL response's Content-Disposition, or a CLI argument.
"""

from __future__ import annotations

import re
from pathlib import Path

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(filename: str) -> str:
    """Strip path separators, collapse anything outside the safe
    charset to `_`. Returns `download` when the result would be empty
    so callers can always join it with a directory."""
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = _FILENAME_SAFE.sub("_", base).strip("._")
    return cleaned or "download"


def unique_inbound_path(directory: Path, raw_name: str) -> Path:
    """Return a writable path inside `directory`, sanitizing
    `raw_name` and suffixing on collision (`name_2.md`, `name_3.md`
    …). The first non-colliding candidate wins."""
    safe = sanitize_filename(raw_name)
    candidate = directory / safe
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    n = 2
    while True:
        candidate = directory / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1
