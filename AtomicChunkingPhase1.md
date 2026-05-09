# Atomic Chunking Phase 1

## Purpose

This document defines the Phase 1 implementation target for Parsem's next
chunking substrate.

The goal is to replace the current monolithic deterministic chunker with a
source-faithful, immutable, deterministic pipeline that can support:

- current reading-time chunking,
- deterministic structural chunking,
- stable chunk indexes,
- future semantic advisory experiments.

This phase explicitly excludes:

- LLM chunking,
- semantic maps,
- semantic anchors,
- semantic relations,
- cognitive-load scoring,
- recursive or fractal chunking,
- non-contiguous chunks,
- user preference driven strategy presets.

Phase 1 is foundation work. It should make the current behaviour easier to
reason about before adding new behaviour.

## Guiding Principle

```text
Immutable source first.
Deterministic legal pieces second.
Deterministic plans third.
Source-faithful materialization last.
```

The chunker must never invent document text. Final chunk text must be derived
from the owned Markdown revision and deterministic source offsets.

## Phase 1 Pipeline

```text
Uploaded Markdown
  -> DocumentRevision
  -> ParsedBlock[]
  -> AtomicPiece[]
  -> PreprocessedPiece[]
  -> ChunkPlan
  -> ChunkRecord[]
  -> SectionRecord[]
```

Every stage after `DocumentRevision` is derived state. Derived state may be
discarded and rebuilt from the revision plus a versioned deterministic ruleset.

## Core Invariants

1. A `DocumentRevision` is immutable.
2. `DocumentRevision.full_text` is the canonical text.
3. `ParsedBlock`, `AtomicPiece`, `PreprocessedPiece`, `ChunkPlan`, chunks, and
   sections are derived from a revision.
4. `AtomicPiece` is the smallest legal source-faithful unit a deterministic
   strategy may place into a chunk.
5. Phase 1 chunks are contiguous in source order.
6. A chunk may contain one or more atomic pieces.
7. A chunk may not contain partial atomic pieces.
8. A chunk's persisted text is a cache, not the source of truth.
9. Chunk materialization uses source offsets from the owned revision.
10. A change to deterministic rules creates a new chunking result, not a
    mutation of the old result's meaning.

## Durable Versus Derived Records

### Durable Source

`DocumentRevision` is durable and canonical.

```text
type DocumentRevision {
  id: RevisionId
  document_id: DocumentId
  full_text: String
  content_hash: Hash
  line_index: LineIndex
  created_at: Timestamp
}
```

Rules:

- never mutate `full_text`;
- never materialize chunks from uploaded temp files after revision creation;
- never use line numbers as the sole internal anchor;
- compute `content_hash` over exact revision text bytes.

### Derived Build Artifact

`ChunkingRun` records which deterministic rules produced a set of chunks.

```text
type ChunkingRun {
  id: ChunkingRunId
  revision_id: RevisionId
  strategy_name: String
  strategy_version: String
  rules_hash: Hash
  created_at: Timestamp
}
```

This is not a semantic interpretation of the document. It is provenance for
derived records.

### Rebuildable Records

These are rebuildable from `DocumentRevision` plus `ChunkingRun` rules:

- `ParsedBlock`
- `AtomicPiece`
- `PreprocessedPiece`
- `ChunkPlan`
- `ChunkRecord`
- `SectionRecord`

They may be persisted for performance, debugging, indexing, and event
re-anchoring, but they are not canonical source.

## ParsedBlock

`ParsedBlock` is the deterministic parser output for Markdown block structure.

```text
type ParsedBlock {
  id: ParsedBlockId
  revision_id: RevisionId
  ordinal: Int
  kind: BlockKind
  source_offset_start: Int
  source_offset_end: Int
  start_line: Int
  end_line: Int
  heading_level: Int?
}
```

Phase 1 block kinds:

```text
heading
paragraph
code
list_item
blockquote
table
horizontal_rule
image
blank
unknown
```

Rules:

