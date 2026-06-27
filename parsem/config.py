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
      callback_token:      bearer token required by the ductile-driven
                           /ingest/raw-arrived and /ingest/converted-arrived
                           endpoints (ADR 0002). Empty = permissive (dev).

    presentation:
      theme/density/width/font_size: the reader's no-localStorage defaults
                           (per-browser overrides live client-side; spec
                           §15.3 — "server has nothing to know"). These are
                           the fallback the no-FOUC bootstrap interpolates.
      fonts:               the prose-font picker — a list of {label, stack}
                           entries; the first is the default. CSS/system
                           stacks only in v1 (bundled webfonts are a
                           follow-up). claude-rdk.
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
  # Where reader notes are exported (one markdown file per document).
  # Point this at an Obsidian vault folder to have notes land there.
  notes: ${PARSEM_NOTES_DIR:-./data/library/notes}

server:
  host: ${PARSEM_HOST:-127.0.0.1}
  port: ${PARSEM_PORT:-8000}

ingest:
  url_timeout_seconds: 30
  url_max_bytes: 52428800  # 50 MiB
  # Bearer token required on /ingest/raw-arrived and
  # /ingest/converted-arrived (the ductile callbacks). Empty value
  # means accept any caller — useful in dev; set in prod.
  callback_token: ${PARSEM_INGEST_TOKEN:-}

ductile:
  # Outbound calls to the ductile gateway (ADR 0003, user-initiated only).
  # Used by /ingest/url to submit URL scrape jobs to the firecrawl plugin
  # (bd claude-5fp). Empty base_url disables URL ingest with a clear error.
  base_url: ${PARSEM_DUCTILE_BASE_URL:-}
  api_token: ${PARSEM_DUCTILE_TOKEN:-}

chunking:
  # Process-wide chunking strategy. Resolved against the registry in
  # parsem.domain.chunking; unknown names fall back to the registry
  # default rather than failing boot. Dev-only knob today — no UI.
  default_strategy: ${PARSEM_CHUNKING_STRATEGY:-current_reading_time}

presentation:
  # The reader's appearance. theme/density/width/size are also
  # overridable per-browser via the in-reader "Aa" / "," panel (stored
  # in localStorage); these values are the no-localStorage fallback.
  theme: paper            # paper | sepia | dark
  density: normal         # compact (1.45) | normal (1.6) | spacious (1.85)
  width: normal           # narrow (640px) | normal (760px) | wide (920px)
  font_size: 18           # 14-24 px
  # Prose-font picker. First entry is the default. `stack` is a full CSS
  # font-family value — v1 ships system/CSS stacks only (bundled
  # webfonts are a follow-up). Add a font = add a line here.
  fonts:
    - label: Charter
      stack: 'Charter, Georgia, "Times New Roman", serif'
    - label: Georgia
      stack: 'Georgia, "Times New Roman", serif'
    - label: Iowan
      stack: '"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif'
    - label: System Sans
      stack: 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
    - label: Helvetica
      stack: '"Helvetica Neue", Helvetica, Arial, sans-serif'
"""


@dataclass(frozen=True)
class Paths:
    data_dir: Path
    library_dir: Path
    # Where reader notes are exported (one markdown file per document).
    # None means "derive from library_dir" (the default below); set it
    # via `paths.notes` in the config to point at, e.g., an Obsidian
    # vault folder. Kept as an override field so existing two-arg Paths
    # construction (resolve_paths, tests) stays valid.
    notes_override: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "parsem.db"

    @property
    def notes_dir(self) -> Path:
        """Resolved notes-export directory: the configured override, or
        `library/notes` when unset."""
        if self.notes_override is not None:
            return self.notes_override
        return self.library_dir / "notes"

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
    callback_token: str  # empty string = no auth (dev); set = require match


@dataclass(frozen=True)
class DuctileSettings:
    """Outbound ductile gateway settings for user-initiated URL submission
    (ADR 0003, bd claude-5fp). Empty `base_url` disables URL ingest with
    a clear error rather than silently dropping submissions."""

    base_url: str  # e.g. "http://localhost:8888"; empty = disabled
    api_token: str  # bearer for the outbound call; empty = no auth header


@dataclass(frozen=True)
class ChunkingSettings:
    """Process-wide chunking knobs (claude-axx.9). `default_strategy`
    names an entry in `parsem.domain.chunking.STRATEGIES`; the value is
    applied to the chunking module's `DEFAULT_STRATEGY_NAME` at boot.
    Unknown names fall back to the shipped default so a typo in YAML
    doesn't break ingest."""

    default_strategy: str


@dataclass(frozen=True)
class FontOption:
    """One entry in the reader's prose-font picker (spec §15.3, claude-rdk).
    `stack` is a full CSS font-family value; the panel stores it verbatim
    in localStorage and the bootstrap sets it as the inline `--prose-font`."""

    label: str
    stack: str


# Allowed values for the appearance axes — match the CSS [data-*]
# blocks in reader.css. An out-of-set value in the YAML falls back to
# the default rather than rendering an unstyled page.
_THEMES: tuple[str, ...] = ("paper", "sepia", "dark")
_DENSITIES: tuple[str, ...] = ("compact", "normal", "spacious")
_WIDTHS: tuple[str, ...] = ("narrow", "normal", "wide")
_FONT_SIZE_MIN, _FONT_SIZE_MAX = 14, 24

