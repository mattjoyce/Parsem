# Chunking Strategy Brief

## Purpose

This brief describes a proposed architecture for evolving Parsem's chunking
system beyond the current deterministic time-based chunker.

The goal is to support multiple chunking strategies, including:

- deterministic reading-time chunking
- deterministic structural chunking
- user-preference-driven chunking
- semantic chunking driven by concept density, anchors, and accretion
- hybrid strategies that combine deterministic legality with LLM-assisted
  semantic judgment

This document is intentionally conceptual. It uses pseudocode only and does not
prescribe exact implementation details.

## Core Idea

Separate chunking into distinct phases:

```text
ParsedBlock[]
  -> AtomicPiece[]
  -> PreprocessedPiece[]
  -> SemanticMap
  -> AnchorPoint[]
  -> ChunkPlan
  -> Chunk[]
  -> Section[]
```

The most important architectural principle:

```text
Deterministic legality first.
Semantic preference second.
Deterministic materialization last.
```

The chunker should never depend on an LLM to invent source boundaries or produce
final chunk text. Instead, deterministic preprocessing creates legal atomic
pieces with exact source offsets. Semantic systems may annotate those pieces,
identify anchors, or propose grouping decisions over piece IDs. Final chunks are
then materialized deterministically from those piece IDs.

## Problems With The Current Shape

The existing chunker is effective but mixes several concerns:

- parsing already-parsed Markdown blocks into reveal units
- estimating reading time
- deciding which blocks are atomic
- deciding how headings absorb paragraph content
- deciding how lists are merged
- deriving sections
- enforcing a single time-budget-based chunking strategy

That makes it hard to support alternate goals such as:

- code is always one chunk
- titles/headings are zero cost
- a list always includes its preceding lead-in
- preamble can be chunked broadly
- dense foundational concepts should be isolated
- examples should be attached to definitions
- an LLM should identify semantic anchors and surrounding support

The system needs a vocabulary for atomicity, rules, rulesets, preprocessing,
strategy, semantic mapping, and chunk materialization.

## Key Concepts

### ParsedBlock

Existing parsed Markdown block from the parser.

Examples:

- heading
- paragraph
- code
- list_item
- blockquote
- table

### AtomicPiece

The smallest legal unit the chunking system may combine or move across chunk
boundaries.

Atomic pieces must be deterministic and source-faithful.

Examples:

- a heading
- a paragraph sentence
- a code block
- a table
- a list item
- a list run
- a blockquote

Pseudocode:

```text
type AtomicPiece {
  id: PieceId
  kind: PieceKind
  source_offset_start: Int
  source_offset_end: Int
  text: String
  source_block_index: Int
  ordinal_in_block: Int
  heading_level: Int?
  parent_piece_id: PieceId?
  metadata: Map
}
```

Atomicity is not necessarily universal. A ruleset may choose whether list items
are atomic individually or whether a consecutive list run is atomic as a whole.
However, each strategy should make that choice explicitly before semantic
chunking begins.

### PreprocessedPiece

An atomic piece plus deterministic annotations.

Examples:

- word count
- estimated read seconds
- token count
- structural role
- heading ancestry
- preceding/following piece IDs
- whether it is code
- whether it is a list lead-in
- whether it ends with a colon

Pseudocode:

```text
type PreprocessedPiece {
  piece: AtomicPiece
  read_seconds: Float
  token_count: Int
  word_count: Int
  structural_role: StructuralRole
  heading_path: HeadingPath
  previous_piece_id: PieceId?
  next_piece_id: PieceId?
  deterministic_flags: Set<Flag>
}
```

### SemanticMap

A semantic interpretation of the document over atomic piece IDs.

This may be produced by an LLM, heuristics, embeddings, or a hybrid system. It
should not replace the source-faithful piece list. It annotates relationships
and meaning.

Pseudocode:

```text
type SemanticMap {
  piece_annotations: Map<PieceId, SemanticPieceAnnotation>
  relations: SemanticRelation[]
  candidate_anchors: AnchorPoint[]
  boundary_signals: BoundarySignal[]
  document_summary: String?
}
```

### SemanticPieceAnnotation

Meaning-oriented metadata for a piece.

Pseudocode:

```text
type SemanticPieceAnnotation {
  piece_id: PieceId
  difficulty: Float
  semantic_density: Float
  novelty: Float
  abstraction_level: Float
  dependency_on_previous: Float
  topic_shift_before: Float
  introduces_concept: Bool
  defines_term: Bool
  states_finding: Bool
  gives_example: Bool
  is_preamble: Bool
  is_transition: Bool
  keep_with_previous: Bool
  keep_with_next: Bool
  rationale: String?
}
```

### SemanticRelation

A relation between pieces, used to capture compound semantics.

This is critical because meaning often emerges across multiple pieces. A
sentence may be simple in isolation but important because it frames, resolves,
contrasts, or exemplifies another piece.

Pseudocode:

```text
type SemanticRelation {
  from_piece_id: PieceId
  to_piece_id: PieceId
  kind: RelationKind
  strength: Float
  rationale: String?
}
```

