# Parsem Project Specification

> **Version:** Post-grill final, 2026-05-07. Reconciled with Phase 1+2 implementation 2026-05-09 (Parsem-ngz).
> **Predecessor:** `parsem-spec-draft.md` (preserved for diffing).
> **Scope:** Self-hosted, single-user, local-first deep-reading app for Markdown documents.
>
> **Reading order for agents:** sections describe the *shipped* product where Phase 1+2 has landed, and the *aspirational* product elsewhere. Look for `Phase 3`, `not yet implemented`, or roadmap-only callouts. The deliberate deviations from the original 2026-05-07 spec are catalogued in §27.

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
| `]` / `[` | Scroll to next / previous pin (any colour; Parsem-bwz) |
| `}` / `{` | Scroll to next / previous pin of the SAME colour as the current chunk's pin (no-op when current chunk has no pin; Parsem-bwz) |
| Shift+Up  | Toggle review-mode dim (Esc to exit)    |
| `'`       | Return to canonical anchor (current chunk at 70%) |
| `,`       | Open settings panel (Esc to close) — *Phase 3* |
| `:`       | Open pin colour labels (Esc to close) — *Phase 3* |
| `?`       | Show cheat-sheet overlay (Esc to close) — *Phase 3* |
| Esc       | Close panel / focus mode                |

### 8.1 Return-first rule

Action keys (Space, Backspace, 1–5, P) are bound to the **active chunk** — the chunk at `current_position`. The active chunk is at its **canonical position** when its bottom edge is within ±20px of 70% of the reading viewport's height (see §9.5); outside that band, the reader is considered scrolled away. When the reader has scrolled away (e.g. they've scrolled back to look at earlier text), the first press of any action key is interpreted as *"bring me back"*: the viewport smooth-scrolls the active chunk to its canonical position and no other action runs. The second press performs the action.

This makes Space self-teaching: a reader who scrolled back and presses Space sees the system gather them back rather than steal them forward, and on the next press the reveal happens at the active position. The rule is uniform across all action keys — there is no special-case behaviour per key.

The rule applies only to action keys. Manual scroll (mouse wheel, Page Down) is sovereign and never auto-corrected.

### 8.2 Space-resume rule

When `current_position < high_water_position` — the reader has clicked a settled chunk (§8a.2), or pin-jumped, or backspace-concealed — the *first* press of Space jumps `current_position` to `high_water_position` for free, without spending a token. The *next* press attempts a paid reveal at `high_water_position + 1` as normal.

Read the user's literal phrasing *"space takes me to the next reveal"* as *"space takes me to the place where the next reveal will happen"* rather than *"space spends a token immediately."* Resume-then-reveal is two presses; one press never spends a token from a back-scrolled position. Composes with `'`/Esc (return-first, §8.1) and with `'` (return-to-pre-jump, §13.4).

This rule is enforced in JS: the keyboard layer reads `current_position` and `high_water_position` from the `#reader-main` data attributes the server stamps on each render, and routes Space accordingly. The `/reveal` server endpoint is unchanged — when JS intercepts the first press of Space-while-behind it issues a `POST /set-current-position` (§8a.2) with `position = high_water_position` instead.

---

## 8a. Pointer Interactions

Keyboard is the source of truth (§8). Pointer is the **review surface**: it lets the reader scrub backward, focus on prior text, and navigate among landmarks, but it cannot advance the reading. This section is the design contract for every individual mouse / touch surface in the reader, written before any of those surfaces ship so the surfaces stay coherent rather than accreting one click handler at a time (claude-axx.4).

### 8a.1 Principle

> **Pointer is for review *and* parallel-to-keyboard advance, but never bypasses friction. Forward motion costs a token whether the trigger is Space or the inline reveal symbol (§8a.4); the blurred preview remains preparation, not a target. Backward motion is free, as on the keyboard.**

Four corollaries:

- **Backward is free, forward costs a token.** The reading economy is the same whichever input drives it. Pointer doesn't introduce a discount.
- **Aim — not input mode — preserves friction.** Both Space and the reveal symbol are small, deliberate targets. A 720px-wide blurred preview is a billboard; that's why it is *not* a click target. Pointer doesn't route around the friction; it offers a parallel surface that costs the same.
- **Backward pointer navigation is undoable.** Every backward pointer move (chunk body click, progress-bar click) captures `pre_jump_position` so `'` (return) / Esc reach the reader's last keyboard-anchored position.
- **Forward advance lands a new frontier, just like Space.** Clicking the reveal symbol mutates `high_water_position` and writes a `reveal` event (§18) — the same as keyboard reveal. There is no "soft advance via pointer."

These imply two hard splits in pointer semantics. First, between clicking a chunk-shaped thing for review vs. clicking the inline symbol for advance:

| Surface              | Effect on `current_position` | Cost  | Why                                                                                                                              |
|----------------------|-------------------------------|-------|---------------------------------------------------------------------------------------------------------------------------------|
| Pin dot (left gutter) | **No change** — scroll only   | Free  | Pins are landmarks. Jumping to a landmark is a viewing action, not a commitment to read from there. Matches `]` / `[` / `}` / `{` (§13.4). |
| Chunk body (back-scrub) | **Sets `current_position` backward** | Free  | The reader is saying *"this is my place now."* Subsequent rate / pin / Space act on this chunk. Captured in `pre_jump_position` so a single keystroke restores the frontier. |
| Reveal symbol (inline, end of current chunk) | **Advances `current_position` and `high_water_position` by 1** | **1 token** | The pointer-mode peer of Space — small target, deliberate aim, same cost, same event log. |

