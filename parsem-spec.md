# Parsem Project Specification

> **Version:** Post-grill final, 2026-05-07.
> **Predecessor:** `parsem-spec-draft.md` (preserved for diffing).
> **Scope:** Self-hosted, single-user, local-first deep-reading app for Markdown documents.

---

## 1. Project Summary

**Parsem** is a self-hosted, local-first web application for deep reading of Markdown documents using a **Progressive Reveal Reading** interface, with a per-document reading economy that paces the reader, and a colour-coded pin system that lets the reader build their own semantic taxonomy of a document.

The MVP is intentionally narrow:

- Markdown-first (`.md` upload only — no PDF, no OCR, no scanned-PDF support)
- No images
- No complex layout preservation
- Single-user, single-machine
- Local SQLite database, local file storage
- Basic HTTP auth via env vars (off by default in dev)
- A welcome document acts as in-app onboarding
- LLM tutor / Q&A is reserved for a later phase; the `?` action exists as a placeholder

The MVP exists to put the reading mechanic in front of a real reader (the developer) on real corpus material and see what falls out. There are no pre-defined success criteria — ship and observe.

---

## 2. Product Aim

Parsem helps a reader engage more deeply with documents that require thought, attention, and judgement.

Standard digital reading interfaces optimise for speed, scrolling, search, and skimming. Parsem deliberately introduces friction in two ways:

1. **The text is revealed one chunk at a time** — the reader explicitly advances.
2. **A reading economy paces advancement** — a small bucket of reveal-tokens regenerates over time; sprinting is throttled.

The product thesis:

> Some documents deserve more than skimming. Parsem creates a reading chamber where text is revealed deliberately, time-paced, and where the reader can mark, rate, and revisit passages by their own categories.

---

## 3. Core Goals

### 3.1 Validate Progressive Reveal Reading at scale-of-one

Use the MVP on real documents. Observe whether deliberate reveal + paced advancement + colour-coded pins + effort ratings produces a meaningfully different reading experience from scrolling.

There are no pre-defined success thresholds. The reader (developer) will know when it works.

### 3.2 Build a minimal self-hosted reading app

Run on a home server with minimal dependencies. Browser UI, Markdown upload, SQLite, local file storage, persistent reading state, keyboard-first interaction.

### 3.3 Establish a foundation for later layered features

Future post-MVP layers (highlight categories, chunk-anchored notes, LLM-driven Q&A grounded in current chunk + window + pinned context) are anticipated by the architecture. The MVP must not block them.

---

## 4. Non-Goals for MVP

- General-purpose PDF parsing
- Pandoc-based PDF → Markdown ingestion (cut: manual `pandoc` outside the app if needed)
- Scanned PDF OCR
- Image extraction or rendering
- Reading Weight / semantic-density heuristic display (reintroduced post-MVP, trained on real effort-rating data)
- Settle as a separate action (cut — the bucket regen interval *is* the pace)
- Mark-unclear as a separate action (collapsed into effort rating ≥ 4)
- Persist as a sidebar feature (replaced by Pins as gutter dots)
- Word-level text selection and highlight layers (post-MVP)
- LLM tutoring / chunk Q&A (post-MVP; key reserved)
- Background worker / async processing (Markdown parse is fast enough synchronously)
- Multi-user access control / production authentication
- Mobile-native app
- URL ingestion, API ingestion, paste-as-text ingestion
- Vector search, agent workflows, spaced repetition
- Cross-document pin/highlight queries

---

## 5. Target User

A single reader operating a self-hosted Parsem instance on their own machine.

The reader engages with documents such as:

- Strategy papers
- Policy documents
- Research papers (already converted to Markdown)
- Governance and board papers
- Technical documents
- Long essays
- Internal proposals

The reader is not consuming quickly. They are trying to understand, remember, question, and think with the document.

---

## 6. Core User Story

> As a reader, I want to upload a Markdown document I need to read deeply, so that Parsem can reveal it progressively, pace my advance, allow me to mark passages by my own categories, rate the effort of each passage, and resume reading with a small re-entry warm-up the next time I open it.

Supporting stories:

> As a reader, I want a library of previously uploaded documents, so I can resume reading where I left off (with a couple of chunks of warm-up to remember context).

> As a reader, I want to **pin** a passage with a colour I've assigned semantic meaning to, so I can navigate between same-colour pins later (e.g., between all "definitions" or all "claims I disagreed with").