Example relation kinds:

```text
supports
defines
exemplifies
contrasts
depends_on
elaborates
summarizes
introduces
resolves
```

### AnchorPoint

A semantic center around which a chunk may form.

An anchor is not merely a high-scoring piece. It is a point of conceptual
gravity: a definition, claim, finding, foundational distinction, new concept,
argument turn, or other important meaning center.

Pseudocode:

```text
type AnchorPoint {
  id: AnchorId
  anchor_piece_id: PieceId
  label: String
  kind: AnchorKind
  importance: Float
  difficulty: Float
  semantic_density: Float
  supporting_piece_ids: PieceId[]
  example_piece_ids: PieceId[]
  prerequisite_piece_ids: PieceId[]
  boundary_before_piece_id: PieceId?
  boundary_after_piece_id: PieceId?
  rationale: String?
}
```

Example anchor kinds:

```text
document_thesis
section_theme
foundational_concept
definition
claim
finding
contrast
procedure
example
warning
transition
```

### ChunkPlan

A plan over piece IDs before final materialization.

Pseudocode:

```text
type ChunkPlan {
  planned_chunks: PlannedChunk[]
}

type PlannedChunk {
  id: PlannedChunkId
  piece_ids: PieceId[]
  anchor_id: AnchorId?
  strategy: StrategyName
  estimated_cost: ChunkCost
  rationale: String?
}
```

### Chunk

The final reveal unit used by the reader.

Chunks should be materialized deterministically from piece IDs and source
offsets wherever possible.

Pseudocode:

```text
type Chunk {
  position: Int
  source_offset_start: Int
  source_offset_end: Int
  text: String
  lead_token_type: BlockType
  lead_heading_level: Int?
  estimated_read_seconds: Float
  metadata: Map
}
```

## Strategy Architecture

The chunking strategy should orchestrate the pipeline, but individual concerns
should remain independently swappable.

Pseudocode:

```text
interface ChunkingStrategy {
  name: StrategyName

  chunk(blocks: ParsedBlock[], config: ChunkingConfig): ChunkerOutput
}
```

Internally, most strategies can share this shape:

```text
function chunk(blocks, config):
  atomic_rules = config.atomic_rules
  preprocessing_rules = config.preprocessing_rules
  planning_rules = config.planning_rules
  materialization_rules = config.materialization_rules

  pieces = build_atomic_pieces(blocks, atomic_rules)
  preprocessed = preprocess_pieces(pieces, preprocessing_rules)

  strategy_context = create_strategy_context(preprocessed, config)

  plan = planner.plan(strategy_context, planning_rules)

  chunks = materialize_chunks(plan, pieces, materialization_rules)
  sections = derive_sections(chunks)

  return ChunkerOutput(chunks, sections)
```

Semantic strategies add a semantic map:

```text
function semantic_chunk(blocks, config):
  pieces = build_atomic_pieces(blocks, config.atomic_rules)
  preprocessed = preprocess_pieces(pieces, config.preprocessing_rules)

  semantic_map = semantic_mapper.map(preprocessed, config.semantic_config)
  anchors = anchor_selector.select(semantic_map, config.anchor_rules)
  plan = accretion_planner.plan(preprocessed, semantic_map, anchors, config.accretion_rules)

  chunks = materialize_chunks(plan, pieces, config.materialization_rules)
  sections = derive_sections(chunks)

  return ChunkerOutput(chunks, sections)
```

## Atomic Piece Builder

The atomic piece builder defines legal boundaries.

Pseudocode:

```text
interface AtomicPieceBuilder {
  build(blocks: ParsedBlock[], rules: AtomicRules): AtomicPiece[]
}
```

Example rules:

```text
type AtomicRules {
  paragraph_atomicity: "sentence" | "paragraph"
  heading_atomicity: "heading"
  code_atomicity: "block"
  table_atomicity: "block"
  blockquote_atomicity: "block" | "sentence"
  list_atomicity: "item" | "run"
}
```

Example deterministic behavior:

```text
for each block in blocks:
  if block.type == "paragraph":
    if rules.paragraph_atomicity == "sentence":
      split paragraph into sentence pieces
    else:
      create one paragraph piece

  if block.type == "code":
    create one code piece

  if block.type == "heading":
    create one heading piece

  if block.type == "list_item":
    if rules.list_atomicity == "item":
      create one list item piece
    if rules.list_atomicity == "run":
      merge consecutive list items into one list run piece
```

## Cost Model

Costs should be generalized beyond reading time.

Pseudocode:

```text
type ChunkCost {
  read_seconds: Float
  cognitive_load: Float
  semantic_density: Float
  difficulty: Float
  novelty: Float
}
```

Cost estimators may be deterministic or semantic.

```text
interface CostEstimator {
  estimate_piece(piece: PreprocessedPiece, context: StrategyContext): ChunkCost
  estimate_chunk(piece_ids: PieceId[], context: StrategyContext): ChunkCost
}
```

Reading-time cost:

```text
read_seconds = words / effective_wpm * 60
cognitive_load = read_seconds
semantic_density = unknown
difficulty = unknown
novelty = unknown
```