Second, the blurred preview gutter is **not** a click target. The preview is preparation (§9.5); making it clickable would rewrite its meaning into "next button" and the eye would start scanning it for affordance instead of absorbing what's coming. Clicks on the preview are swallowed — the reader who wants to advance with the mouse uses the inline symbol.

### 8a.2 Surfaces

Each row is a future implementation bead. Every surface obeys the principle above.

| Surface                                  | On click                                                                                                                                                              | Token   | Status / bead                |
|------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------|------------------------------|
| **Reveal symbol** (inline, end of current chunk's last line) | Same code path as Space: `POST /reveal`. Bucket has tokens → advance one chunk, write a `reveal` event. Bucket empty → trigger the §12.5 rejection motion, no advance. | **1**   | reveal-symbol (next, see §8a.4) |
| Chunk body (`.chunk` body, behind frontier) | Set `current_position := chunk.position`. Capture `pre_jump_position` if currently null. No-op when `position >= high_water_position`. No event log entry.            | Free    | claude-axx.3 (next)          |
| Pin dot (left gutter, settled)           | Smooth-scroll the chunk into the canonical 70% anchor. No state change. No event.                                                                                      | Free    | claude-axx.4-pindot (future) |
| Section heading sticky line (top bar)    | Smooth-scroll the section's first chunk into view. No state change.                                                                                                    | Free    | claude-axx.4-section (future)|
| Progress bar (top bar)                   | Click maps to a position; clamps to `[0, high_water_position]`; sets `current_position` and captures `pre_jump_position`. Forward of the frontier is rejected silently.| Free    | claude-axx.4-progress (future)|
| Drag-select inside a chunk               | Begin a future word-level pin span (§13.1). MVP: unbound — drag-select still does native browser text selection, no Parsem behaviour attached.                          | Free    | post-MVP                     |
| Right-click context menu                 | Reserved. Browser default for now.                                                                                                                                     | —       | post-MVP                     |
| Preview gutter (the blurred next chunk) **NOT** clickable | Click is swallowed. The preview is preparation (§9.5), not a button — making the 720px-wide blurred region clickable would defeat the deliberate-aim principle. The reveal symbol exists for pointer-driven advance. | —       | n/a                          |
| Rating digits `1 / 2 / 3 / 4 / 5` (right-gutter vertical stack) | Click rates the current chunk — same code path as the 1–5 keypress (§7.4). No state mutation beyond the rate event, no `current_position` change, no settle. | Free    | claude-axx.3 (live)          |

### 8a.3 Cross-cutting decisions

**`pre_jump_position` capture.** Any pointer surface that mutates `current_position` (chunk body click, progress bar click) writes the *prior* `current_position` to `pre_jump_position` if and only if `pre_jump_position` is null. This composes with the existing `'` / Esc return rule (§8.1, §13.4): one return-keystroke always lands on the last keyboard-anchored position, regardless of how many pointer hops happened in between. Pointer surfaces that only scroll (pin dot, section heading) do **not** touch `pre_jump_position` — the spine has not moved.

**Cursor affordance.** The browser cursor signals what the surface will do:
- `cursor: pointer` on settled chunks behind `high_water_position` (clickable to set-current)
- `cursor: pointer` on pin dots, section heading line, progress bar
- `cursor: default` on the current chunk and the preview gutter (no forward navigation)
- `cursor: text` on chunk body during drag (so native selection still feels normal)

**Selection vs. click disambiguation.** A `click` event with no intervening `mousemove` (or movement < 4px) and `mouseup` within 250ms of `mousedown` is a "set-current" click. Anything longer or with movement is a text selection — Parsem does nothing, the browser keeps its native selection. This matters for the future word-level pin span (post-MVP); for now it just keeps copy/paste from feeling broken.

**Return-first does not apply to pointer.** §8.1's return-first rule fires when an *action key* is pressed while the reader is scrolled away — to prevent accidental reveal at the wrong place. A pointer click is itself an explicit attention signal: the user has aimed at a target. Pointer clicks therefore execute their action regardless of scroll position. Manual scroll remains sovereign (§8.1 final clause).

**Touch parity.** Tap == click. Long-press, two-finger gestures, and pinch-zoom are out of scope; the browser default applies.

**Mobile / small-screen.** Phone breakpoint UX is its own design problem and is explicitly not covered here. The principle above will hold; the surfaces will not be a 1:1 port.

**No event-log entries for pointer navigation.** Pointer surfaces that only scroll, and pointer surfaces that only move `current_position` backward, do not write to the event log (§18). They change view, not history. Only token-spending and rating / pin actions log events. This keeps event-log replay deterministic — replaying all events of a session reproduces the reader's *reading*, not their *eye movement*.

**Reverse migration.** If a future bead decides any of these surfaces should change the *cost* of any pointer surface (e.g. make backward navigation cost a token, or add a second forward-advance surface), that change overrides §8a.1 and must edit this section first. The principle is the gate.

### 8a.4 Reveal symbol

The reveal symbol is the pointer-mode peer of Space. It is intentionally small and intentionally placed inline so the reader's eye encounters it at exactly the moment they finish the current chunk.

**Placement.** A small glyph appears immediately after the last character of the current chunk's last line, in the text flow itself — *not* on a separate line, not in a gutter. Whitespace between the last word and the symbol is one rendered space. When the chunk's last block is a list / code block / table / blockquote / horizontal rule, the symbol still hangs off the chunk's natural reading endpoint (the last character of the last visible text line of that block). For a horizontal-rule chunk (which has no prose text), the symbol renders centred on the rule line.

**Glyph.** Use a single right-pointing glyph. Defaults: `»` or `▸`. The glyph is muted by default (~50% of body ink) so it disappears into the typography for keyboard-only readers; it brightens to body ink on `:hover`. The exact glyph is an implementation choice, not a spec commitment — but it is a single character, never a button-shaped element.

**Visibility states.**

| Bucket state | Symbol appearance                                  | On click                                              |
|--------------|-----------------------------------------------------|-------------------------------------------------------|
| Has tokens   | Muted ink at rest; body ink on hover; `cursor: pointer` | `POST /reveal` — same handler as Space (§7.1, §12.3)  |
| Empty bucket | Ghosted (~25% ink), no hover brighten, `cursor: not-allowed` | Triggers the §12.5 rejection motion (column shake + amber pulse) and the empty-bucket pictograph; does not advance. The token pictograph is the canonical empty-bucket signal — the symbol echoes it locally. |
| End of document (no next chunk) | Hidden entirely | n/a — reading is over; no advance affordance |

**Why the symbol echoes the keyboard exactly.** Click and Space share the same server endpoint, the same event log entry, the same rejection motion, the same warm-restore semantics. The reader's mental model is *"there are two ways to commit a reveal, and they cost the same."* That symmetry keeps the keyboard authoritative (§8) — the symbol is just a second instrument playing the same note.

**No advance preview by hover.** Hovering the symbol does *not* lift the preview's blur. Lifting blur is the consequence of *committing* a reveal, not of intending to. This preserves the preview's "preparation, not tease" meaning.

**Discoverability.** The symbol is the only on-screen affordance signalling that pointer-driven advance exists. Tooltips and onboarding labels are out of scope here; if discoverability proves a problem in UAT, a one-time tooltip can land in a follow-up.

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

The reader screen is structured into a **persistent top bar**, a **scrolling reading area**, and three vertical regions inside that area:

```
[ ─── top bar: title · progress · token pictograph ─── ]   ← persistent, outside scroll

[ left gutter (~16px) ]  [ main reading column (max 720px) ]  [ right gutter (~28px) ]
   pin colour dots          windowed view + current chunk       rating prompt 1/2/3/4/5
                                                                (vertical stack, current chunk only)
```

The **top bar** carries global session context — document title, progress (a fraction `current+1 / total` plus a thin progress bar underneath), and the token pictograph (see §12.5). It sits outside the scroll context so it never moves while the reader scrolls. The current section's heading collapses into the top bar (as a secondary line under the title) once the reader has scrolled past the first H2; above the first H2, only the document title shows.

The **reading viewport** is the scrollable element that contains the main column and excludes the top bar — typically a `<div class="reader-scroll">` wrapping the partial fragment. The main column is anchored within the reading viewport so that the **current chunk's bottom edge sits at ~70% of the viewport's height**. The bottom 30% is a **preview gutter**: it renders the next chunk in a blurred, slightly faded state (~5px blur, opacity ~0.6 with a subtle continuous pulse). The preview is preparation, not a tease — it lets the eye anticipate what is coming without permitting the reader to actually read ahead. On reveal, the preview's blur lifts and its opacity climbs while the column smooth-scrolls upward to bring the new current chunk to the same 70% anchor.

Above the current chunk, the main column shows the **full revealed history** as rendered HTML (Parsem-kli growing-document model — see §15). All settled chunks remain visible; the current chunk is marked with a 2px left-border accent in the left gutter.

The **left gutter** (~16px) shows pin colour dots aligned with each chunk.

The **right gutter** (~28px) carries the rating prompt — a vertical `1 / 2 / 3 / 4 / 5` stack rendered alongside the bottom edge of the current chunk. It is muted at rest (~40% opacity), brightens on hover, and tracks the current chunk wherever it goes (frontier, click-back, conceal-back). The digits are clickable as a pointer-mode peer of the 1–5 keypress (§8a.1) — free, never advancing. The earlier inline-below-chunk placement (claude-axx.3 first cut) was moved into the gutter on UAT feedback: the inline form interrupted the reading flow when the cursor was mid-document. The right gutter remains reserved for further expansion (notes, chunk Q&A); rating is the first occupant.

The window **clears** at every heading — when the reader crosses into a new section, the prior section's chunks vanish from the visible window and the new section's heading becomes the top bar's section line. Backward navigation across the boundary repopulates the prior section's window.

---

## 10. Document Model

The document model is a deterministic substrate (claude-axx, *AtomicChunkingPhase1.md*):

```text
Uploaded Markdown
  → DocumentRevision        (immutable, hashed, line-indexed)
  → ParsedBlock[]           (markdown-it tokens with offsets)
  → AtomicPiece[]           (smallest legal source-faithful unit)
  → PreprocessedPiece[]     (deterministic metrics + flags)
  → ChunkPlan               (planning over piece IDs)
  → ChunkRecord[]           (the reveal units the reader sees)
  → SectionRecord[]         (heading-bounded grouping)
```

`DocumentRevision` is the canonical text (one immutable row per ingest pass; `content_hash` is sha-256 over UTF-8 bytes; `line_index_json` caches line-start offsets for fast `(line, column)` lookup). Every derived record traces back to a revision; nothing is materialised from uploaded temp files after the revision is created.

`AtomicPiece` is the smallest legal unit a deterministic strategy may place in a chunk. Phase 1 piece kinds: `heading`, `sentence`, `paragraph`, `code_block`, `list_item`, `list_run`, `blockquote`, `table`. Each piece carries source offsets, line/column spans, a `text_hash` over its slice, and a `text_snapshot` (debug/test convenience; canonical text remains the revision slice).

A `ChunkingRun` is provenance: `(strategy_name, strategy_version, rules_hash)` over a revision. Changing any rule produces a new run, never a mutation of an old run's meaning. Phase 1 ships one strategy (`current_reading_time` v1.0.0); the substrate is intentionally over-built to admit additional strategies (structural, speed, concept_learning, …) without further schema change.

A `ChunkRecord` is the reveal unit the reader sees. Phase 1 chunks are **contiguous** source spans: `text == revision.full_text[source_offset_start:source_offset_end]`, validated by `text_hash`. A chunk references the pieces it contains via the `chunk_pieces` junction. A chunk has:

- A position (0-indexed, contiguous within a chunking run)
- Source-Markdown byte range (`source_offset_start`, `source_offset_end`) and line/column spans
- Denormalised chunk text (cached for fast read; re-derivable from revision + offsets) plus its `text_hash`
- A `lead_token_type` (`heading` | `paragraph` | `list_item` | `code` | `blockquote` | `table`) — derived from the first piece's kind via a fixed map (sentence → paragraph; list_run → list_item; code_block → code) so reader templates dispatch unchanged
- An optional `lead_heading_level` (1–6) when the chunk's lead piece is a heading
- An `estimated_read_seconds` value (sum over piece read-times under the run's `ReadingRules`)
- A `section_id` linking it to the section it belongs to

`SectionRecord` is the heading-bounded grouping: a heading chunk starts a new section; chunks before the first heading form a prologue (heading_chunk_id NULL). Section ranges are inclusive over chunk positions.

Pin and rating durability across re-chunks is piece-set based (claude-z99, deferred): a chunk's pieces are the stable anchor for re-anchoring, falling back to source-offset overlap when atomic rules change. Phase 1 is fresh-data only, so the primitive is documented but not exercised.

---

## 11. Chunking Rule

Phase 1 chunking runs through a strategy-driven substrate (claude-axx, *AtomicChunkingPhase1.md*):

```text
ChunkingStrategy.plan(preprocessed: PreprocessedPiece[], rules: ChunkingRuleset) → ChunkPlan
materialize(plan, revision, pieces, rules) → ChunkRecord[]
```

The default strategy is `current_reading_time` (v1.0.0), which reproduces the historical chunker behaviour on the new substrate. A `ChunkingRuleset` packages four rule groups:

- `AtomicRules` — atomicity decisions used *before* planning: `paragraph_atomicity` (sentence | paragraph), `list_atomicity` (item | run), code/table/blockquote atomic at block grain.
- `ReadingRules` — `prose_wpm`, `code_wpm`, `budget_seconds`, `heading_cost` (normal | zero), `wpm_user_scaling`.
- `StructuralRules` — `heading_attachment`, `code_handling`, `list_handling`, `list_lead_in` (none | colon_previous_paragraph), `table_handling`, `blockquote_handling`.
- `MaterializationRules` — `require_contiguous_chunks` (Phase 1 always true), `preserve_source_text_when_contiguous`.

The strategy is **never** allowed to invent text or source offsets. It emits `PlannedChunk[]` over piece ordinals; final text comes from `revision.full_text[start:end]` and is hash-validated.

### 11.1 The budget rule

A chunk is filled greedily with whole sentences until the next sentence would exceed the **`chunking.budget_seconds`** budget at the configured WPM. Round down — never split a sentence.

Sentences pack **across paragraph boundaries** within a run of consecutive paragraph blocks. Two short paragraphs that together fit the budget produce one chunk; the rendered text joins them with a paragraph break (`\n\n`) so the markdown renderer treats them as adjacent `<p>` elements. A non-paragraph block (heading, list, code, blockquote, table) ends the run.

> Default is **30s** at 220 wpm ≈ 110 words ≈ comfortably one or two paragraphs (Parsem-ew8). The earlier 10s default produced sub-paragraph chunks that read like Powerpoint.

### 11.2 Heading absorption

A heading chunk **absorbs forward** sentences from the body following it, up to the budget, OR until the next heading hits — whichever comes first. A bare heading immediately followed by another heading becomes a heading-only chunk. A heading at end-of-document becomes a heading-only chunk.

### 11.3 Structural blocks

- **Code blocks** are one chunk regardless of length when `chunking.code_handling = block`. Read time is estimated at the `read_wpm_code` rate (slower than prose). Token cost stays 1. When `code_handling = prose`, code is sentence-split and packed like prose.
- **Lists**: each item is one chunk when `chunking.list_handling = item`. When `block` (default — Parsem-ew8), the whole list is one chunk; when `prose`, list items are joined and packed like prose.
- **Blockquotes** are one chunk regardless of length.
- **Tables** are one chunk regardless of length.
- **Horizontal rules**, **image syntax**, blank lines are not chunked (skipped during chunking).
- **Colon-terminated lead-in absorption** (Parsem-5lx): when a paragraph chunk's trimmed text ends with `:` AND the next blocks form a `list_item` run, the paragraph is absorbed into the merged list chunk (text prepended, `lead_token_type` stays `list_item`). The lead-in and the enumeration it introduces read as one unit. Behind `chunking.absorb_colon_lead_in` (default `true`).

### 11.4 Reading time estimation

`estimated_read_seconds = words_in_chunk / (read_wpm × wpm_user_scaling) × 60`, with `read_wpm` selected per content type (`prose` or `code`).

### 11.5 Sentence detection

`pysbd` (Python Sentence Boundary Disambiguation). Pure Python, no model downloads, handles abbreviations and typical edge cases.

### 11.6 Re-chunking

A change to any rule (atomic, reading, structural, materialisation) — or to the strategy name/version — produces a **new ChunkingRun**, never a mutation of the old run's meaning. Old chunks remain in the database (provenance + audit) until garbage-collected. The reader always reads the latest run for a document; old runs are inert.

Pin and rating re-anchoring across runs is piece-set based:

- **Within unchanged atomic rules** — pieces are byte-identical across runs; chunks are different groupings of the same piece IDs. A pin/rating's owning chunk is identified by piece-set Jaccard against the new run's chunks.
- **When atomic rules change** — pieces themselves change. Fall back to source-offset overlap: a piece's `(start_offset, end_offset)` finds the new piece(s) covering that range.

Phase 1 is fresh-data only (no existing pins/ratings to re-anchor on first ship); claude-z99 and claude-jtu cover re-anchoring beyond Phase 1. Existing reading events still reference old chunk ids; projection rebuild can re-anchor by source overlap as before.

---

## 12. Reading Economy

The economy paces advancement through a **per-document** token bucket.

### 12.1 Tokens, capacity, and regen

- `bucket.capacity` is **fixed at 5**. It is not exposed in the settings UI. Pace tuning happens via WPM scalers and `regen_seconds`, never via capacity. The cap is opinionated — five is the upper limit of glanceable subitization, and a higher cap would weaken the deliberate-friction thesis (§2). The valve has a fixed throat; only the regen rate moves.
- `bucket.regen_seconds` (default 6; the user's pace knob) is the regen interval. Earlier 12s default jammed too easily for natural reading pace at 30s prose budgets — UAT (claude-axx) showed the bucket draining faster than it filled.
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

### 12.5 Empty-bucket UX — token pictograph and rejection motion

The bucket's state is communicated through two channels, both visual and quiet, neither textual.

**Token pictograph (top bar, always visible).** Five dots fixed: `●` filled = available, `○` open = empty. The next-to-fill dot fades in (opacity 0 → 1 over `regen_seconds`), giving a peripheral-vision cue for "next token in roughly N seconds." Once full, all dots are `●` and the fade animation pauses.

The pictograph is the only persistent answer to *"when can I reveal next."* There is no ticking text countdown.

**Rejection motion (in the reading area).** When the reader presses Space at the active position with an empty bucket, the system responds in motion, not in words. Timings below are starting points, tuned for feel; an implementation may adjust within ±50ms per phase:

1. The current chunk and the blurred preview translate upward by ~one chunk-height as if advancing (~250ms).
2. They hold at the advanced position; the current chunk's left-border accent briefly thickens and shifts to a soft amber (~150ms).
3. They translate back to rest (~250ms).

The whole sequence is ~650ms of soft motion. The reader sees that the system received the keystroke and that the answer is "wait." There is no banner, no flash, no chrome — *the rejection is felt in the body of the page itself*.

If the reader pressed Space while scrolled away from the active position, the return-first rule (§8.1) handles the keystroke and the rejection motion does not run. The reader must be at the gate to be told the gate is closed.

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

Pin navigation under Parsem-bwz is **pure client-side scroll** — it does not advance `current_position`, does not consume tokens, does not send any POST. Pinned chunks stay visible in the growing-document model (§15), so jumping is just smooth-scrolling to a pin's chunk element.

- `]` / `[` — scroll to the next / previous pin (any colour). No wrap-at-ends: `]` past the last pin and `[` before the first pin are both no-ops. Wrap-around behaviour was tried first and read as direction-inversion in UAT (claude-axx.3) — readers expect arrow-keys-style "stop at the wall."
- `}` / `{` — scroll to the next / previous pin of the **same colour as the current chunk's pin**; if the current chunk has no pin, the keys are a deliberate no-op (rather than falling back to last-active-colour state)

The legacy `POST /documents/{id}/jump-to-pin` and `POST /documents/{id}/return` routes from §22 still exist for tests and tooling, but are not reached by the keyboard.

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
- Re-rating prior chunks (a chunk other than the current one) is deferred. In Phase 1 the return-first rule (§8.1) routes any 1–5 press while scrolled away into a return-scroll, after which the second press rates the active chunk. A future bead introduces explicit "rate the chunk under cursor" semantics for backward navigation.

### 14.3 Display

A subtle horizontal bar at the bottom of each chunk in the main column. Library view shows a small heatmap strip per document.

Diverging palette: red (5) → amber (4) → neutral grey (3) → light blue-grey (2) → light blue (1). Unrated chunks render blank.

---

## 15. Visual Frame

The reading surface is a **growing rendered document** (Parsem-kli). Every revealed chunk stays in the DOM with its source markdown rendered to HTML via `markdown-it-py`; the reader can scroll back through everything they have read in this document. The current chunk is marked with a 2px left-border accent in the gutter; settled chunks render at full opacity (their footprint is the visual reading-history). The chunker still controls when content reveals; the renderer just keeps it visible.

> The earlier draft of this section described a windowed view with `view.window_k = 5` — settled chunks fading at 70% opacity and scrolling off as the reader advanced. That was Phase 1 prototype behaviour. Parsem-kli replaced it because (a) markdown rendering produces structured HTML that wants to be readable, not faded, and (b) being able to scroll back through what you've read is more honest to "deep reading" than artificial fade-out.

### 15.1 Section boundaries

The current section's heading **collapses into the top bar** as a secondary line under the document title once the reader has scrolled past the first H2. Above the first H2, only the document title shows. There is **no window-clear** at heading boundaries under the growing-document model — section transitions are visible in the rendered HTML structure (heading elements, spacing).

### 15.2 Backward review

`Shift+Up` toggles **review mode** (a body class). Under the windowed-view spec this expanded K; under the growing-document model the entire revealed history is already visible, so review mode is a softer affordance — it dims the visual differentiation between current and settled (`.chunk--settled` opacity tweak) so the reader can scan history without the current-chunk accent guiding their eye. `Esc` exits review mode. Pins can be created and ratings can be recorded while in review mode; the return-first rule (§8.1) still applies to action keys.

### 15.3 Presentation

Configurable:

- **Background**: `paper` (default — `#FAF7F0`), `sepia` (`#F4ECD8`), `dark` (`#1A1A1A`)
- **Prose font**: Charter (default), Georgia, Lora, Inter
- **Code font**: JetBrains Mono (default), Fira Code, IBM Plex Mono, system monospace
- **Font size**: 18px default, range 14–24
- **Density**: `compact` (line-height 1.4), `normal` (1.6, default), `spacious` (1.85)
- **Max column width**: 720px (≈65 chars at 18px Charter)

Presentation prefs live in browser localStorage (single-machine, single-user). Server has nothing to know.

### 15.4 Character

The reader's character has two axes that should never collapse into each other.

**Visual / motion axis: smooth, graceful, gentle.** Motion communicates state — the empty-bucket rejection is a soft pretend-advance with an amber pulse, not a banner. Surfaces are soft (paper background, subtle borders, blurred preview that reads as *forming* rather than *withheld*). Feedback is felt more than seen — peripheral cues over centre-of-attention chrome.

**Behaviour / boundary axis: firm and bounded.** The token bucket is a valve, not a game. Tokens are not earned through actions (no rate-to-earn, no conceal-to-refund); they regenerate on time alone. The UI must never read as a score: the pictograph is fixed at five dots, no counters, no streaks, no badges. Capacity is not user-configurable — only the WPM scalers and regen interval are. Defaults are opinionated.

The two axes work together: Parsem is gentle in *how* it communicates and firm about *what* it allows. *"You came to do something, and this is how we do it here. Other places may be different."* When in doubt about a feature, ask whether it would belong in a meditation room. If yes, it fits. If it would belong in a productivity app or a video game, it does not.

---

## 16. Multi-Tab and Multi-Window

Multiple tabs or windows on the same document are **allowed**. The event log stays consistent because all events are append-only and timestamped, regardless of which tab wrote them.

### 16.1 Auto-sync via polling

Each open tab/window polls `/documents/{id}/version` every `view.sync_interval_seconds` (default 2s). When the server's max event timestamp for the document changes, the tab re-fetches the rendered reader fragment and swaps it in. All tabs stay within ~2s of truth.

The bucket is server-authoritative. If two tabs both attempt to advance at near-simultaneous moments, the second sees the rejection motion (§12.5) rather than double-spending.

### 16.2 Cross-browser limitation

Two different browsers (e.g. Chrome and Firefox) cannot detect each other client-side. They will both poll independently and stay eventually-consistent through the server. Documented as known MVP behaviour.

---

## 17. Ingestion

### 17.1 Markdown only

Upload a `.md` file. The pipeline runs synchronously:

```text
Upload .md
  → store original at data/originals/{doc_id}.md            (debug archive)
  → INSERT documents row (status=processing)
  → INSERT document_revisions (immutable, hashed, line-indexed)
  → parse Markdown into ParsedBlock[] (markdown-it tokens + source offsets)
  → build_atomic_pieces(blocks, AtomicRules)  →  AtomicPiece[]
  → preprocess_pieces(pieces, ReadingRules)   →  PreprocessedPiece[]
  → ChunkingStrategy.plan(preprocessed, rules)→  ChunkPlan
  → materialize(plan, revision, pieces, rules)→  ChunkRecord[]
  → derive_sections(chunks)                    →  SectionRecord[]
  → INSERT chunking_runs + atomic_pieces + chunks + chunk_pieces + sections
  → mark document `ready` (or `failed` with reason)
```

All inserts happen inside one transaction — a half-substrate is never persisted. Validation gates run between phases (`validate_pieces`, `validate_chunk_plan`); failures mark the document `failed`.

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
- **Templates**: Jinja2. POST routes return partial fragments (§22). Client-side swaps are driven by vanilla `fetch` + `outerHTML` replacement; HTMX is intentionally not in the stack ("explicit beats magical").
- **Client JS**: ~50–150 lines, vanilla. Keyboard handling, smooth-scroll, return-first rule (§8.1), token pictograph regen animation, rejection motion (§12.5), polling sync, pin cycle navigation.
- **Database**: SQLite with `journal_mode=WAL`, `foreign_keys=ON`, `synchronous=NORMAL`.
- **Markdown parser**: `markdown-it-py` (token stream output, source offsets, plug-in friendly).
- **Sentence detection**: `pysbd`.

Local LLM (post-MVP) options: Ollama, LM Studio local server, OpenAI-compatible local endpoint.

---

## 20. Configuration

Configuration lives in a single `settings` row (`config_json` blob) and is mirrored into the editing UI via the `,` settings panel. Per-document overrides live in `documents.preference_overrides_json` (NULL = use global; UI exposure deferred post-MVP).

Some values are deliberately **not user-configurable**, even though they are stored alongside the rest. `bucket.capacity` is one — see §12.1 for rationale.

```yaml
chunking:
  budget_seconds: 30          # Parsem-ew8: 30s @ 220wpm ≈ 110 words ≈ paragraph
  read_wpm_prose: 220
  read_wpm_code: 110
  wpm_user_scaling: 1.0       # range 0.5–2.0
  code_handling: block        # block | prose (prose unimplemented)
  list_handling: block        # item | block | prose (Parsem-ew8 default)
  absorb_colon_lead_in: true  # Parsem-5lx: 'Foo:\n\n- a\n- b' becomes one chunk

bucket:
  capacity: 5                 # FIXED — not exposed in the settings UI (§12.1)
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

-- ──────────────────────────────────────────────────────────────────────
-- v2: atomic chunking substrate (claude-axx, AtomicChunkingPhase1.md)
-- ──────────────────────────────────────────────────────────────────────

-- Immutable Markdown revision per ingest pass. Canonical text + sha-256 +
-- cached line index. Every derived record (pieces, plans, chunks, sections)
-- traces back here. Never mutate full_text after creation.
CREATE TABLE document_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL,
  full_text TEXT NOT NULL,
  content_hash TEXT NOT NULL,                    -- sha-256 over UTF-8 bytes
  line_index_json TEXT NOT NULL,                 -- JSON list of line-start offsets
  created_at TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX idx_revisions_doc ON document_revisions(document_id, created_at DESC);

-- Provenance: which deterministic rules produced which chunks. Strategy +
-- version + rules_hash form the identity. Any rule change → new run, never
-- a mutation of an old run's meaning.
CREATE TABLE chunking_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  revision_id INTEGER NOT NULL,
  strategy_name TEXT NOT NULL,                   -- e.g. 'current_reading_time'
  strategy_version TEXT NOT NULL,                -- e.g. '1.0.0'
  rules_hash TEXT NOT NULL,                      -- sha-256 over canonical ruleset JSON
  created_at TEXT NOT NULL,
  FOREIGN KEY(revision_id) REFERENCES document_revisions(id) ON DELETE CASCADE
);
CREATE INDEX idx_runs_revision ON chunking_runs(revision_id, created_at DESC);

-- Smallest legal source-faithful unit a strategy may place into a chunk.
-- Pieces are determined by (revision_id, AtomicRules); same inputs always
-- produce identical pieces. Stable across planning-rule changes; only
-- changes to AtomicRules itself produces new pieces.
CREATE TABLE atomic_pieces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  revision_id INTEGER NOT NULL,
  ordinal INTEGER NOT NULL,                      -- document-order, dense, 0-based
  kind TEXT NOT NULL,                            -- heading|sentence|paragraph|code_block|list_item|list_run|blockquote|table
  source_block_index INTEGER NOT NULL,
  ordinal_in_block INTEGER NOT NULL,
  source_offset_start INTEGER NOT NULL,
  source_offset_end INTEGER NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  start_column INTEGER NOT NULL,
  end_column INTEGER NOT NULL,
  text_hash TEXT NOT NULL,                       -- sha-256 over the slice
  text_snapshot TEXT NOT NULL,                   -- denormalised for debug/test
  heading_level INTEGER,                         -- 1-6 or NULL
  structural_parent_piece_id INTEGER,            -- e.g. sentence → paragraph parent
  FOREIGN KEY(revision_id) REFERENCES document_revisions(id) ON DELETE CASCADE,
  FOREIGN KEY(structural_parent_piece_id) REFERENCES atomic_pieces(id) ON DELETE SET NULL,
  UNIQUE(revision_id, ordinal)
);
CREATE INDEX idx_pieces_revision_ord ON atomic_pieces(revision_id, ordinal);

-- Junction: which pieces compose which chunk. Re-anchor primitive for
-- pins/ratings (claude-z99) — Jaccard over piece-id sets.
CREATE TABLE chunk_pieces (
  chunk_id INTEGER NOT NULL,
  piece_id INTEGER NOT NULL,
  ordinal INTEGER NOT NULL,                      -- order within the chunk
  PRIMARY KEY(chunk_id, ordinal),
  FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE,
  FOREIGN KEY(piece_id) REFERENCES atomic_pieces(id) ON DELETE CASCADE
);
CREATE INDEX idx_chunk_pieces_piece ON chunk_pieces(piece_id);

-- v2 ALTERs on the chunks table — back-references to the run + revision,
-- plus per-chunk hash and line/column anchors. Nullable in SQLite (ALTER
-- ADD COLUMN limitation); the v2 migration wipes existing rows so all
-- new inserts populate every field.
ALTER TABLE chunks ADD COLUMN chunking_run_id INTEGER
    REFERENCES chunking_runs(id) ON DELETE CASCADE;
ALTER TABLE chunks ADD COLUMN revision_id INTEGER
    REFERENCES document_revisions(id) ON DELETE CASCADE;
ALTER TABLE chunks ADD COLUMN text_hash TEXT;
ALTER TABLE chunks ADD COLUMN start_line INTEGER;
ALTER TABLE chunks ADD COLUMN end_line INTEGER;
ALTER TABLE chunks ADD COLUMN start_column INTEGER;
ALTER TABLE chunks ADD COLUMN end_column INTEGER;
CREATE INDEX idx_chunks_run_pos ON chunks(chunking_run_id, position);
```

---

## 22. Routes

GET routes return full HTML pages (`<html>...</html>`). POST routes return only the **`<main id="reader-main">…</main>` partial fragment** — the full page wraps the partial via `{% include %}`, and client-side swaps replace the `<main>` element via `outerHTML`. The top bar lives in the full shell, outside the swap target, so it never reloads on action.

```
# Browser views
GET   /                            → redirect to /library
GET   /library                     → library page
GET   /upload                      → upload form
POST  /upload                      → ingest .md; parse synchronously; redirect

GET   /documents/{id}/reader       → reader page (full HTML)

# Reader actions (return partial fragment, JS swaps #reader-main)
POST  /documents/{id}/reveal       → advance current_position
POST  /documents/{id}/conceal      → retreat one chunk
POST  /documents/{id}/rate         → {chunk_id, rating}
POST  /documents/{id}/pin          → {chunk_id, action: cycle|clear}
POST  /documents/{id}/close        → log close_document event (sendBeacon on pagehide; Parsem-8wj)
GET   /documents/{id}/version      → tiny JSON {version} for 2s poll-sync (not yet implemented — Parsem-2rp)

# LEGACY pin navigation — kept for backward compat, not exercised by the keyboard.
# Pin nav is handled client-side as pure scroll under Parsem-bwz; see §13.4.
POST  /documents/{id}/jump-to-pin  → {direction: next|prev, color_id?}
POST  /documents/{id}/return       → return to pre-jump position

# Document management
POST  /documents/{id}/rename       → {title}; returns the updated library-row fragment
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
- Token pictograph + rejection motion (§12.5), fresh-session credit, return-first rule (§8.1)
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

## 27. Deviations & Decisions Log

Deliberate divergences between this spec and the shipped Phase 1+2 code. Each entry cites the bead that landed it. Order is bead-id, not chronological.

### 27.1 Chunking budget raised from 10s to 30s (Parsem-ew8)

The original 10s default produced sub-paragraph chunks that read as Powerpoint slides. 30s @ 220 wpm ≈ 110 words ≈ comfortably one or two paragraphs. Reflected in §11.1 and §20.

### 27.2 List handling default flipped from `item` to `block` (Parsem-ew8)

Per-item chunks broke list flow visually. Reading a 6-item list as 6 chunks felt wrong. The whole-list-as-one-chunk default reads as one unit. §20 default updated.

### 27.3 Reader is a growing rendered document, not a windowed view (Parsem-kli)

Replaced the K=5 windowed view (current chunk + 4 settled at 70% opacity, scrolling off as reader advances) with a growing rendered document. Every revealed chunk stays in the DOM; markdown source is rendered to HTML via `markdown-it-py`; the reader can scroll back through their full reading history. The current chunk is marked only by a 2px left-border accent in the gutter. §15 fully rewritten; §15.1 (window-clear at heading) and §15.2 (review-mode K-expansion) revised.

### 27.4 Cross-paragraph sentence packing (Parsem-e9t)

Spec §11.1's "fill greedily with whole sentences" was implemented as "within one paragraph block" through Phase 1, producing tiny chunks for documents with short consecutive paragraphs. Parsem-e9t makes packing cross paragraph boundaries within a run; sentences from different blocks join with `\n\n` so the renderer treats them as adjacent `<p>` elements. The spec language was always correct; the implementation now matches.

### 27.5 Colon-terminated lead-in absorption (Parsem-5lx)

A paragraph chunk whose trimmed text ends with `:` followed immediately by a list run is absorbed into the merged list chunk. "This list:\n\n- a\n- b" reads as one chunk instead of orphaning the lead-in. New rule, not in original §11; behind `chunking.absorb_colon_lead_in` (default true).

### 27.6 Pin navigation moved to client-side scroll (Parsem-bwz)

Spec §13.4 had `[`/`]` advance `current_position` to a pin's chunk via `POST /jump-to-pin`. Under the growing-document model (27.3) every revealed chunk is in the DOM, so pin navigation is now pure client-side smooth-scroll — `current_position` is unchanged. New keys `}` / `{` scroll between same-colour pins (no-op when current chunk has no pin). The server routes `POST /jump-to-pin` and `POST /return` remain for tests/tooling but are unreachable from the keyboard.

### 27.7 Lifecycle event POST route (Parsem-8wj)

Spec §18.1 listed `close_document` as an event type but no route to log it. Added `POST /documents/{id}/close` triggered by client `pagehide`/`beforeunload` `sendBeacon`. Returns 204 unconditionally so stale beacons for deleted docs don't surface as errors. `open_document` is logged synchronously inside `GET /documents/{id}/reader`.

### 27.8 Multi-tab polling deferred (Parsem-2rp open)

Spec §16's `GET /documents/{id}/version` + 2s JS poll is not yet implemented. Single-tab assumption holds for now: process-global `app.state.reader` is overwritten when a different doc opens. Multi-tab support is filed as Parsem-2rp.

### 27.9 Settings/cheatsheet/admin routes deferred to Phase 3

§22 routes for `GET/POST /settings`, `GET /cheatsheet`, `POST /pin-labels`, `POST /admin/rebuild-projections` are unimplemented. Phase 3 work per §25.

### 27.10 `POST /pin` body is cycle-only

Spec §22 parameterizes `POST /pin → {chunk_id, action: cycle|clear}`. Code only implements cycle (no body); clear requires 5 P-presses. A 30-line follow-up to land the `clear` action and rebind `Shift+P` is queued.

---

## 28. One-Sentence Definition

**Parsem is a self-hosted Markdown-first deep-reading app that reveals documents one chunk at a time, paces the reader through a per-document reveal-token bucket, and lets the reader build their own semantic taxonomy of each document via colour-coded pins and a 1–5 effort heatmap.**