> As a reader, I want to **rate** the cognitive effort of each chunk on a 1–5 scale, so the document grows a heatmap of my struggle and ease.

> As a reader, I want to **conceal** a passage if I'm not ready for it, so the system retreats the visible window by one chunk without penalising me.

> As a reader, I want a calm reading surface — paper-like background, comfortable typography, narrow column — so the chrome stays out of the way.

---

## 7. Core Interaction Model

The reader has **four primary actions**, plus **rating** as an in-place data action, plus **navigation gestures**.

### 7.1 Reveal

Reveal advances to the next chunk. Costs **1 token** from the per-document bucket. Backward navigation through paid chunks is free. Re-reveal of the current or a past chunk is free.

### 7.2 Conceal

Conceal retreats by one chunk in the windowed view. Free. Concealment is a first-class action — *"I am not ready for this"* — not a failure.

If conceal crosses a section boundary (heading), the section banner reverts to the previous section and the windowed view rebuilds with the last K-1 chunks of that section.

### 7.3 Pin

Pin marks a chunk with one of 5 colours. Pressing **P** cycles through `none → c1 → c2 → c3 → c4 → c5 → none`. Free. The reader assigns colours their own semantic meaning (e.g., yellow = definitions, blue = claims, green = questions). A small label panel (`:` key) lets the reader name each colour globally.

Pins are durable across sessions. Pins live in the **left gutter** as small coloured dots. There is no sidebar in MVP.