Semantic learning cost:

```text
cognitive_load =
  weighted_sum(
    read_seconds,
    semantic_density,
    difficulty,
    novelty,
    abstraction_level,
    dependency_on_previous
  )
```

Rules may override cost:

```text
if piece.kind == "heading" and config.heading_cost == "zero":
  piece_cost = zero

if piece.kind == "code" and config.code_handling == "atomic":
  piece is unsplittable even if cost exceeds budget
```

## Rule Sets

A ruleset configures how atomic pieces may be combined.

Pseudocode:

```text
type ChunkingRuleset {
  atomic_rules: AtomicRules
  structural_rules: StructuralRules
  cost_rules: CostRules
  boundary_rules: BoundaryRules
  accretion_rules: AccretionRules?
  user_preferences: UserChunkingPreferences
}
```

Structural rules:

```text
type StructuralRules {
  code_handling: "atomic" | "prose"
  heading_attachment: "alone" | "attach_forward" | "zero_cost_attach_forward"
  list_handling: "whole_list" | "per_item" | "semantic"
  list_lead_in: "never" | "colon_only" | "always_previous_line" | "semantic"
  table_handling: "atomic" | "semantic"
}
```

Boundary rules:

```text
type BoundaryRules {
  max_read_seconds: Float?
  max_cognitive_load: Float?
  max_semantic_density: Float?
  split_on_strong_topic_shift: Bool
  avoid_splitting_examples_from_definitions: Bool
  avoid_tiny_chunks: Bool
}
```

User preferences:

```text
type UserChunkingPreferences {
  reading_speed_multiplier: Float
  preferred_chunk_size: "small" | "medium" | "large"
  learning_mode: "speed" | "concept_learning" | "review" | "deep_study"
  code_preference: "always_atomic" | "allow_explained_splitting"
  list_preference: "always_with_lead_in" | "colon_lead_in" | "standalone"
  heading_preference: "zero_cost" | "count_as_text"
}
```

## Deterministic Time-Based Strategy

This is the current behavior generalized into the new architecture.

Goal:

```text
Create chunks that fit within a reading-time budget while preserving sentence
boundaries and structural blocks.
```

Pseudocode:

```text
function plan_time_based(preprocessed, rules):
  chunks = []
  current = []
  current_cost = zero

  for piece in preprocessed:
    if piece.kind is atomic_structural_block:
      flush current
      chunks.append([piece.id])
      continue

    if piece.kind == "heading":
      if rules.heading_attachment == "zero_cost_attach_forward":
        current.append(piece.id)
        continue

    next_cost = current_cost + cost(piece)

    if current not empty and next_cost exceeds max_read_seconds:
      flush current
      current = []
      current_cost = zero

    current.append(piece.id)
    current_cost += cost(piece)

  flush current
  return ChunkPlan(chunks)
```

## Deterministic Structural Strategy

Goal:

```text
Use structural Markdown shape rather than semantic density.
```

Examples:

- code is always one chunk
- headings attach to following content
- lists always include the preceding line
- tables are atomic
- paragraph sentences may be packed by reading time

Pseudocode:

```text
function plan_structural(preprocessed, rules):
  chunks = []
  cursor = 0

  while cursor < pieces.length:
    piece = pieces[cursor]

    if piece.kind == "heading":
      chunk = [piece.id]
      chunk += take_following_until_budget_or_boundary(cursor, rules)
      chunks.append(chunk)
      cursor = after(chunk)
      continue

    if piece.kind == "list_run":
      lead_in = find_lead_in(piece, rules.list_lead_in)
      chunk = lead_in + [piece.id]
      chunks.append(chunk)
      cursor = after(piece)
      continue

    if piece.kind == "code":
      chunks.append([piece.id])
      cursor += 1
      continue

    chunk = pack_forward_by_budget(cursor, rules)
    chunks.append(chunk)
    cursor = after(chunk)

  return ChunkPlan(chunks)
```

## Semantic Strategy

The semantic strategy should not simply score isolated pieces. It should model
compound semantics: concepts, claims, examples, dependencies, and argument
structure across multiple pieces.

The proposed semantic pipeline:

```text
AtomicPiece[]
  -> SemanticMap
  -> AnchorPoint[]
  -> ChunkAccretion
  -> Chunk[]
```

### Semantic Map Generation

The semantic mapper reads the document as a sequence of piece IDs and text.

It returns semantic annotations over IDs.

Important requirement:

```text
The mapper must reference only existing piece IDs.
It must not produce arbitrary chunk text.
It must not invent source offsets.
```

Pseudocode:

```text
interface SemanticMapper {
  map(preprocessed: PreprocessedPiece[], config: SemanticConfig): SemanticMap
}
```

LLM-oriented pseudocode:

```text
function map_with_llm(preprocessed):
  prompt = render_piece_sequence(preprocessed)

  response = ask_llm_for_json({
    task: "Identify semantic anchors, dependencies, examples, topic shifts, and density peaks.",
    constraints: [
      "Use only provided piece IDs.",
      "Do not rewrite text.",
      "Do not create final chunks.",
      "Represent relationships between pieces."
    ],
    schema: SemanticMapSchema
  })

  semantic_map = validate_response_against_piece_ids(response)
  return semantic_map
```

