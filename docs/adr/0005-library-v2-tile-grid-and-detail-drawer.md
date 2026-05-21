# ADR 0005: Library v2 — tile grid + detail drawer

- **Status:** accepted
- **Date:** 2026-05-21
- **Tracking:** bd `Parsem-7wu` (library-v2 epic)
- **Supersedes:** parsem-spec.md §9.1 "Library view" (row-shaped table); the v1 library renders today through `parsem/web/templates/library.html` and `_library_row.html`.

## Context

The library page is the funnel every document lands in. Substrate work has moved on — atomic chunking with a cursor engine, docling PDFs, firecrawl URL ingest, NAS folderwatch — but the library is still shaped from the MD-only era: a flat HTML `<table>` with title / status / progress / heatmap / rename / re-chunk / delete columns. It works, but it's debug-grade: every signal is buried in a cell, recognition is title-typography-only, and there is no surface for tagging, filtering, or per-document detail beyond the row itself.

This ADR captures the design decisions for v2 of the library — re-shaped as a tile grid + detail drawer — settled in a grilling session on 2026-05-21. The redesign is a peer surface to the reader, not its index page.

The decisions below were resolved one branch at a time against two reference points:

- **Kindle / Audible library patterns** — recognition over information on the tile; detail one click away; reading-state is a footnote, not the mark; segments + sort as a top-strip control.
- **Hickey + Armstrong design lens** (per project memory) — decomplect signals, names matter, data over code, contracts at edges, let it crash early.

## Decision

**The library v2 is a tile grid of squares (3 across desktop), each tile carrying title-as-mark + 5×5 silhouette + source slug. Clicking a tile opens a side-drawer with full detail and actions; the drawer's "Open" button enters the reader. A top control strip carries exclusive reading-state segments + composable tag chips + a sort dropdown.**

### Tile anatomy

```
┌─────────────────────┐
│  Brick Wisdom:      │  ← title, 2-line + ellipsis,
│  Foundations for…   │     hover-tooltip = full title
│                     │
│       ▮▮·▮·         │  ← 5×5 silhouette (3-state cells)
│       ▮·▮▮·         │     · = faint unread
│       ▮·▮·▯         │     ▯ = neutral (--tint-3) read-unrated
│       ▯···          │     ▮ = mean-rating-coloured read-rated
│       ····          │       (from --rating-1..5 palette)
│                     │
│  Stratechery        │  ← source slug:
│   · 3d ago · 73%    │     URL → [favicon] + domain
│                     │     File → [MD] / [PDF] badge
└─────────────────────┘     + ingest-date + percent
   (~200×220, taller than wide; 3 across desktop)
```

