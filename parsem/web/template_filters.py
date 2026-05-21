"""Custom Jinja filters for the web layer. ADR 0005, bd Parsem-7wu.2.

Small, presentation-only transforms registered on the templates env in
app startup. Pure functions — no DB, no I/O. Tested independently of
the template renderer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.templating import Jinja2Templates

# Threshold for switching between relative and absolute date display
# in the tile slug. ~30 days matches "is this fresh content or have I
# sat on it for a while?" — see ADR 0005, Q9.
_RELATIVE_THRESHOLD_DAYS = 30


def relative_date(then: datetime | None, now: datetime | None = None) -> str:
    """Render a date for the library tile slug. ADR 0005, Q9.

    Recent (within ~30 days) → relative phrasing ("today", "3d ago",
    "2w ago"). Older → absolute ISO date ("2026-04-21"). None → empty
    string, so the template can render it unconditionally.

    `now` defaults to UTC-now; the parameter exists so tests can pin
    time. Both `then` and `now` are normalised to UTC for the
    comparison.
    """
    if then is None:
        return ""
    if now is None:
        now = datetime.now(UTC)
    # Normalise both ends to UTC so naive vs aware datetimes compare
    # cleanly. Naive inputs are assumed UTC (consistent with the rest
    # of the store layer).
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    delta = now - then
    if delta < timedelta(0):
        # Future date — render absolute. Shouldn't happen for ingest
        # dates but a defensive fallback.
        return then.date().isoformat()
    if delta < timedelta(hours=24):
        return "today"
    if delta < timedelta(days=_RELATIVE_THRESHOLD_DAYS):
        days = delta.days
        if days < 7:
            return f"{days}d ago"
        weeks = days // 7
        return f"{weeks}w ago"
    return then.date().isoformat()


def source_label(source_type: str, source_domain: str | None) -> str:
    """Render the source bit of the tile slug. ADR 0005, Q9.

    URL ingest → domain (e.g. "stratechery.com"). Markdown/PDF →
    typographic badge ("MD" / "PDF"). Unknown types fall back to the
    source_type string itself so a future ingest source (epub, html, …)
    is visible without a code change.

    The favicon for URL docs is a v2.1 fast-follow; for now the bare
    domain text carries the recognition signal.
    """
    if source_type == "url":
        return source_domain or "URL"
    if source_type == "markdown":
        return "MD"
    if source_type == "pdf":
        return "PDF"
    return source_type.upper()


def register(templates: Jinja2Templates) -> None:
    """Wire the helpers into a templates env. Called from app startup;
    keeps the registration inside this module so the helper list is
    one self-contained surface.

    `relative_date` is a filter (single-arg | invocation reads cleanly).
    `source_label` is registered as a global so the template can call
    it as a function with both positional args — `source_label(t, d)`
    reads better than `t|source_label(d)` at the call site.
    """
    templates.env.filters["relative_date"] = relative_date
    templates.env.globals["source_label"] = source_label