### Anchor Selection

Anchors represent centers of meaning.

Pseudocode:

```text
interface AnchorSelector {
  select(semantic_map: SemanticMap, rules: AnchorRules): AnchorPoint[]
}
```

Anchor selection rules may prefer:

- definitions
- foundational concepts
- key claims
- findings
- transitions in argument
- high-density passages
- places where the reader needs a pause

Pseudocode:

```text
function select_anchors(semantic_map, rules):
  candidates = semantic_map.candidate_anchors

  anchors = candidates
    .filter(candidate.importance >= rules.min_importance)
    .filter(candidate.kind in rules.allowed_anchor_kinds)
    .rank_by(candidate.importance, candidate.difficulty, candidate.semantic_density)

  return suppress_near_duplicates(anchors, rules.min_distance_between_anchors)
```

### Chunk Accretion

Chunk accretion grows chunks around anchors.

This is the heart of semantic chunking.

Instead of packing pieces linearly until a budget is full, the planner starts
from an anchor and pulls in nearby support:

- prerequisite setup
- headings
- definitions
- examples
- clarifying contrasts
- lead-ins
- tightly related list items

It stops at:

- another independent anchor
- excessive cognitive load
- strong topic shift
- argument boundary
- section boundary, depending on rules

Pseudocode:

```text
interface AccretionPlanner {
  plan(
    preprocessed: PreprocessedPiece[],
    semantic_map: SemanticMap,
    anchors: AnchorPoint[],
    rules: AccretionRules
  ): ChunkPlan
}
```

Accretion rules:

```text
type AccretionRules {
  max_read_seconds: Float?
  max_cognitive_load: Float?
  include_heading_path: Bool
  include_prerequisites: Bool
  include_examples: Bool
  include_contrasts: Bool
  include_list_lead_in: "always" | "semantic" | "colon_only"
  stop_at_independent_anchor: Bool
  stop_at_strong_topic_shift: Bool
  allow_anchor_merging: Bool
  max_anchor_merge_distance: Int
}
```

Core accretion pseudocode:

```text
function plan_by_accretion(preprocessed, semantic_map, anchors, rules):
  claimed_piece_ids = set()
  planned_chunks = []

  for anchor in anchors in document_order:
    if anchor.anchor_piece_id in claimed_piece_ids:
      continue

    chunk_piece_ids = set(anchor.anchor_piece_id)

    if rules.include_heading_path:
      chunk_piece_ids += heading_context_for(anchor)

    if rules.include_prerequisites:
      chunk_piece_ids += anchor.prerequisite_piece_ids

    chunk_piece_ids += accrete_backward(anchor, semantic_map, rules)
    chunk_piece_ids += accrete_forward(anchor, semantic_map, rules)

    chunk_piece_ids = close_structural_requirements(chunk_piece_ids, rules)
    chunk_piece_ids = order_by_document_position(chunk_piece_ids)

    if cost(chunk_piece_ids) exceeds hard_limits:
      chunk_piece_ids = shrink_to_core(anchor, chunk_piece_ids, rules)

    planned_chunks.append(PlannedChunk(
      piece_ids = chunk_piece_ids,
      anchor_id = anchor.id,
      strategy = "semantic_accretion",
      estimated_cost = cost(chunk_piece_ids),
      rationale = anchor.rationale
    ))

    claimed_piece_ids += chunk_piece_ids

  planned_chunks += handle_unclaimed_pieces(preprocessed, claimed_piece_ids, rules)

  return normalize_chunk_order(planned_chunks)
```

Backward accretion:

```text
function accrete_backward(anchor, semantic_map, rules):
  result = []
  cursor = piece_before(anchor.anchor_piece_id)

  while cursor exists:
    if cursor is already claimed:
      break

    if strong_boundary_between(cursor, anchor):
      break

    if cursor is independent_anchor and rules.stop_at_independent_anchor:
      break

    if relation(cursor, anchor) in ["introduces", "supports", "defines", "depends_on"]:
      result.prepend(cursor.id)
      cursor = piece_before(cursor)
      continue

    if cursor is low_density_preamble and cost_allows(cursor):
      result.prepend(cursor.id)
      cursor = piece_before(cursor)
      continue

    break

  return result
```

Forward accretion:

```text
function accrete_forward(anchor, semantic_map, rules):
  result = []
  cursor = piece_after(anchor.anchor_piece_id)

  while cursor exists:
    if cursor is already claimed:
      break

    if cursor is independent_anchor and rules.stop_at_independent_anchor:
      break

    if topic_shift_before(cursor) is strong and rules.stop_at_strong_topic_shift:
      break

    if relation(cursor, anchor) in ["exemplifies", "elaborates", "contrasts", "resolves"]:
      if cost_allows(cursor):
        result.append(cursor.id)
        cursor = piece_after(cursor)
        continue

    if cursor is example_for(anchor) and rules.include_examples:
      result.append(cursor.id)
      cursor = piece_after(cursor)
      continue

    break

  return result
```