- block offsets must slice valid text from `DocumentRevision.full_text`;
- `horizontal_rule`, `image`, and `blank` are not reveal content in Phase 1;
- `unknown` should fail ingestion unless the existing parser already has a
  deterministic safe handling for it.

## AtomicPiece

`AtomicPiece` is the legal unit for deterministic planning.

It is source-faithful. It does not know about semantic density, user preference,
or LLM interpretation.

```text
type AtomicPiece {
  id: PieceId
  revision_id: RevisionId
  source_block_id: ParsedBlockId
  ordinal: Int
  ordinal_in_block: Int

  kind: PieceKind
  source_offset_start: Int
  source_offset_end: Int
  start_line: Int
  end_line: Int
  start_column: Int
  end_column: Int

  text_hash: Hash
  text_snapshot: String

  heading_level: Int?
  structural_parent_piece_id: PieceId?
}
```

Phase 1 piece kinds:

```text
heading
sentence
paragraph
code_block
list_item
list_run
blockquote
table
```

Rules:

- `PieceId` is stable within a `revision_id + rules_hash` build.
- `ordinal` is the document-order position among pieces.
- `text_snapshot` is a cache for tests, prompts, indexing, and debugging.
- canonical text remains the revision slice.
- `text_hash` validates that the snapshot matches the revision slice.
- `structural_parent_piece_id` is only for deterministic structure, such as a
  sentence belonging to a paragraph piece if paragraph parent tracking is
  useful.

### Atomicity Rules

Atomicity is selected before planning begins.

```text
type AtomicRules {
  paragraph_atomicity: "sentence" | "paragraph"
  code_atomicity: "block"
  table_atomicity: "block"
  blockquote_atomicity: "block"
  list_atomicity: "item" | "run"
}
```

Phase 1 defaults:

```text
paragraph_atomicity: sentence
code_atomicity: block
table_atomicity: block
blockquote_atomicity: block
list_atomicity: run
```

These defaults preserve the current product direction:

- prose can be packed by reading time;
- code, tables, and blockquotes remain whole;
- lists default to one readable unit rather than many tiny reveal units.

## PreprocessedPiece

`PreprocessedPiece` adds deterministic metrics and structural facts to an
atomic piece.

```text
type PreprocessedPiece {
  piece_id: PieceId
  word_count: Int
  estimated_read_seconds: Float
  structural_role: StructuralRole
  heading_path: HeadingPath
  previous_piece_id: PieceId?
  next_piece_id: PieceId?
  flags: Set<DeterministicFlag>
}
```

Allowed deterministic flags:

```text
is_heading
is_code
is_table
is_blockquote
is_list
is_list_run
is_colon_terminated
is_skipped_source
```

Rules:

- preprocessing may not change source offsets;
- preprocessing may not split or merge pieces;
- preprocessing may be recomputed at any time;
- read time uses deterministic WPM rules only.

## Deterministic Ruleset

Phase 1 needs one ruleset shape that can express current behaviour and a
structural variant.

```text
type DeterministicChunkingRuleset {
  atomic_rules: AtomicRules
  reading_rules: ReadingRules
  structural_rules: StructuralRules
  materialization_rules: MaterializationRules
}
```

### Reading Rules

```text
type ReadingRules {
  prose_wpm: Int
  code_wpm: Int
  budget_seconds: Float
  heading_cost: "normal" | "zero"
}
```

Default:

```text
prose_wpm: 220
code_wpm: 120
budget_seconds: 30
heading_cost: zero
```

### Structural Rules

```text
type StructuralRules {
  heading_attachment: "alone" | "attach_forward" | "zero_cost_attach_forward"
  code_handling: "atomic"
  list_handling: "item" | "run"
  list_lead_in: "none" | "colon_previous_paragraph"
  table_handling: "atomic"
  blockquote_handling: "atomic"
}
```

Default:

```text
heading_attachment: zero_cost_attach_forward
code_handling: atomic
list_handling: run
list_lead_in: colon_previous_paragraph
table_handling: atomic
blockquote_handling: atomic
```

### Materialization Rules

```text
type MaterializationRules {
  require_contiguous_chunks: Bool
  preserve_source_text_when_contiguous: Bool
}
```

Phase 1 default:

```text
require_contiguous_chunks: true
preserve_source_text_when_contiguous: true
```

Phase 1 should reject non-contiguous plans. Do not introduce joining rules yet.

## ChunkPlan

`ChunkPlan` is a deterministic decision artifact over piece IDs.

It is not a document model and not a source of text.

```text
type ChunkPlan {
  revision_id: RevisionId
  chunking_run_id: ChunkingRunId
  planned_chunks: PlannedChunk[]
}

type PlannedChunk {
  ordinal: Int
  piece_ids: PieceId[]
  estimated_read_seconds: Float
  lead_piece_id: PieceId
  reason: PlanningReason
}
```

Allowed planning reasons:

```text
prose_budget
heading_attach_forward
structural_atomic_block
list_run
list_with_colon_lead_in
end_of_document
```

Rules:

- every `piece_id` must belong to the same `revision_id` and chunking run;
- pieces inside a planned chunk must be in document order;
- planned chunks must be non-overlapping;
- planned chunks must be contiguous in reveal order;
- skipped source pieces must be intentionally omitted before planning;
- every revealable piece must appear in exactly one planned chunk.

## Deterministic Strategies

Phase 1 has two deterministic strategies.

### Current Reading Time Strategy

This rebuilds the existing reader behaviour through the new substrate.

Intent:

```text
Pack prose sentences by reading-time budget while preserving structural blocks.
```

Rules:

- paragraph prose is sentence-split;
- prose sentences pack greedily up to `budget_seconds`;
- consecutive paragraph blocks may pack into one chunk;
- headings attach forward according to `heading_attachment`;
- code blocks are one chunk;
- blockquotes are one chunk;
- tables are one chunk;
- list runs are one chunk by default;
- colon-terminated paragraph lead-ins attach to the following list run when
  enabled;
- horizontal rules, images, and blank blocks are skipped.

Pseudocode:

```text
function plan_current_reading_time(preprocessed, rules):
  chunks = []
  current = empty_chunk()

  for piece in preprocessed in document_order:
    if piece is skipped:
      continue

    if piece is colon_lead_in_for_next_list and rules.list_lead_in enabled:
      hold piece for next list
      continue

    if piece is structural_atomic_block:
      flush current
      if held_lead_in exists and piece is list_run:
        chunks.append([held_lead_in.id, piece.id], reason=list_with_colon_lead_in)
        clear held_lead_in
      else:
        chunks.append([piece.id], reason=structural_atomic_block)
      continue

    if piece is heading:
      flush current
      current.add(piece, cost=heading_cost(piece, rules))
      if rules.heading_attachment == "alone":
        flush current
      continue

    next_cost = current.cost + read_cost(piece)

    if current has revealable prose and next_cost > rules.budget_seconds:
      flush current

    current.add(piece)

  flush current
  return ChunkPlan(chunks)
```

### Structural Strategy

This is deterministic and Markdown-shape driven. It does not use semantic
meaning.

Intent:

```text
Prefer readable Markdown structures, then use reading-time packing for prose.
```

Rules:

- heading starts a chunk and may attach following prose up to budget;
- code, table, blockquote, and list runs are indivisible;
- list lead-ins attach only by deterministic colon rule;
- prose still packs by read-time budget;
- the strategy never looks for topic shifts or conceptual density.

This may initially share most of the implementation with current reading-time
strategy. It exists as a named strategy so later behaviour differences do not
get hidden inside conditionals.

## ChunkRecord

`ChunkRecord` is the persisted reveal unit used by the reader.

