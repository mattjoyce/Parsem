"""Shared test constants."""

from __future__ import annotations

from datetime import UTC, datetime

# Anchor timestamp used across domain/store tests for deterministic time math.
# Tests build relative timestamps via T0 + timedelta(...).
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