Structural closure:

```text
function close_structural_requirements(piece_ids, rules):
  for piece in piece_ids:
    if piece.kind == "list_run" and rules.include_list_lead_in:
      piece_ids += required_lead_in(piece, rules)

    if piece.kind == "list_item" and list requires lead-in:
      piece_ids += required_lead_in(piece, rules)

    if piece.kind == "heading" and heading must attach forward:
      piece_ids += minimum_following_piece(piece)

    if piece.kind == "code":
      piece_ids += all_piece_ids_in_code_block(piece)

  return piece_ids
```

## Semantic Fractaling

Semantic chunking may be recursive.

The same operation can apply at multiple scales:

```text
document anchors
  -> section anchors
    -> paragraph anchors
      -> sentence anchors
```

This supports different reading modes.

Concept learning:

```text
Use broad chunks for low-density setup.
Use narrow chunks for foundational concepts.
Attach examples to abstract definitions.
Split dense anchors into smaller sub-anchors when needed.
```

Speed reading:

```text
Use larger chunks.
Collapse low-density explanation.
Split mostly at strong topic boundaries.
Avoid tiny chunks.
```

Review mode:

```text
Separate claims from evidence.
Isolate definitions.
Avoid including answer-like follow-up in the same reveal unit.
Prefer recall-friendly boundaries.
```

Recursive pseudocode:

```text
function semantic_fractal_chunk(region, target_mode):
  semantic_map = map_region(region)
  anchors = select_anchors(semantic_map, target_mode)

  chunks = []

  for anchor in anchors:
    chunk = accrete_around(anchor)

    if cost(chunk) exceeds target_mode.max_cognitive_load:
      subchunks = semantic_fractal_chunk(chunk.region, target_mode.with_smaller_scale())
      chunks += subchunks
    else:
      chunks.append(chunk)

  return chunks
```

## Hybrid Strategy

A practical first semantic implementation should be hybrid:

1. deterministic atomic piece creation
2. deterministic preprocessing
3. LLM semantic map over piece IDs
4. deterministic validation of LLM output
5. deterministic accretion
6. deterministic materialization

Pseudocode:

```text
function hybrid_semantic_chunk(blocks, config):
  pieces = build_atomic_pieces(blocks, config.atomic_rules)
  preprocessed = preprocess_pieces(pieces, config.preprocessing_rules)

  semantic_map = semantic_mapper.map(preprocessed, config.semantic_config)
  semantic_map = validate_semantic_map(semantic_map, pieces)

  anchors = select_anchors(semantic_map, config.anchor_rules)
  plan = accrete_chunks(preprocessed, semantic_map, anchors, config.accretion_rules)
  plan = validate_chunk_plan(plan, pieces, config.validation_rules)

  chunks = materialize_chunks(plan, pieces, config.materialization_rules)
  sections = derive_sections(chunks)

  return ChunkerOutput(chunks, sections)
```

## Validation

Semantic systems require strict validation because LLM output may be malformed,
overconfident, or structurally invalid.

Validation rules:

```text
All referenced piece IDs must exist.
No planned chunk may contain piece IDs outside the document.
Piece ordering must be recoverable from source order.
Chunks must not overlap unless the strategy explicitly permits duplicated context.
Required atomic pieces must not be split.
Final text must be materialized from source pieces, not LLM text.
Source offsets must come from atomic pieces.
```

Pseudocode:

```text
function validate_semantic_map(map, pieces):
  known_ids = set(piece.id for piece in pieces)

  for annotation in map.piece_annotations:
    require annotation.piece_id in known_ids

  for relation in map.relations:
    require relation.from_piece_id in known_ids
    require relation.to_piece_id in known_ids

  for anchor in map.candidate_anchors:
    require anchor.anchor_piece_id in known_ids
    require all ids in anchor.supporting_piece_ids exist
    require all ids in anchor.example_piece_ids exist
    require all ids in anchor.prerequisite_piece_ids exist

  return normalized_map
```

Plan validation:

```text
function validate_chunk_plan(plan, pieces, rules):
  for chunk in plan.planned_chunks:
    require chunk.piece_ids not empty
    require all piece IDs exist
    require piece IDs are in document order after normalization
    require no forbidden overlap
    require no atomic block is partially included
    require structural closure is satisfied

  require every required piece is assigned or intentionally skipped
```

## Materialization

Materialization converts a chunk plan into final chunks.

This should be deterministic and source-faithful.

Pseudocode:

```text
function materialize_chunks(plan, pieces, rules):
  chunks = []

  for planned_chunk in plan.planned_chunks in document_order:
    ordered_pieces = order_by_document_position(planned_chunk.piece_ids)

    text = join_piece_texts(ordered_pieces, rules.joining)
    source_start = min(piece.source_offset_start for piece in ordered_pieces)
    source_end = max(piece.source_offset_end for piece in ordered_pieces)

    chunks.append(Chunk(
      position = chunks.length,
      source_offset_start = source_start,
      source_offset_end = source_end,
      text = text,
      lead_token_type = ordered_pieces[0].kind,
      lead_heading_level = ordered_pieces[0].heading_level,
      estimated_read_seconds = estimate_read_seconds(text),
      metadata = planned_chunk.metadata
    ))

  return chunks
```

