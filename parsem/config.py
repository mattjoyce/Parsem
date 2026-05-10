"""Path configuration. Spec: ADR docs/adr/0001-nas-ingest-pipeline.md.

Resolves the two on-disk roots from environment variables, with
zero-config defaults that match the dev layout. In prod (Docker on
unRAID) both env vars are set to the bind-mount targets:

    PARSEM_DATA_DIR    = /mnt/user/appdata/parsem      (parsem.db)
    PARSEM_LIBRARY_DIR = /mnt/user/Library/parsem-library  (originals + inbound)

Pure functions — no side effects. Directory creation is the caller's
job (FastAPI startup uses `ensure_library_layout`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
_DEFAULT_LIBRARY_DIR = _DEFAULT_DATA_DIR / "library"


@dataclass(frozen=True)
class Paths:
    """Resolved on-disk paths for one running Parsem instance."""

    data_dir: Path
    library_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "parsem.db"

    @property
    def originals_dir(self) -> Path:
        return self.library_dir / "originals"

    @property
    def inbound_raw_dir(self) -> Path:
        return self.library_dir / "inbound" / "raw"

    @property
    def inbound_converted_dir(self) -> Path:
        return self.library_dir / "inbound" / "converted"


def resolve_paths(
    *,
    data_dir: Path | str | None = None,
    library_dir: Path | str | None = None,
) -> Paths:
    """Resolve paths with explicit args > env vars > defaults.

    Tests pass explicit args for isolation; the CLI reads env vars."""
    resolved_data = (
        Path(data_dir)
        if data_dir is not None
        else Path(os.environ.get("PARSEM_DATA_DIR", _DEFAULT_DATA_DIR))
    )
    resolved_library = (
        Path(library_dir)
        if library_dir is not None
        else Path(os.environ.get("PARSEM_LIBRARY_DIR", _DEFAULT_LIBRARY_DIR))
    )
    return Paths(data_dir=resolved_data, library_dir=resolved_library)


def ensure_library_layout(paths: Paths) -> None:
    """Create the library directory contract if it doesn't exist. Safe
    to call repeatedly; idempotent. Called from FastAPI startup."""
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.library_dir.mkdir(parents=True, exist_ok=True)
    paths.originals_dir.mkdir(parents=True, exist_ok=True)
    paths.inbound_raw_dir.mkdir(parents=True, exist_ok=True)
    paths.inbound_converted_dir.mkdir(parents=True, exist_ok=True)
