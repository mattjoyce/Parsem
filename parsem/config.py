"""Configuration. Spec: ADR docs/adr/0001-nas-ingest-pipeline.md.

Loads a YAML config via `loaden`, expanding ${VAR:-default} references
against the environment. The default file lives at
`~/.config/parsem/config.yaml`; the CLI's `--config` flag overrides.

If the default config doesn't exist on first run, we materialize a
bundled template at the default path so the user has a file to edit.
This is the only side effect on disk; everything else is read-only.

The settings tree:

    paths:
      data:    parsem.db lives under this dir
      library: originals + inbound dirs live under this dir

    server:
      host:    bind address for `parsem serve`
      port:    bind port for `parsem serve`

    ingest:
      url_timeout_seconds: per-URL fetch timeout
      url_max_bytes:       per-URL fetch size cap (bytes)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loaden import get, load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "parsem" / "config.yaml"

# Bundled template — written to DEFAULT_CONFIG_PATH on first run when
# no config file exists. Comments survive the write so the user has a
# reference for the schema.
_DEFAULT_TEMPLATE = """\
# Parsem configuration. ${VAR:-default} expands at load time, so
# environment variables remain a clean override surface for ops.

paths:
  # Where parsem.db lives.
  data: ${PARSEM_DATA_DIR:-./data}
  # Where the document library lives (originals + inbound dirs).
  library: ${PARSEM_LIBRARY_DIR:-./data/library}

server:
  host: ${PARSEM_HOST:-127.0.0.1}
  port: ${PARSEM_PORT:-8000}

ingest:
  url_timeout_seconds: 30
  url_max_bytes: 52428800  # 50 MiB
"""


@dataclass(frozen=True)
class Paths:
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


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int


@dataclass(frozen=True)
class IngestSettings:
    url_timeout_seconds: float
    url_max_bytes: int


@dataclass(frozen=True)
class Settings:
    paths: Paths
    server: ServerSettings
    ingest: IngestSettings


def resolve_config_path(explicit: Path | str | None = None) -> Path:
    """Return the config path the CLI should load. Explicit `--config`
    wins; otherwise the default at `~/.config/parsem/config.yaml`."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    return DEFAULT_CONFIG_PATH


def ensure_default_config(path: Path) -> None:
    """Materialize the bundled template at `path` if no config file is
    there yet. Idempotent; touches nothing when the file already exists."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_DEFAULT_TEMPLATE, encoding="utf-8")


def load_settings(
    config_path: Path | str | None = None,
    *,
    auto_create_default: bool = True,
) -> Settings:
    """Load `Settings` from a YAML file. Resolution order:

    1. Explicit `config_path` argument (CLI's `--config`).
    2. `DEFAULT_CONFIG_PATH` (`~/.config/parsem/config.yaml`).

    On first run with the default path, the bundled template is
    written so the user has a config file to edit. Tests pass an
    explicit path and `auto_create_default=False` to stay isolated.
    """
    resolved = resolve_config_path(config_path)
    if auto_create_default and resolved == DEFAULT_CONFIG_PATH:
        ensure_default_config(resolved)
    if not resolved.exists():
        raise FileNotFoundError(f"Parsem config not found: {resolved}")
    raw: dict[str, Any] = load_config(str(resolved))
    return _settings_from_dict(raw)


def _settings_from_dict(raw: dict[str, Any]) -> Settings:
    """Project the loaden-loaded dict into typed Settings dataclasses.
    Each section has fallbacks so a user editing the YAML can leave
    blocks out and still boot."""
    data_dir = Path(get(raw, "paths.data", "./data")).expanduser().resolve()
    library_dir = Path(
        get(raw, "paths.library", "./data/library")
    ).expanduser().resolve()
    return Settings(
        paths=Paths(data_dir=data_dir, library_dir=library_dir),
        server=ServerSettings(
            host=str(get(raw, "server.host", "127.0.0.1")),
            port=int(get(raw, "server.port", 8000)),
        ),
        ingest=IngestSettings(
            url_timeout_seconds=float(get(raw, "ingest.url_timeout_seconds", 30.0)),
            url_max_bytes=int(get(raw, "ingest.url_max_bytes", 50 * 1024 * 1024)),
        ),
    )


def ensure_library_layout(paths: Paths) -> None:
    """Create the library directory contract if it doesn't exist. Safe
    to call repeatedly; idempotent. Called from FastAPI startup."""
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.library_dir.mkdir(parents=True, exist_ok=True)
    paths.originals_dir.mkdir(parents=True, exist_ok=True)
    paths.inbound_raw_dir.mkdir(parents=True, exist_ok=True)
    paths.inbound_converted_dir.mkdir(parents=True, exist_ok=True)


# Back-compat shim: callers that used the env-var-only `resolve_paths`
# (cycle 1) keep working. New callers should use `load_settings()`.
def resolve_paths(
    *,
    data_dir: Path | str | None = None,
    library_dir: Path | str | None = None,
) -> Paths:
    """Resolve `Paths` from explicit args or env vars (`PARSEM_DATA_DIR`,
    `PARSEM_LIBRARY_DIR`). Predates loaden config; kept for tests that
    only need paths and don't want to set up a YAML file."""
    resolved_data = (
        Path(data_dir)
        if data_dir is not None
        else Path(os.environ.get("PARSEM_DATA_DIR", PROJECT_ROOT / "data"))
    )
    resolved_library = (
        Path(library_dir)
        if library_dir is not None
        else Path(os.environ.get("PARSEM_LIBRARY_DIR", PROJECT_ROOT / "data" / "library"))
    )
    return Paths(data_dir=resolved_data, library_dir=resolved_library)