Joining rules matter because some chunks may combine non-contiguous pieces, such
as a heading and a later sentence, or a lead-in and a list.

Pseudocode:

```text
type JoiningRules {
  preserve_source_text_when_contiguous: Bool
  insert_separator_between_heading_and_body: String
  insert_separator_between_lead_in_and_list: String
  allow_non_contiguous_chunks: Bool
}
```

Preferred default:

```text
If pieces are contiguous in source, use source slice.
If pieces are adjacent but need Markdown separation, insert configured separator.
If pieces are non-contiguous, either reject or materialize with explicit separators,
depending on strategy.
```

## Sections

Section derivation can remain mostly deterministic.

Pseudocode:

```text
function derive_sections(chunks):
  sections = []
  current_section = prologue

  for chunk in chunks:
    if chunk.lead_token_type == "heading":
      close current_section before chunk
      start new section at chunk

  close final section
  return sections
```

Semantic strategies may later add semantic sections, but heading-bounded
sections should remain available for compatibility.

## LLM Contract

The LLM should act as an advisor, not the chunk materializer.

The LLM may:

- identify semantic anchors
- score difficulty
- score density
- identify topic shifts
- identify examples
- identify dependencies
- suggest keep-with-previous or keep-with-next relationships
- explain rationale

The LLM must not:

- produce final chunk text
- invent source offsets
- reference missing piece IDs
- split atomic pieces
- reorder text outside allowed strategy rules

Example LLM output shape:

```text
{
  piece_annotations: {
    "p17": {
      difficulty: 0.82,
      semantic_density: 0.91,
      novelty: 0.76,
      abstraction_level: 0.88,
      dependency_on_previous: 0.64,
      topic_shift_before: 0.12,
      introduces_concept: true,
      defines_term: false,
      states_finding: false,
      gives_example: false,
      is_preamble: false,
      is_transition: false,
      keep_with_previous: true,
      keep_with_next: false,
      rationale: "Introduces the central distinction used by the following examples."
    }
  },
  relations: [
    {
      from_piece_id: "p16",
      to_piece_id: "p17",
      kind: "introduces",
      strength: 0.83
    },
    {
      from_piece_id: "p18",
      to_piece_id: "p17",
      kind: "exemplifies",
      strength: 0.78
    }
  ],
  candidate_anchors: [
    {
      id: "a3",
      anchor_piece_id: "p17",
      label: "Semantic accretion around concept anchors",
      kind: "foundational_concept",
      importance: 0.94,
      difficulty: 0.82,
      semantic_density: 0.91,
      supporting_piece_ids: ["p16"],
      example_piece_ids: ["p18"],
      prerequisite_piece_ids: ["p15"],
      boundary_before_piece_id: "p15",
      boundary_after_piece_id: "p19",
      rationale: "This piece establishes the model used by the surrounding discussion."
    }
  ]
}
```

## Strategy Examples

### Strategy: Current Reading Time

```text
atomic_rules:
  paragraph_atomicity: sentence
  code_atomicity: block
  list_atomicity: run

structural_rules:
  heading_attachment: attach_forward
  list_lead_in: colon_only
  code_handling: atomic

boundary_rules:
  max_read_seconds: 30
  split_on_strong_topic_shift: false
```

### Strategy: User Preference, Lists Need Lead-In

```text
atomic_rules:
  paragraph_atomicity: sentence
  list_atomicity: run

structural_rules:
  list_lead_in: always_previous_line
  heading_attachment: zero_cost_attach_forward
  code_handling: atomic

boundary_rules:
  max_read_seconds: user_preference
```

### Strategy: Concept Learning

```text
atomic_rules:
  paragraph_atomicity: sentence
  code_atomicity: block
  list_atomicity: run

semantic_config:
  identify_anchors: true
  identify_relations: true
  identify_density_peaks: true
  identify_preamble: true

accretion_rules:
  include_heading_path: true
  include_prerequisites: true
  include_examples: true
  include_contrasts: true
  include_list_lead_in: always
  stop_at_independent_anchor: true
  stop_at_strong_topic_shift: true
  max_cognitive_load: medium

behavior:
  low-density preamble may form larger chunks
  foundational concepts form narrower chunks
  definitions stay with immediate examples
  dense findings may stand alone
```

### Strategy: Deep Study

```text
semantic_config:
  identify_subanchors: true
  allow_recursive_fractaling: true

accretion_rules:
  max_cognitive_load: low
  include_examples: true
  stop_at_independent_anchor: true

behavior:
  recursively split dense conceptual regions
  isolate claims, definitions, and mechanisms
  attach only the minimum context required for comprehension
```

### Strategy: Speed Reading

```text
boundary_rules:
  max_read_seconds: high
  avoid_tiny_chunks: true

semantic_config:
  identify_topic_shifts: true
  identify_low_density_regions: true

behavior:
  larger chunks
  low-density setup grouped broadly
  split mainly at strong topic shifts
  preserve code/list atomicity
```