```text
type ChunkRecord {
  id: ChunkId
  document_id: DocumentId
  revision_id: RevisionId
  chunking_run_id: ChunkingRunId

  position: Int
  piece_ids: PieceId[]

  source_offset_start: Int
  source_offset_end: Int
  start_line: Int
  end_line: Int
  start_column: Int
  end_column: Int

  text_snapshot: String
  text_hash: Hash

  lead_token_type: BlockType
  lead_heading_level: Int?
  estimated_read_seconds: Float
  section_id: SectionId?
}
```

Rules:

- `position` is dense and zero-based within a chunking run;
- `piece_ids` are the plan inputs used to produce the chunk;
- offsets must span the exact contiguous source range for the chunk;
- `text_snapshot` must equal the revision slice for contiguous Phase 1 chunks;
- `text_hash` validates the materialized snapshot;
- reader events may reference chunk IDs, but future re-chunking must preserve
  enough offset information to re-anchor events by source overlap.

## Materialization

Materialization turns a valid `ChunkPlan` into `ChunkRecord[]`.

```text
function materialize(plan, revision, pieces, rules):
  chunks = []

  for planned in plan.planned_chunks:
    ordered = pieces_for(planned.piece_ids).sort_by_source_offset()

    require ordered is not empty
    require pieces_are_contiguous_or_separated_only_by_skipped_source(ordered)
    require rules.require_contiguous_chunks

    start = ordered.first.source_offset_start
    end = ordered.last.source_offset_end
    text = revision.full_text[start:end]

    require text contains every ordered piece slice in order

    chunks.append(ChunkRecord(
      position = chunks.length,
      piece_ids = planned.piece_ids,
      source_offset_start = start,
      source_offset_end = end,
      text_snapshot = text,
      text_hash = hash(text),
      lead_token_type = ordered.first.kind,
      lead_heading_level = ordered.first.heading_level,
      estimated_read_seconds = planned.estimated_read_seconds
    ))

  return chunks
```

Phase 1 should prefer source slices over joining piece text. Joining rules belong
to a later phase, if non-contiguous chunks are ever admitted.

## SectionRecord

Sections remain deterministic heading-bounded groups over chunk positions.

```text
type SectionRecord {
  id: SectionId
  document_id: DocumentId
  revision_id: RevisionId
  chunking_run_id: ChunkingRunId
  heading_chunk_id: ChunkId?
  heading_level: Int?
  title: String?
  start_chunk_position: Int
  end_chunk_position: Int
}
```

Rules:

- prologue section has `heading_chunk_id = null`;
- a chunk whose lead piece is a heading starts a section;
- section ranges are inclusive;
- sections are derived after chunk materialization;
- sections do not affect source identity.

## Validation Gates

Phase 1 should validate each stage.

### Revision Validation

- `content_hash` matches exact revision text.
- line index maps offsets to line and column positions.
- revision text is immutable after creation.

### Parsed Block Validation

- block offsets are in bounds;
- block offsets are ordered;
- block text slices match parser output;
- skipped block types are explicit.

### Atomic Piece Validation

- piece offsets are in bounds;
- piece offsets are ordered;
- piece slices hash to `text_hash`;
- piece ordinals are dense and document-ordered;
- every revealable parsed block contributes at least one piece;
- no piece crosses a parsed block boundary unless the atomic rule explicitly
  creates a structural run, such as `list_run`.

### Plan Validation

- all piece IDs exist;
- chunks are non-empty;
- piece IDs are ordered;
- chunks do not overlap;
- every revealable piece is assigned once;
- no chunk exceeds hard structural rules;
- Phase 1 chunks are contiguous source spans.

### Materialization Validation

- chunk offsets are in bounds;
- chunk text equals `revision.full_text[start:end]`;
- chunk hash matches text;
- chunk positions are dense and zero-based;
- chunk lead fields match the first piece;
- section ranges cover all chunks exactly once.

## Persistence Shape

The existing database can evolve toward this shape without changing the reader's
core contract.

Near-term tables:

```text
document_revisions
chunking_runs
atomic_pieces
chunks
sections
```