`]` and `[` cycle to the next / previous pin of the most recently used colour, jumping the reader's position to that pin and rebuilding the window around it. **Esc** (or `'`) returns to the pre-jump position.

Pin spans are designed for a future word-level selection feature; in MVP every pin defaults to whole-chunk span.

### 7.4 Rate

Rate (effort) is a 1-5 keypress: 1 = easy, 3 = normal, 5 = struggled. Optional, anytime, non-advancing. Rating does **not** consume a token. Re-rating a chunk overwrites the latest rating; full history is preserved in the event log. The aggregate is rendered as a subtle horizontal bar at the bottom of each chunk and as a heatmap strip in the library view.

### 7.5 Ask (placeholder in MVP)

`?` reserved for future chunk-grounded Q&A with a local LLM (Ollama / OpenAI-compatible local endpoint). MVP shows a placeholder: *"Ask is not yet available — coming in a future version."*

---

## 8. Keyboard Controls

Keyboard is the primary reading mode. Mouse/touch may mirror these later but the keyboard grammar is the source of truth.

| Key       | Action                                  |
|-----------|-----------------------------------------|
| Space     | Reveal next chunk (cost 1 token)        |
| Backspace | Conceal current chunk (retreat by one)  |
| 1–5       | Rate effort on current chunk            |
| P         | Pin / cycle pin colour                  |
| `]` / `[` | Next / previous pin of active colour    |
| Shift+Up  | Expand windowed review (Esc to exit)    |
| `'`       | Return to pre-jump position             |
| `,`       | Open settings panel (Esc to close)      |
| `:`       | Open pin colour labels (Esc to close)   |
| `?`       | Show cheat-sheet overlay (Esc to close); also reserved for Ask placeholder |
| Esc       | Close panel / focus mode                |

---

## 9. Reader Experience

### 9.1 Library view

On launch, the reader sees a list of documents.

Each row shows:

- Title (rename via inline click)
- Status (`uploaded`, `processing`, `ready`, `failed`)
- Progress percentage (`high_water_chunk / total_chunks`)
- Last-opened time (last-opened first; secondary alphabetical)
- A small horizontal heatmap strip — one thin column per chunk, coloured by latest effort rating (red = 5, amber = 4, neutral = 3, light blue = 1–2, blank = unrated)
- Failure reason and Retry / Delete actions if `status = failed`

Failed parses do not retry automatically.

### 9.2 Upload view

Markdown only. Upload a `.md` file → store the original → parse synchronously → save chunks → mark document `ready` (or `failed` with a reason).

Pandoc PDF ingestion is out of MVP. The README documents `pandoc input.pdf -o output.md` as a pre-upload step the reader can run themselves.

### 9.3 Document opening

Opening a freshly-uploaded document for the first time shows the title and a `[Begin]` button. The document does not spill its content. The reader enters deliberately.

### 9.4 Resume

When reopening a document, Parsem opens at `high_water_position − N`, where `N = resume.warm_chunks` (default 2). This warms the cogs — the reader re-reads a couple of chunks before advancing. The N chunks are paid territory, free to re-read.

If `high_water − N < 0`, clamp to 0. If the warm-restore lands across a section boundary, the windowed view clears at the heading; the section banner appears sticky above; the warm chunks display below.

### 9.5 Reading surface

The reading area uses three vertical regions:

```
[ left gutter (~16px) ]  [ main reading column (max 720px) ]  [ right gutter (~16px) ]
   pin colour dots          windowed view + current chunk       reserved for future sidebar
```

The **main column** shows:

- A sticky heading banner at the top (current section)
- A windowed view of the last **K = 5** settled chunks above the current chunk, faded to 70% opacity
- The **current chunk** at full opacity, optional 2px subtle left-border accent
- Below the current chunk, a subtle rating prompt: `1 · 2 · 3 · 4 · 5`
- Below that, when the bucket is empty: a countdown — *"Next reveal in 7s"* — plus inline reminders: `Persist · Rate · Ask`

The **left gutter** shows pin colour dots aligned with each chunk.

The **right gutter** is empty in MVP but reserved in CSS so future expansion (notes, chunk Q&A) does not reflow the main column.

The window **clears** at every heading — when the reader crosses into a new section, the prior section's chunks vanish from the visible window and the new section's heading becomes the sticky banner. Backward navigation across the boundary repopulates the prior section's window.

---

## 10. Document Model

The MVP document model is a list of **chunks**, derived from a Markdown token stream by a deterministic chunker. Sections group chunks by heading boundary.

A chunk is the atomic unit of reveal. A chunk has:

- A position (0-indexed, contiguous within a document)
- A reference to the source-Markdown byte range (`source_offset_start`, `source_offset_end`)
- The denormalised chunk text (cached for fast read; re-derivable from source + offsets)
- A `lead_token_type` (`heading`, `paragraph`, `list_item`, `code`, `blockquote`, `table`)
- An optional `lead_heading_level` (1–6) when the chunk's lead token is a heading
- An `estimated_read_seconds` value
- A `section_id` linking it to the section it belongs to

Section is a lightweight group with a heading chunk (or `NULL` for prologue), heading level, and the inclusive `(start, end)` chunk-position range.

The model has no full-document AST. The Markdown parser produces a token stream with source offsets; the chunker is the only stage that turns that stream into chunks.

---

## 11. Chunking Rule

The chunker is a pure function: `chunker(token_stream, config) → chunks + sections`.

### 11.1 The 10-second budget rule

A chunk is filled greedily with whole sentences until the next sentence would exceed the **`chunking.budget_seconds`** budget at the configured WPM. Round down — never split a sentence.

### 11.2 Heading absorption

A heading chunk **absorbs forward** sentences from the body following it, up to the budget, OR until the next heading hits — whichever comes first. A bare heading immediately followed by another heading becomes a heading-only chunk. A heading at end-of-document becomes a heading-only chunk.

### 11.3 Structural blocks

- **Code blocks** are one chunk regardless of length when `chunking.code_handling = block`. Read time is estimated at the `read_wpm_code` rate (slower than prose). Token cost stays 1. When `code_handling = prose`, code is sentence-split and packed like prose.
- **Lists**: each item is one chunk when `chunking.list_handling = item`. When `block`, the whole list is one chunk; when `prose`, list items are joined and packed like prose.
- **Blockquotes** are one chunk regardless of length.
- **Tables** are one chunk regardless of length.
- **Horizontal rules**, **image syntax**, blank lines are not chunked (skipped during chunking).

### 11.4 Reading time estimation

`estimated_read_seconds = words_in_chunk / (read_wpm × wpm_user_scaling) × 60`, with `read_wpm` selected per content type (`prose` or `code`).

### 11.5 Sentence detection

`pysbd` (Python Sentence Boundary Disambiguation). Pure Python, no model downloads, handles abbreviations and typical edge cases.

### 11.6 Re-chunking

If `chunking` config changes, the chunker is re-run. Existing reading events continue to reference the old chunk ids. The projection rebuild step re-anchors event chunk-references to new chunks via `source_offset` overlap.

---

## 12. Reading Economy

The economy paces advancement through a **per-document** token bucket.

### 12.1 Tokens, capacity, and regen

- `bucket.capacity` (default 3) is the maximum number of tokens.
- `bucket.regen_seconds` (default 12; user's pace knob) is the regen interval.
- `bucket.start_full = true` — opening a document gives the reader a full bucket.

### 12.2 Computed, not stored

Bucket state is a **pure function** of `(now, last_advance_event_for_this_document, advance_count_since_last_full, capacity, regen_seconds)`. There is no "current tokens" column. Each Reveal request recomputes the bucket from the event log + clock.

### 12.3 Costs

- `costs.reveal = 1`
- `costs.conceal = 0`
- `costs.rate = 0`
- `costs.pin = 0`

All costs are configurable in `settings.config_json`. Rate, conceal, and pin are free to encourage thoughtful reading.

### 12.4 Re-reveal and backward navigation

Re-revealing the current chunk or any chunk at position ≤ `high_water_position` is **free**. The system tracks the highest-position chunk paid for. Tokens are spent only on advancing into new territory.

### 12.5 Empty-bucket UX

When the bucket is empty, Reveal does not silently fail. Instead, the reader sees:

```
Next reveal in 7s
Rate effort  1 · 2 · 3 · 4 · 5
Persist · Ask
```

A small countdown ticks down on the client. When tokens regen, the prompt clears. The wait window is a natural opportunity for the reader to use the alternative actions — rate the current chunk, pin it, ask (post-MVP).

### 12.6 Fresh-session credit

On reopening a document, if more than `bucket.fresh_session_idle_multiplier × regen_seconds` (default 5×, i.e. one minute at default regen) has passed since the last reveal, the bucket is treated as full. This makes "close and come back later" feel welcoming, not punitive — while preserving the anti-burst floor for rapid open/close cycles.

---

## 13. Pins

Pins are colour-coded categorical markers. Five colours, reader-assigned semantics, IDE-breakpoint-style navigation.

### 13.1 Mechanics

- **P** cycles the current chunk's pin: `none → c1 → c2 → c3 → c4 → c5 → none`.
- Pins are durable across sessions.
- Each pin's data shape is a span: `(chunk_id_start, word_start, chunk_id_end, word_end, color_id)`. In MVP, every pin defaults to `(chunk, 0, chunk, -1)` — the whole chunk. Word-level selection is a post-MVP feature; the schema is ready for it.

### 13.2 Colour palette (default)

| Colour ID | Hex       | Editorial note      |
|-----------|-----------|--------------------|
| 1         | `#E4B363` | warm yellow         |
| 2         | `#5B8DBE` | calm blue           |
| 3         | `#7A9F6B` | muted green         |
| 4         | `#C97B63` | terracotta          |
| 5         | `#9477B4` | muted purple        |

Mid-saturation, work on paper / sepia / dark backgrounds, distinguishable for most colour-blind readers.

### 13.3 Labels

Reader can name each colour globally via the `:` panel, e.g. `Yellow: definitions / Blue: claims / Green: questions`. Stored in `pin_color_labels`, single label per `color_id`. Optional and omitted by default.

### 13.4 Navigation

- `]` jumps to the **next** pin of the most recently active colour. The reading position teleports to the target chunk; the window rebuilds around the target; the section banner updates.
- `[` jumps to the previous pin of the same colour.
- `'` returns to the pre-jump position.

If the reader has not yet touched a pin in the session, `]` and `[` cycle through pins of any colour.

### 13.5 Pin density

No cap. Cross-section pin jumps update the section banner and rebuild the window with the K-1 chunks that precede the pinned chunk in its section (or empty above, if the pin is in the first chunks of its section).

---

## 14. Effort Rating

The effort rating produces a heatmap of cognitive effort per chunk, used by the reader to revisit hard passages, by the future LLM Ask feature for context, and by future revision/coaching layers.

### 14.1 Scale

- 1: easy
- 3: normal
- 5: struggled

### 14.2 Mechanics

- 1–5 keypress on the current chunk records an effort event.
- Optional, anytime, non-advancing.
- Re-rating is allowed; latest wins; full history kept in `reading_events`.
- Backward navigation lets the reader re-rate prior chunks.

### 14.3 Display

A subtle horizontal bar at the bottom of each chunk in the main column. Library view shows a small heatmap strip per document.

Diverging palette: red (5) → amber (4) → neutral grey (3) → light blue-grey (2) → light blue (1). Unrated chunks render blank.

---

## 15. Visual Frame

The reading surface is a **windowed view** with `view.window_k = 5`. The current chunk is visible at full opacity; the last K-1 settled chunks above are at 70% opacity. Chunks scroll off as the reader advances.

### 15.1 Section boundaries

The window **clears** when the reader crosses a heading. The new section's heading becomes a sticky banner. Backward navigation across the boundary repopulates the prior section's window.

### 15.2 Backward review

`Shift+Up` enters review mode: the windowed K expands so the reader can scroll further into the past without changing their `current_position`. `Esc` exits review mode and returns to the prior view. Pins can be created, ratings can be recorded, while in review mode.

### 15.3 Presentation

Configurable:

- **Background**: `paper` (default — `#FAF7F0`), `sepia` (`#F4ECD8`), `dark` (`#1A1A1A`)
- **Prose font**: Charter (default), Georgia, Lora, Inter
- **Code font**: JetBrains Mono (default), Fira Code, IBM Plex Mono, system monospace
- **Font size**: 18px default, range 14–24
- **Density**: `compact` (line-height 1.4), `normal` (1.6, default), `spacious` (1.85)
- **Max column width**: 720px (≈65 chars at 18px Charter)

Presentation prefs live in browser localStorage (single-machine, single-user). Server has nothing to know.

---

## 16. Multi-Tab and Multi-Window

Multiple tabs or windows on the same document are **allowed**. The event log stays consistent because all events are append-only and timestamped, regardless of which tab wrote them.

### 16.1 Auto-sync via polling

Each open tab/window polls `/documents/{id}/version` every `view.sync_interval_seconds` (default 2s). When the server's max event timestamp for the document changes, the tab re-fetches the rendered reader fragment and swaps it in. All tabs stay within ~2s of truth.

The bucket is server-authoritative. If two tabs both attempt to advance at near-simultaneous moments, the second hits the empty-bucket countdown UX rather than double-spending.

### 16.2 Cross-browser limitation

Two different browsers (e.g. Chrome and Firefox) cannot detect each other client-side. They will both poll independently and stay eventually-consistent through the server. Documented as known MVP behaviour.

---

## 17. Ingestion

### 17.1 Markdown only

Upload a `.md` file. The pipeline runs synchronously:

```text
Upload .md
  → store original at data/originals/{doc_id}.md
  → parse Markdown into a token stream (with source offsets)
  → run the chunker (10s budget, sentence boundaries, structural rules)
  → emit chunks + sections
  → mark document `ready` (or `failed` with reason)
```

### 17.2 Failure handling

If parsing fails, the document is marked `failed` with a `failure_reason`. The library shows the row with **Retry** and **Delete** buttons. No auto-retry.

Common failures:

- Empty Markdown → `Document is empty.`
- No headings detected — *not* a failure; the document is treated as a flow with no section boundaries.
- Some Markdown blocks could not be classified cleanly → `failed` with diagnostic.

### 17.3 Images

Markdown image syntax is **skipped silently** during chunking. No placeholder, no warning. (Spec original used `[Image omitted]` — dropped to keep the reading surface clean. Future image support will reintroduce.)

---

## 18. Technical Architecture

### 18.1 Hickey-pragmatic separation

The architecture follows Rich Hickey's discipline of *separating data, transforms, and frameworks*, applied where it earns its keep:

- **The event log is the source of truth** for reader actions (`reveal`, `conceal`, `rate_effort`, `pin_set`, `pin_clear`, `open_document`, `close_document`).
- **Projections are caches** (`reading_state`, `chunk_ratings`, `pins`). They are rebuildable from the event log and tracked by `last_event_id_applied`.
- **Bucket state is a pure function** of `(now, events, config)`. Never stored.
- **Chunking is a pure transformation** from the Markdown token stream + config. Re-runnable.
- **Selections are first-class values** (chunk-id span). Pin, future highlight, future note, future Q&A thread all reference selections.
- **Layers are first-class polymorphic data** (post-MVP). System layers (nouns, verbs, obligation words from a tagger) and user layers (yellow, blue, themes) coexist in one table.
- **Domain logic is pure functions** in `domain/`. The web framework is just transport. None of `domain/` imports from `web/` or `store/`.

### 18.2 Module structure

```
parsem/
  domain/
    chunking.py          # pure: tokens + config → chunks + sections
    bucket.py            # pure: events + config + now → tokens_now
    projections.py       # pure: events → reading_state, chunk_ratings
    selections.py        # value type
    layers.py            # post-MVP
  parse/
    markdown_parse.py    # markdown-it-py wrapper → token stream + offsets
    sentence.py          # pysbd wrapper
    word_tokens.py       # post-MVP
  store/
    db.py                # schema, migrations, connection
    events.py            # append-only writer; query helpers
    projections_cache.py # incremental + full rebuild
    settings.py          # config read/write
  web/
    routes/{library,upload,reader,settings}.py
    templates/{base,library,upload,reader,cheatsheet,settings}.html
    static/{app.css, reader.js}
  cli.py                 # parsem rebuild-projections [--document N]
  main.py                # FastAPI app

data/
  parsem.db
  originals/             # uploaded .md files
  welcome.md             # bundled welcome doc (Phase 3)
README.md
```

### 18.3 Foreground loop

```text
User opens document
  → load chunks
  → apply projections (reading_state, chunk_ratings, pins)
  → compute current bucket from event log + clock
  → render windowed view at high_water − warm_chunks
```

### 18.4 No background worker for MVP

Markdown parsing of a 100 KB document takes <500 ms. Synchronous on upload, the reader waits on a spinner. The `jobs` table from the original spec is removed. A worker may reappear in a later phase if Pandoc, OCR, or local-LLM features land.

### 18.5 Projection rebuild

If the projection cache drifts from the event log (detected by `last_event_id_applied < MAX(reading_events.id)` for a document), the projection is rebuilt at server start. A CLI command — `parsem rebuild-projections [--document N]` — provides manual recovery.

---

## 19. Tech Stack

- **Backend**: Python + FastAPI. Domain modules pure-Python, FastAPI is just transport.
- **Templates**: Jinja2 + HTMX for partial updates on Reveal/Conceal/Pin/Rate.
- **Client JS**: ~50–100 lines, vanilla. Keyboard handling, bucket countdown timer, polling sync, pin cycle navigation.
- **Database**: SQLite with `journal_mode=WAL`, `foreign_keys=ON`, `synchronous=NORMAL`.
- **Markdown parser**: `markdown-it-py` (token stream output, source offsets, plug-in friendly).
- **Sentence detection**: `pysbd`.

Local LLM (post-MVP) options: Ollama, LM Studio local server, OpenAI-compatible local endpoint.

---

## 20. Configuration

Configuration lives in a single `settings` row (`config_json` blob) and is mirrored into the editing UI via the `,` settings panel. Per-document overrides live in `documents.preference_overrides_json` (NULL = use global; UI exposure deferred post-MVP).

```yaml
chunking:
  budget_seconds: 10
  read_wpm_prose: 220
  read_wpm_code: 110
  wpm_user_scaling: 1.0       # range 0.5–2.0
  code_handling: block        # block | prose
  list_handling: item         # item | block | prose

bucket:
  capacity: 3
  regen_seconds: 12           # the user's pace knob
  start_full: true
  fresh_session_idle_multiplier: 5

costs:
  reveal: 1
  conceal: 0
  rate: 0
  pin: 0

view:
  window_k: 5
  pin_color_count: 5
  sync_interval_seconds: 2

resume:
  warm_chunks: 2

presentation:                 # client localStorage; mirrored as default
  background: paper           # paper | sepia | dark
  font_prose: charter
  font_code: jetbrains
  font_size: 18
  density: normal             # compact | normal | spacious
  max_column_width: 720

auth:
  require_auth: false         # PARSEM_REQUIRE_AUTH=true to enable
```

---

## 21. Database Schema

```sql
-- Documents
CREATE TABLE documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'markdown',
  original_path TEXT NOT NULL,
  status TEXT NOT NULL,                          -- uploaded | processing | ready | failed
  failure_reason TEXT,
  total_chunks INTEGER,                          -- denormalised for progress %
  preference_overrides_json TEXT,                -- per-doc config overrides; NULL = global
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Sections (heading-bounded grouping)
CREATE TABLE sections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL,
  heading_chunk_id INTEGER,                      -- NULL for prologue
  heading_level INTEGER,
  start_chunk_position INTEGER NOT NULL,
  end_chunk_position INTEGER NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- Chunks
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL,
  position INTEGER NOT NULL,                     -- 0-indexed, contiguous
  source_offset_start INTEGER NOT NULL,          -- byte offset into original Markdown
  source_offset_end INTEGER NOT NULL,
  text TEXT NOT NULL,                            -- denormalised cache
  lead_token_type TEXT NOT NULL,                 -- heading | paragraph | list_item | code | blockquote | table
  lead_heading_level INTEGER,                    -- 1-6 or NULL
  section_id INTEGER,
  estimated_read_seconds REAL NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
  FOREIGN KEY(section_id) REFERENCES sections(id) ON DELETE SET NULL,
  UNIQUE(document_id, position)
);
CREATE INDEX idx_chunks_doc_pos ON chunks(document_id, position);

-- Reading events (append-only, source of truth for reader actions)
CREATE TABLE reading_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL,
  chunk_id INTEGER,                              -- NULL for open/close
  event_type TEXT NOT NULL,                      -- reveal | conceal | rate_effort | pin_set | pin_clear | open_document | close_document
  payload_json TEXT,                             -- e.g. {"rating":4} or {"color_id":2}
  created_at TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
  FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);
CREATE INDEX idx_events_doc_created ON reading_events(document_id, created_at);

-- Reading state (projection)
CREATE TABLE reading_state (
  document_id INTEGER PRIMARY KEY,
  high_water_position INTEGER NOT NULL DEFAULT 0,
  current_position INTEGER NOT NULL DEFAULT 0,
  last_event_id_applied INTEGER,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- Chunk ratings (projection; latest wins)
CREATE TABLE chunk_ratings (
  chunk_id INTEGER PRIMARY KEY,
  rating INTEGER NOT NULL,                       -- 1-5
  updated_at TEXT NOT NULL,
  FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);

-- Pins (canonical; spans designed for future word-level selection)
CREATE TABLE pins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL,
  chunk_id_start INTEGER NOT NULL,
  word_start INTEGER NOT NULL DEFAULT 0,
  chunk_id_end INTEGER NOT NULL,
  word_end INTEGER NOT NULL DEFAULT -1,
  color_id INTEGER NOT NULL,                     -- 1-5
  created_at TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
  FOREIGN KEY(chunk_id_start) REFERENCES chunks(id) ON DELETE CASCADE,
  FOREIGN KEY(chunk_id_end) REFERENCES chunks(id) ON DELETE CASCADE
);
CREATE INDEX idx_pins_doc_color ON pins(document_id, color_id);

-- Pin colour labels (global, optional)
CREATE TABLE pin_color_labels (
  color_id INTEGER PRIMARY KEY,                  -- 1-5
  label TEXT
);

-- Settings (single-row global config)
CREATE TABLE settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  config_json TEXT NOT NULL
);
```

---

## 22. Routes

```
# Browser views
GET   /                            → redirect to /library
GET   /library                     → library page
GET   /upload                      → upload form
POST  /upload                      → ingest .md; parse synchronously; redirect

GET   /documents/{id}/reader       → reader page

# Reader actions (HTMX → HTML fragments)
POST  /documents/{id}/reveal       → advance current_position
POST  /documents/{id}/conceal      → retreat one chunk
POST  /documents/{id}/rate         → {chunk_id, rating}
POST  /documents/{id}/pin          → {chunk_id, action: cycle|clear}
POST  /documents/{id}/jump-to-pin  → {direction: next|prev, color_id?}
POST  /documents/{id}/return       → return to pre-jump position
GET   /documents/{id}/version      → tiny JSON {version} for 2s poll-sync

# Document management
POST  /documents/{id}/rename       → {title}; HTMX OOB swap
POST  /documents/{id}/delete       → hard delete; redirect to /library
POST  /documents/{id}/retry-parse  → re-parse; redirect

# Settings & help
GET   /settings                    → settings panel overlay
POST  /settings                    → update config_json
GET   /cheatsheet                  → keyboard cheat-sheet overlay
POST  /pin-labels                  → {color_id, label}

# Reserved (post-MVP)
GET   /documents/{id}/ask          → placeholder "coming soon"

# Admin
POST  /admin/rebuild-projections   → projection rebuild (CLI also: parsem rebuild-projections)
```

---

## 23. Authentication

Single user, self-hosted. Basic HTTP auth via env vars, off by default in dev.

```text
PARSEM_REQUIRE_AUTH=true      # enable HTTP Basic
PARSEM_USER=<username>
PARSEM_PASS=<password>
```

No sign-up, no user table, no password reset, no sessions. The browser handles credential prompting. The README documents the setup prominently to prevent the *"I bound to 0.0.0.0 by accident"* footgun.

---

## 24. UI Design Principles

- **Minimal.** Calm, sparse, text-first. No dashboards, no complex panels in MVP.
- **Keyboard-first.** Keyboard interaction is the source of truth; mouse mirrors.
- **Productive friction.** The bucket regen creates the deliberate beat. Operations otherwise stay out of the way of deep reading.
- **No false cleverness.** If structure is weak (e.g., a doc with no headings), treat as flow. Don't synthesise structure.
- **Source-text integrity.** Whitespace and formatting may be cleaned, but the original `.md` is preserved at `data/originals/{id}.md`. Future LLM output remains separate from document text.
- **Reading chamber, not PDF viewer.** Paper-like background, narrow column, comfortable typography.

---

## 25. MVP Build Plan

### Phase 1 — Reading mechanic prototype

Goal: prove the mechanic with a single hardcoded Markdown doc loaded from disk. No DB, no upload.

Build:

- `domain/chunking.py` (10s rule, sentence boundaries via `pysbd`, heading absorption, list/code/blockquote/table rules)
- `domain/bucket.py` (pure-function token computation)
- Reader screen: windowed view K=5, left gutter pin dots, right gutter reserved
- Keyboard: Space, Backspace, P (cycle 5 colours), 1–5, `]` `[`, Shift+Up, `'`, Esc
- Bucket countdown UX, fresh-session credit, empty-bucket prompt
- Section-boundary window clear, sticky heading banner

### Phase 2 — Library, Markdown ingestion, persistence

Build:

- SQLite schema + migrations
- `store/` modules: events writer, projection rebuilder, query helpers
- Library screen (titles, status, progress %, heatmap strip, last-opened ordering)
- Upload screen
- Event log: `reveal`, `conceal`, `rate_effort`, `pin_set`, `pin_clear`, `open_document`, `close_document`
- Projections: `reading_state`, `chunk_ratings`, `pins` (canonical)
- Resume at `high_water − warm_chunks`
- Document delete (cascade), rename
- Failed-parse retry/delete UI
- Multi-tab polling sync

### Phase 3 — Polish, heatmap, onboarding

Build:

- Effort heatmap visualisation in the reader (subtle horizontal bars per chunk)
- Pin colour labels panel (`:` key)
- First-run onboarding: a hardcoded `welcome.md` (~30–50 chunks) walks the reader through actions in narrative form. The welcome doc is itself the tutorial.
- Settings panel (`,` key)
- Cheat-sheet overlay (`?` key)
- `?` key shows placeholder Ask panel (separate trigger from cheatsheet — the spec resolves this as: `?` cheatsheet always wins; Ask is reachable from the empty-bucket prompt's "Ask" affordance)
- Per-document `preference_overrides_json` column added (UI exposure deferred)

---

## 26. Future Features (post-MVP, in priority order)

1. **Highlight layers + word-level selection.** `selections`, `highlights`, `layers` tables. System-derived layers (nouns, verbs, obligation words). User-defined layers via highlight key.
2. **Sidebar expansion: notes + chunk-grounded LLM Q&A.** Right gutter expands, `?` opens chunk-anchored Q&A panel, notes per chunk via `n` key. Local LLM via Ollama.
3. **Reading Weight v2.** Trained on real effort-rating data, off by default, optional pre-bias display.
4. **Per-document preference overrides exposed in UI.**
5. **Cross-document pin/highlight queries.** *"Show me all blue pins across my library."*
6. **Document export.** Reading-state, ratings, pins, notes for a doc as a JSON bundle.
7. **Document import via paste / URL / API.**
8. **Image support, table support, OCR, Pandoc PDF.**

---

## 27. One-Sentence Definition

**Parsem is a self-hosted Markdown-first deep-reading app that reveals documents one chunk at a time, paces the reader through a per-document reveal-token bucket, and lets the reader build their own semantic taxonomy of each document via colour-coded pins and a 1–5 effort heatmap.**