## Open Design Questions

These should be resolved before implementation:

1. Should overlapping context be allowed?

   Example: a definition appears in one chunk and is repeated as context in a
   later chunk. This may help learning but complicates source-offset anchoring.

2. Should semantic chunks be allowed to be non-contiguous?

   Example: include a heading, skip a transition sentence, include the anchor.
   This may produce useful chunks but weakens source-faithful reading flow.

3. Should headings always be zero cost?

   For reading-time chunking, yes may be intuitive. For cognitive load, a dense
   technical heading may carry meaningful cost.

4. Should list lead-ins be structural or semantic?

   A simple rule says always include the previous paragraph. A semantic rule may
   include only genuine lead-ins, but this requires reliable judgment.

5. What is the fallback when semantic mapping fails?

   Recommended fallback: deterministic time-based or structural chunking.

6. How should semantic prompts be windowed for long documents?

   A long document may require section-level semantic maps, followed by a merge
   pass to preserve cross-section anchors and dependencies.

7. What metadata should final chunks expose to the reader?

   Examples: difficulty, estimated read time, anchor label, semantic rationale,
   strategy name.

## Recommended Implementation Sequence

Suggested order for a future implementation:

1. Extract current chunker into a named deterministic strategy.
2. Add `AtomicPiece` creation from parsed blocks.
3. Rebuild current behavior using `AtomicPiece[] -> ChunkPlan -> Chunk[]`.
4. Add configurable structural rules:
   - code atomicity
   - heading cost
   - heading attachment
   - list lead-in behavior
   - list item vs list run atomicity
5. Add generalized cost model.
6. Add semantic map data structures and validation.
7. Add LLM semantic mapper that outputs annotations, relations, and anchors over
   piece IDs.
8. Add anchor-based accretion planner.
9. Add strategy presets:
   - reading_time
   - structural
   - concept_learning
   - deep_study
   - speed_reading
10. Add fallback behavior and tests for malformed semantic output.

## Summary

The architecture should distinguish between what is legally chunkable and what
is pedagogically desirable.

Atomic pieces define legal source-faithful units. Rulesets define structural and
user preferences. Semantic maps identify meaning, density, dependencies, and
anchors. Accretion grows chunks around those anchors while respecting structural
and cognitive budgets. Final chunks are always materialized deterministically.

The resulting model supports both the current deterministic time-based chunker
and future semantic chunking strategies without sacrificing source integrity.

## Appendix: Atomic Chunk Indexing Approach

This appendix describes a proposed indexing and anchoring model for library
documents, atomic pieces, and chunks.

The design assumes that documents are copied into the Parsem library before
chunking. Once copied, Parsem owns the text for a specific immutable revision.
That makes line numbers useful and stable for that revision, while still
allowing offsets and hashes to provide exact source anchoring.

### Library Ownership

Chunking should operate on a library-owned document revision, not directly on an
external file that may change without Parsem knowing.

Pseudocode:

```text
type LibraryDocument {
  id: DocumentId
  title: String
  source_uri: String?
  created_at: Timestamp
}

type DocumentRevision {
  id: RevisionId
  document_id: DocumentId
  full_text: String
  content_hash: Hash
  line_index: LineIndex
  created_at: Timestamp
}
```

The document revision is the source of truth. Atomic pieces and chunks are
derived records for that revision.

```text
External source
  -> copied into LibraryDocument
  -> immutable DocumentRevision
  -> ParsedBlock[]
  -> AtomicPiece[]
  -> ChunkPlan
  -> Chunk[]
```

### Why Not Use Only Line Numbers?

Line numbers are stable inside an immutable revision and are useful for display,
debugging, and user-facing references. However, they are not precise enough as
the only internal anchor.

Potential issues:

- sentence chunks may begin or end mid-line
- repeated lines may be ambiguous
- Markdown blocks may span multiple lines
- later re-anchoring across revisions needs stronger evidence
- exact chunk materialization should not depend on line slicing alone

Recommended approach:

```text
Use line numbers for human-facing boundaries.
Use source offsets for exact slicing.
Use hashes to validate integrity.
Use text previews for indexing, debugging, and recovery hints.
Use piece IDs for planning.
```

### Atomic Piece Index

Atomic pieces should store enough information to be independently validated
against the owned document revision.

Pseudocode:

```text
type AtomicPiece {
  id: PieceId
  revision_id: RevisionId

  kind: PieceKind
  source_offset_start: Int
  source_offset_end: Int

  start_line: Int
  end_line: Int
  start_column: Int
  end_column: Int

  text_snapshot: String
  text_hash: Hash

  anchor_start_preview: String
  anchor_start_preview_hash: Hash
  anchor_end_preview: String?
  anchor_end_preview_hash: Hash?

  source_block_index: Int
  ordinal_in_block: Int
  heading_level: Int?
  heading_path: HeadingPath
}
```

The canonical text remains `DocumentRevision.full_text`. The `text_snapshot` is
a denormalized convenience for debugging, LLM prompts, tests, and index display.