# Shipped default prose fonts — CSS/system stacks only (no bundled
# webfonts in v1). First entry is the default; mirrors the YAML template.
_DEFAULT_FONTS: tuple[FontOption, ...] = (
    FontOption("Charter", 'Charter, Georgia, "Times New Roman", serif'),
    FontOption("Georgia", 'Georgia, "Times New Roman", serif'),
    FontOption(
        "Iowan", '"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif'
    ),
    FontOption(
        "System Sans",
        'system-ui, -apple-system, "Segoe UI", Roboto, '
        '"Helvetica Neue", Arial, sans-serif',
    ),
    FontOption("Helvetica", '"Helvetica Neue", Helvetica, Arial, sans-serif'),
)


@dataclass(frozen=True)
class PresentationSettings:
    """The reader's no-localStorage appearance defaults (spec §15.3,
    claude-rdk). Per-browser overrides live client-side; these values are
    what the no-FOUC bootstrap interpolates as the empty-localStorage
    fallback and what the "Aa" panel renders its font picker from."""

    theme: str  # paper | sepia | dark
    density: str  # compact | normal | spacious
    width: str  # narrow | normal | wide
    font_size: int  # 14..24
    fonts: tuple[FontOption, ...]

    @classmethod
    def default(cls) -> PresentationSettings:
        return cls(
            theme="paper",
            density="normal",
            width="normal",
            font_size=18,
            fonts=_DEFAULT_FONTS,
        )

    @property
    def default_font_stack(self) -> str:
        """The first picker entry's stack — the prose font used when
        localStorage carries no `fontStack`. The no-FOUC bootstrap
        interpolates it via Jinja's `tojson` (HTML/script-safe)."""
        return self.fonts[0].stack if self.fonts else _DEFAULT_FONTS[0].stack


@dataclass(frozen=True)
class Settings:
    paths: Paths
    server: ServerSettings
    ingest: IngestSettings
    presentation: PresentationSettings
    ductile: DuctileSettings
    chunking: ChunkingSettings


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
    notes_raw = get(raw, "paths.notes", None)
    notes_override = (
        Path(notes_raw).expanduser().resolve() if notes_raw else None
    )
    return Settings(
        paths=Paths(
            data_dir=data_dir,
            library_dir=library_dir,
            notes_override=notes_override,
        ),
        server=ServerSettings(
            host=str(get(raw, "server.host", "127.0.0.1")),
            port=int(get(raw, "server.port", 8000)),
        ),
        ingest=IngestSettings(
            url_timeout_seconds=float(get(raw, "ingest.url_timeout_seconds", 30.0)),
            url_max_bytes=int(get(raw, "ingest.url_max_bytes", 50 * 1024 * 1024)),
            callback_token=str(get(raw, "ingest.callback_token", "") or ""),
        ),
        presentation=_presentation_from_dict(raw),
        ductile=DuctileSettings(
            base_url=str(get(raw, "ductile.base_url", "") or ""),
            api_token=str(get(raw, "ductile.api_token", "") or ""),
        ),
        chunking=ChunkingSettings(
            default_strategy=str(
                get(raw, "chunking.default_strategy", "current_reading_time")
                or "current_reading_time"
            ),
        ),
    )


def _one_of(value: object, allowed: tuple[str, ...], default: str) -> str:
    """`str(value)` if it's in `allowed`, else `default` — so a typo in
    the YAML degrades to the default rather than an unstyled page."""
    v = str(value)
    return v if v in allowed else default


def _presentation_from_dict(raw: dict[str, Any]) -> PresentationSettings:
    """Project the `presentation:` block into PresentationSettings.
    Tolerant of an absent block or a malformed `fonts:` list — falls back
    to the shipped defaults entry-by-entry so a half-edited YAML still
    boots; out-of-range axis values clamp/fall back too."""
    fonts_raw = get(raw, "presentation.fonts", None)
    fonts: tuple[FontOption, ...] = _DEFAULT_FONTS
    if isinstance(fonts_raw, list):
        parsed = [
            FontOption(str(entry["label"]), str(entry["stack"]))
            for entry in fonts_raw
            if isinstance(entry, dict) and entry.get("label") and entry.get("stack")
        ]
        if parsed:
            fonts = tuple(parsed)
    font_size = int(get(raw, "presentation.font_size", 18))
    return PresentationSettings(
        theme=_one_of(get(raw, "presentation.theme", "paper"), _THEMES, "paper"),
        density=_one_of(get(raw, "presentation.density", "normal"), _DENSITIES, "normal"),
        width=_one_of(get(raw, "presentation.width", "normal"), _WIDTHS, "normal"),
        font_size=max(_FONT_SIZE_MIN, min(_FONT_SIZE_MAX, font_size)),
        fonts=fonts,
    )


def ensure_library_layout(paths: Paths) -> None:
    """Create the library directory contract if it doesn't exist. Safe
    to call repeatedly; idempotent. Called from FastAPI startup."""
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.library_dir.mkdir(parents=True, exist_ok=True)
    paths.originals_dir.mkdir(parents=True, exist_ok=True)
    paths.inbound_raw_dir.mkdir(parents=True, exist_ok=True)
    paths.inbound_converted_dir.mkdir(parents=True, exist_ok=True)
    paths.notes_dir.mkdir(parents=True, exist_ok=True)


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