Optional tables if useful for debugging or indexing:

```text
parsed_blocks
preprocessed_pieces
chunk_plan_items
```

Implementation rule:

```text
Persist only what helps performance, inspection, or event re-anchoring.
Keep every persisted derived record rebuildable.
```

## Re-Chunking Rule

When rules change, do not mutate the meaning of old chunks.

Create a new `ChunkingRun` for:

- changed strategy name;
- changed strategy version;
- changed atomic rules;
- changed reading rules;
- changed structural rules;
- changed materialization rules;
- changed parser version if it affects offsets or block classification.

Existing reader events may continue to reference old chunk IDs. Projection
rebuild may map old events to the new run by source-offset overlap.

Phase 1 does not need perfect cross-run re-anchoring. It must preserve enough
data to make it possible.

## Implementation Sequence

1. Introduce `DocumentRevision` as the immutable chunking input.
2. Add a line index helper for offset-to-line/column lookup.
3. Extract parser output into `ParsedBlock[]` with offsets.
4. Build `AtomicPiece[]` from parsed blocks using `AtomicRules`.
5. Validate atomic pieces against revision slices and hashes.
6. Add deterministic preprocessing for read time, flags, heading path, and
   adjacency.
7. Implement `ChunkPlan` for current reading-time behaviour.
8. Materialize contiguous `ChunkRecord[]` from revision slices.
9. Derive `SectionRecord[]` from materialized chunks.
10. Persist `ChunkingRun`, atomic pieces, chunks, and sections.
11. Rewire upload ingestion to read chunks from the new run.
12. Add the deterministic structural strategy as a named variant only after the
    current behaviour is reproduced.

## Test Strategy

Tests should lock down the substrate before expanding behaviour.

### Golden Behaviour Tests

Use small Markdown fixtures for:

- consecutive paragraphs packing across paragraph boundaries;
- heading attaching forward;
- bare heading before heading;
- code block as one chunk;
- whole list as one chunk;
- colon lead-in absorbed into list chunk;
- blockquote as one chunk;
- table as one chunk;
- image skipped;
- horizontal rule skipped;
- empty document failure.

### Invariant Tests

For every fixture:

- all chunk text equals revision slices;
- chunk offsets are ordered;
- piece offsets are ordered;
- every revealable piece appears exactly once;
- chunk positions are dense;
- section ranges cover all chunks;
- re-running the same revision and rules produces identical pieces and chunks.

### Ruleset Tests

Cover deterministic rule changes:

- paragraph sentence versus paragraph atomicity;
- list item versus list run atomicity;
- heading normal cost versus zero cost;
- colon lead-in enabled versus disabled.

## Explicit Non-Goals

Do not implement these in Phase 1:

- LLM calls;
- semantic annotation tables;
- semantic chunk plans;
- topic shift detection;
- difficulty, novelty, or density scoring;
- user-facing strategy presets;
- non-contiguous chunk materialization;
- chunk text rewriting;
- fuzzy re-anchoring;
- cross-document chunk indexes.

## Acceptance Criteria

Phase 1 is complete when:

1. Current deterministic reading-time chunking is rebuilt through
   `DocumentRevision -> AtomicPiece -> ChunkPlan -> ChunkRecord`.
2. Chunks are materialized from immutable revision source slices.
3. Atomic pieces and chunks carry offsets, line spans, hashes, and dense
   ordinals.
4. Re-running the same revision and rules produces identical output.
5. Structural rules for headings, code, lists, blockquotes, tables, and colon
   lead-ins are explicit and tested.
6. Existing reader behaviour is preserved.
7. Semantic and LLM systems remain absent from the implementation.

## Final Shape

The intended Phase 1 outcome is not smarter chunking. It is cleaner chunking.

Parsem should end this phase with a deterministic source substrate that makes
the current reading chamber more stable and inspectable. Later semantic systems
may advise over atomic piece IDs, but they should arrive as overlays on this
foundation, not as part of it.