- **Title is the primary mark.** Recognition pattern from Kindle/Audible: when you don't have covers, title typography does the recognition work; the silhouette is a backup signal, not the foreground.
- **2-line ellipsis** for long titles + **hover-tooltip showing the full title** as the safety net. Smart head-truncate on visible-sibling collision is a v2.1 follow-up.
- **5×5 silhouette is brand colour, not fingerprint.** Three cell states encode (read/unread × rated/unrated) in two visible properties: opacity (read = filled, unread = faint outline) and hue (rated cells take the bucket's mean rating colour; unrated reads take a neutral). Always 5×5; cell `i` aggregates chunks `[⌊i·N/25⌋, ⌊(i+1)·N/25⌋)` for an N-chunk doc. Stable until the user rates — the silhouette doesn't flip on engagement state alone.
- **Slug carries provenance**, not engagement. Ingest-date answers "how stale is this doc?" — a different axis from the default sort (last-opened), so the two facts don't double-encode each other. Format: relative-recent (< 30d) then absolute; tooltip on the date shows absolute always.
- **No thin progress bar.** The silhouette already shows read/unread spatially; the bar would be double-encoding. Percent number stays as text in the slug.

### Detail drawer

Click a tile → a right-side drawer (≈420px wide, full height) slides in over the library (page dimmed via overlay, same mechanic as the existing "Aa" prefs panel — reuse `.prefs-overlay` shape).

Drawer contents, top to bottom:

1. Close (✕) — top-right corner; also dismisses on Esc and on backdrop click.
2. **Full title** (untruncated, prominent).
3. Source slug — favicon + domain (URL) or `[MD]`/`[PDF]` badge (file) + absolute ingest date.
4. **Open** — primary button. Click → `/documents/{id}/reader`.
5. Full-resolution **section-aware heatmap** — each section a row of cells (variable width = section length); cells use the same three-state semantics as the tile silhouette but with the full `--rating-1..5` palette (mean-rounded per cell). Section names rendered as small labels.
6. Reading stats line — `73% · chunk 22 of 31 · ~12 min remaining · 4 pins`.
7. **Tags** — chip list with `[× tag]` for removal and `[+]` button for add (text input with autocomplete over existing tags). Manual tags only in v2.0.
8. **Source URL** (URL ingests only) — clickable, opens in new tab.
9. Secondary actions footer — `Re-chunk` · `Rename` · `Delete` (each with appropriate confirmation; Delete and Re-chunk use the existing endpoints).

The drawer is **the deliberate antechamber**. Two-step open (tile → drawer → Open) is in character with the reader spec's "the reader enters deliberately." A double-click on the tile is the v2.1 fast-path that opens the drawer with Open auto-triggered.

### Control strip

```
┌──────────────────────────────────────────────────────────┐
│  ▦ Library                            🔎  + Add    Aa    │  ← header
├──────────────────────────────────────────────────────────┤
│   All   ⟨In progress⟩   Unread   Finished                │  ← segments
│   [wisdom]  [brick]  [stratechery]  [+]      Sort: Last  │  ← tag row (hidden if no tags)
├──────────────────────────────────────────────────────────┤
│   tile  tile  tile                                       │
│   tile  tile  tile                                       │
```

- **Exclusive reading-state segments** — All / Unread / In progress / Finished. Default: **In progress** (you walk in, books with bookmarks tucked in are right there). One segment selected at a time.
- **Composable tag chips** below the segments — multi-select, AND-stacked, narrows the current segment. Row hides entirely when zero tags exist. `[+]` appears once ≥1 tag has been created.
- **Sort dropdown**, independent of filters. Default: **Last opened**. Other options: Recently added · Title A–Z · Longest.
- **`+ Add`** demoted to a small button in the top-right header → opens a modal with File / URL tabs. Today's prominent two-form ingest section is removed.
- **Search (🔎)** is a v2.1 affordance; greyed/hidden in v2.0.

### Segment semantics

- `All` — every doc.
- `Unread` — `high_water_chunk == 0`.
- `In progress` — `0 < high_water_chunk` AND `high_water_chunk / total_chunks < 0.95`.
- `Finished` — `high_water_chunk / total_chunks ≥ 0.95` (lenient — covers footnotes/appendix).

### State persistence

Library navigation state (segment, sort, active tag filters) persists via:

- **URL query params** as authoritative — `/library?segment=in-progress&sort=last-opened&tag=wisdom&tag=brick`. Shareable, back-button-correct, observable.
- **localStorage** as default fallback — when no query params present, use the last-used values. Peer of the "Aa" prefs persistence model.

Tile-level UI state (drawer open for which doc) is **not** persisted in v2.0 — refresh closes the drawer. v2.1 will move drawer-open into a URL fragment (`/library#doc=42`) so it becomes shareable too.

### Tag model

Manual tags only in v2.0. Schema: `document_tags(doc_id INTEGER NOT NULL, tag TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (doc_id, tag), FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE)`. Lowercased on input, hyphens-not-spaces, max 32 chars. Flat namespace — no hierarchy.

LLM-suggested tags are a deferred phase (lands when the Ask/LLM phase does).

## Why this carve-up

- **Recognition over information** (Nielsen #6). The tile's job is "yes, that's the one I was reading," not "tell me everything about this doc." Title-as-mark + slug + quiet silhouette serves recognition; the drawer serves information.
- **Decomplect at every layer.** Silhouette = how-it-felt (rating shape). Bar/percent = how-far (progress). Slug = provenance (source + freshness). Segments = exclusive primary axis. Tag chips = composable secondary axis. Sort = ordering, independent of filtering. Each surface does one thing; together they compose without colliding.
- **Names matter** — a "section" is a real noun (`SectionRecord`); the drawer's heatmap renders sections as rows. A `tag.source` of manual vs auto is the kind of fact that *would* deserve its own column if we were doing both, so the v2.0 schema stays free of it now and adds it cleanly later if/when LLM suggestions land.
- **Data over code.** Tag chips are populated from the data itself — the chip row *is* a query over `document_tags`. No hardcoded category list. Same for segments (they're a `WHERE` clause over `high_water_chunk / total_chunks`).
- **Contracts at the edges.** The drawer reuses the existing `/documents/{id}/rename`, `/delete`, `/retry-parse` endpoints — no new write surfaces in v2.0. The new read endpoint is a row-payload extension on the existing library GET, not a separate detail endpoint (the drawer pre-fetches with the row).
- **Let it crash early.** A missing tag row → empty chip column, no error. A doc with `total_chunks == 0` (mid-conversion) → silhouette renders all-faint, segment falls into the appropriate state. No silent degraded paths.
- **Parsem character** — "soft surfaces, hard edges" (project memory). The grid is visually generous and welcoming; the constraint (no batch ops, no drag-reorder, no clever sorts beyond what the data supports) is firm.

## What changes in code

### Templates
- `parsem/web/templates/library.html` — rewritten: header strip + segments + tag-chip row + grid (CSS Grid, `auto-fill` columns ≥ 200px).
- `parsem/web/templates/_library_row.html` — replaced by `_library_tile.html` (square tile with title / silhouette / slug).
- New: `parsem/web/templates/_library_drawer.html` (the side-drawer panel, server-rendered with the page; visibility toggled by JS).
- New: `parsem/web/templates/_library_add_modal.html` (the `+ Add` modal).

### CSS
- `parsem/web/static/reader.css` — library section rewritten. New tokens: `--silhouette-unread`, `--silhouette-read`, plus reuse of `--rating-1..5`. Drawer reuses `.prefs-overlay` / `.prefs-panel` shape from the existing "Aa" panel.

### JS
- `parsem/web/static/library.js` — extended: segment switch, tag chip toggle, sort selection, drawer open/close, hover-tooltip for full title, URL ↔ state sync (query params + localStorage fallback), Add modal handling.

### Backend
- `parsem/store/documents.py` — `list_library_rows` payload extended with: `source_type` ('md' | 'pdf' | 'url'), `source_url` (nullable), `source_domain` (nullable, derived at row time), `ingest_date`, `last_opened`, `pin_count`, `total_reading_seconds`, `tags` (list of strings), `section_layout` (list of `(section_title, chunk_count)` for the drawer's full heatmap), `silhouette_buckets` (list of 25 `{state, mean_rating}` for the tile silhouette — pre-computed server-side).
- `parsem/web/routes/library.py` — `GET /library` accepts `segment`, `sort`, `tag` query params; filters/sorts via SQL, not in template.
- New: `parsem/store/tags.py` — CRUD over `document_tags`.
- New endpoints:
  - `POST /documents/{id}/tags` — add tag; returns the drawer's chip row partial.
  - `DELETE /documents/{id}/tags/{tag}` — remove tag; returns the drawer's chip row partial.
- Migration: `document_tags` table.

### Tests
- New: `tests/web/test_library_v2_filters.py` (segment / tag filtering, sort).
- New: `tests/web/test_library_tags.py` (tag CRUD endpoints).
- New: `tests/store/test_silhouette_buckets.py` (down-sample logic).
- Existing rename / delete / retry-parse tests stay green (endpoints unchanged).

## What does NOT change

- Reader surface, chunking engine, ingest pipeline (all of `claude-axx*`, `claude-fro`, `claude-mwx*`).
- Rename / delete / retry-parse contracts — same endpoints, drawer surfaces them via the same partials.
- `extraction_runs`, `chunks`, `chunk_ratings`, `pins` schemas.
- The reader → library back-link (`top-bar__home`) — still navigates to `/library`, now lands on the saved (or default) segment/sort/tags.

## Consequences

### Positive
- Library becomes a first-class surface — peer of the reader, not its index.
- Tile grid scales visually to 100+ docs (today's table degrades at ~30 rows).
- Rating signal is surfaced at-glance via the silhouette; full detail accessible without leaving the page.
- Tag organisation gives the user a real curation surface.
- URL state makes the library shareable/bookmarkable per filter combination.

### Negative / risks
- **Two-step open is +1 click** vs today's row-link → reader. Mitigated by the drawer being fast (no network round-trip — data ships with the page row) and by the v2.1 double-click fast-path.
- **5-colour rating palette at 0.2 opacity / 5×5 scale risks muddiness.** Mitigated: opacity tuning during build (likely 0.4–0.6 with the title sitting on a soft scrim, not 0.2), and the silhouette is *brand colour* not *fingerprint* — pixel-perfect 5-way discrimination is not the design goal.
- **Tag bootstrap problem.** New users see an empty chip row until they tag something. Acceptable — the chip row is hidden when empty, segments+sort still work alone.
- **Favicon-fetching deferred to v2.1.** v2.0 ships with `[URL]` badge for URL docs; favicon enhancement lands later.
- **Larger row payload.** Server-side pre-computation of `silhouette_buckets` and `section_layout` adds CPU per row. Acceptable for current library sizes; profile when corpus exceeds 200 docs.

## Related

- ADR 0001, 0002, 0003, 0004 — unrelated; library v2 is a presentation-layer redesign, not an ingest/eventing change.
- parsem-spec.md §9.1 — superseded by this ADR; spec to be updated as part of the epic.
- Project memory: `parsem_character.md`, `feedback_velocity_over_micro_iteration.md`, `feedback_hickey_armstrong_design_lens.md`.
- Kindle / Audible library patterns — used as reference for "recognition over information" and "detail-one-click-away."