Validation pseudocode:

```text
function validate_piece(piece, revision):
  source_text = revision.full_text[
    piece.source_offset_start : piece.source_offset_end
  ]

  require hash(source_text) == piece.text_hash

  if piece.text_snapshot exists:
    require source_text == piece.text_snapshot
```

### Chunk Index

A chunk should be indexed by revision, strategy, position, piece IDs, line
range, offsets, hashes, and a human-readable preview.

Pseudocode:

```text
type ChunkRecord {
  id: ChunkId
  revision_id: RevisionId
  strategy_id: StrategyId

  position: Int
  piece_ids: PieceId[]

  source_offset_start: Int
  source_offset_end: Int

  start_line: Int
  end_line: Int
  start_column: Int
  end_column: Int

  text_hash: Hash

  anchor_start_preview: String
  anchor_start_preview_hash: Hash
  anchor_end_preview: String?
  anchor_end_preview_hash: Hash?

  lead_token_type: BlockType
  lead_heading_level: Int?

  estimated_read_seconds: Float?
  semantic_label: String?
  difficulty: Float?
  semantic_density: Float?

  metadata: Map
}
```

The chunk plan should primarily refer to `piece_ids`, while the chunk record
stores derived boundaries for efficient lookup and display.

```text
type ChunkPlan {
  revision_id: RevisionId
  strategy_id: StrategyId
  planned_chunks: PlannedChunk[]
}

type PlannedChunk {
  piece_ids: PieceId[]
  anchor_id: AnchorId?
  rationale: String?
}
```

### Anchor Preview

Store the first normalized `N` characters of the first meaningful line of each
atomic piece and each chunk.

This should be treated as a locator hint, not the primary identity.

Pseudocode:

```text
function anchor_start_preview(text, n):
  line = first_non_empty_line(text)
  line = normalize_whitespace(line)
  line = trim(line)
  return first_n_characters(line, n)
```

Recommended fields:

```text
anchor_start_preview
anchor_start_preview_hash
anchor_end_preview
anchor_end_preview_hash
```

The end preview is optional but helpful when many chunks begin with similar
phrases.

Uses:

- display chunk indexes
- debug chunk plans
- show compact references in logs
- sanity-check offset resolution
- assist future re-anchoring across document revisions
- help humans identify chunks without loading full text

Not recommended:

```text
Do not use preview text as the canonical chunk identity.
Do not assume previews are unique.
Do not materialize chunks from previews.
```

### Lookup Priority

When resolving a chunk or atomic piece, use progressively weaker anchors.

Preferred lookup order:

```text
1. revision_id + chunk_id or piece_id
2. revision_id + source offsets
3. text_hash validation against revision text
4. line range for display or fallback location
5. anchor previews as human-readable hints or recovery aids
```

For an immutable document revision, offsets should normally be sufficient.
Hashes protect against corruption or accidental mismatch. Line numbers and
previews make the system understandable and recoverable.

### Materialization

Chunks should be materialized from the owned document revision and planned piece
IDs.

Pseudocode:

```text
function materialize_chunk(chunk_record, revision, pieces):
  ordered_pieces = load_pieces(chunk_record.piece_ids)
  ordered_pieces = order_by_source_position(ordered_pieces)

  if pieces_are_contiguous(ordered_pieces):
    text = revision.full_text[
      chunk_record.source_offset_start : chunk_record.source_offset_end
    ]
  else:
    text = join_piece_texts(ordered_pieces, chunk_record.joining_rules)

  require hash(text) == chunk_record.text_hash
  return text
```

For contiguous chunks, the source slice should be preferred. For non-contiguous
semantic chunks, materialization must use explicit joining rules.

### Re-Anchoring Across Revisions

Initial implementation does not need cross-revision re-anchoring, but the index
should leave room for it.

Future re-anchoring can attempt:

```text
1. exact text_hash match
2. exact anchor_start_preview and anchor_end_preview match
3. nearby line/offset match
4. fuzzy text similarity
5. mark stale if no reliable match exists
```

Pseudocode:

```text
function reanchor_piece(old_piece, new_revision):
  candidates = find_by_text_hash(old_piece.text_hash, new_revision)

  if one candidate:
    return candidate

  candidates = find_by_anchor_previews(
    old_piece.anchor_start_preview_hash,
    old_piece.anchor_end_preview_hash,
    new_revision
  )

  candidates = rank_by_nearby_old_location(candidates, old_piece)
  candidates = rank_by_fuzzy_similarity(candidates, old_piece.text_snapshot)

  if confidence(candidates.best) >= threshold:
    return candidates.best

  return stale
```

### Practical Recommendation

For the first implementation slice:

```text
Create immutable DocumentRevision records.
Build AtomicPiece records from each revision.
Store line spans, offsets, text snapshots, text hashes, and anchor previews.
Use piece IDs as the planner's unit of reference.
Use line numbers for display and chunk index browsing.
Use offsets and hashes for exact materialization and validation.
```

This gives the system a stable substrate for deterministic chunking now and
semantic chunking later.
