# Python Coding Standards for Parsem

Project-facing reference. Apply when writing or reviewing Python in this repository.

This document is deliberately opinionated toward Parsem's architecture:

- Markdown-first ingestion
- deterministic chunking
- append-only reading events
- rebuildable projections
- pure domain logic separated from transport and storage

It combines two influences:

- Rich Hickey idioms where they improve architectural clarity
- Pythonic idioms where they improve readability and local simplicity

Use both. Do not cargo-cult either.

---

## Core Rule

Write code so that:

1. The important logic is expressed as data transforms.
2. Stateful edges are narrow and explicit.
3. The code still reads like straightforward Python.

If a choice improves cleverness but harms inspectability, reject it.

---

## Architectural Boundaries

Parsem follows the separation described in the spec:

- `domain/` contains pure business logic.
- `parse/` adapts external parsing libraries into domain-friendly data.
- `store/` owns SQLite, migrations, event append, and projection rebuilds.
- `web/` is transport and HTML/JS delivery, not business logic.

Rules:

- `domain/` must not import from `web/` or `store/`.
- Prefer passing plain values into `domain/` functions and returning plain values out.
- Keep framework objects at the boundary. Do not pass FastAPI request objects, DB connections, or ORM-like rows into domain functions.
- `store/` may call `domain/`; `domain/` must not call `store/`.
- `parse/` should produce stable, explicit structures rather than leaking parser library types through the system.

---

## Hickey Idioms, Applied Pragmatically

These are the main Rich Hickey-style constraints for this codebase.

### Data over objects

- Prefer explicit data structures over stateful service objects.
- Use dataclasses, `TypedDict`, tuples, and small enums for stable domain values.
- Reach for classes when they model a real value or boundary, not to simulate namespaces or dependency injection containers.

Good fit in Parsem:

- chunk records
- section records
- selections
- projection snapshots
- config values

Bad fit:

- mutable "manager" objects carrying hidden cross-request state
- classes whose only job is wrapping a few functions

### Separate facts from caches

- The event log is the source of truth.
- Projections are caches derived from events.
- Never treat a projection table as authoritative when the event stream says otherwise.
- Any cached state must be rebuildable.

When adding a feature, ask:

1. What is the canonical fact?
2. What is the derived view?
3. Can the derived view be rebuilt from source facts?

### Pure transforms first

- Express chunking, bucket math, projection rebuilds, and selection logic as pure functions first.
- Wrap those pure functions with IO adapters second.
- Keep time, filesystem, database, and HTTP effects injectable at the boundary.

Examples:

- `chunker(token_stream, config) -> chunks, sections`
- `tokens_now(events, config, now) -> bucket_state`
- `project_reading_state(events) -> reading_state`

### Values at the boundary

- Time should be passed in, not read deep inside domain functions.
- Config should be passed in as an explicit value, not read from globals.
- Source offsets, chunk ids, and section ids are values. Keep them explicit.

### Simplicity over incidental abstraction

- Do not build generic plugin systems, repository layers, or event buses before the product needs them.
- Prefer one direct function call over an abstraction stack.
- Prefer a specific, boring schema over a flexible but unclear one.

---

## Pythonic Idioms, Applied Deliberately

Pythonic does not mean loose. It means clear, direct, and idiomatic.

### Readability first

- Use descriptive names.
- Prefer straightforward control flow over compressed cleverness.
- Keep functions small enough to scan, but not artificially tiny.
- Use early returns to keep the happy path obvious.

### Standard library first

- Prefer the standard library unless an external package materially improves correctness or simplicity.
- In this project, external libraries are justified where the spec already depends on them, such as `pysbd`, FastAPI, and Markdown parsing.

### Explicit beats magical

- Avoid hidden mutation through shared module state.
- Avoid implicit defaults that materially affect reading mechanics.
- Do not bury business rules in template logic or JavaScript event handlers when they belong in Python.

### Flat is better than nested

- Keep conditionals shallow where possible.
- Break complex branching into named helper functions.
- Prefer simple comprehensions over deeply nested loops, but do not force comprehensions where a normal loop is clearer.

### Exceptions for exceptional cases

- Raise exceptions for invalid states, parse failures, and invariant breaks.
- Return normal values for normal absence, such as "no next pin found" or "document has no heading".
- Add context when re-raising across boundaries.

### Mutability is a tool, not a default

- Use immutable-feeling value types for domain records when practical.
- Mutate local accumulators freely inside a function when it makes the code clearer.
- Avoid passing around mutable structures that many layers update opportunistically.

---

## Preferred Data Shapes

Use the lightest structure that preserves clarity.

- Dataclass: stable domain records with named fields
- `TypedDict`: structured dict-shaped payloads crossing boundaries
- `dict[str, Any]`: only for genuinely open-ended JSON or temporary glue
- tuple: fixed small return values where names are still obvious
- enum / `Literal`: constrained choices such as event kind, token type, color id

Rules:

- Do not use unstructured dicts for core domain records like chunks, sections, or bucket results.
- Keep persisted event payloads explicit and versionable.
- If a shape appears in more than one module, name it.

---

## Type Hints

- All non-trivial functions should have parameter and return annotations.
- Use `from __future__ import annotations`.
- Prefer `X | None` over `Optional[X]`.
- Annotate public module functions and boundary helpers first; add internal annotations wherever ambiguity would slow a reader down.
- Use aliases for repeated structured types when that improves readability.

Typing should clarify intent, not turn simple code into type gymnastics.

Avoid:

- deeply clever generics with little runtime value
- broad `Any` in domain code without a reason
- type-driven contortions to satisfy a checker while obscuring the code

---

## Modules and Project Layout

Match the spec's structure unless there is a clear reason to change it:

```text
parsem/
  domain/
    chunking.py
    bucket.py
    projections.py
    selections.py
  parse/
    markdown_parse.py
    sentence.py
  store/
    db.py
    events.py
    projections_cache.py
    settings.py
  web/
    routes/
    templates/
    static/
  cli.py
  main.py
```

Rules:

- Put business rules where a future reader would expect them.
- Do not hide domain logic in route handlers.
- Do not let SQL shape leak into unrelated modules.
- Keep modules cohesive. If a file becomes a junk drawer, split by responsibility, not by line count alone.

---

## Parsing and Chunking Rules

The chunker is one of the core product semantics. Treat it carefully.

- Preserve source offsets explicitly.
- Keep chunking deterministic.
- Never split a sentence just to satisfy a budget target.
- Heading absorption and structural block handling must be encoded as clear rules, not scattered conditionals.
- If chunking config changes, re-chunking behavior must remain explainable and testable.

Preferred style:

- small helpers with explicit names
- data in, data out
- no hidden reads from settings tables or environment variables

---

## Events and Projections

The event log model should shape the code.

Rules:

- Append events; do not mutate history.
- Projection rebuild code must be idempotent.
- Event application order must be explicit and deterministic.
- Projection code should tolerate replay from zero.
- Avoid mixing event append and projection interpretation in one dense function.

When implementing a feature, separate:

1. event creation
2. event persistence
3. projection application
4. read-model query

That separation makes rebuilds, debugging, and future migrations tractable.

---

## Configuration

Parsem's reading mechanics are product behavior. Treat config carefully.

- Keep config centralized and explicit.
- Avoid magic numbers in domain code.
- Name defaults close to the code that consumes them, or in a dedicated config module with clear ownership.
- Read configuration at the boundary, then pass it inward as values.

Do not:

- read environment variables deep inside business logic
- perform ad hoc stringly-typed config lookups throughout the codebase
- scatter fallback defaults across multiple layers

---

## Error Handling

- No bare `except:`.
- Catch broad exceptions only at boundaries where you can add context, convert them, or return a controlled failure.
- Preserve exception chaining with `raise ... from e`.
- Do not swallow parse, persistence, or projection errors silently.
- Use `sys.exit()` only in CLI entry points.

In this repo, a good error message should help answer:

- what operation failed
- on which document or file
- under which boundary, such as parse, store, or web

---

## Logging

- Prefer stdlib `logging`.
- Log at boundaries and operational seams, not inside every small helper.
- Do not replace return values with logging side effects.
- Do not use `print()` for diagnostics in application code.

Useful logging targets:

- ingestion start/failure/success
- projection rebuild start/failure/success
- schema migration steps
- unexpected boundary exceptions

---

## Testing

Test in layers.

### Domain tests first

- Pure functions get the highest test density.
- Test chunking, bucket math, projections, and selection logic without DB or web setup.
- Prefer parameterized tests for rule-heavy behavior.

### Integration tests second

- Use real SQLite.
- Use real files via `tmp_path`.
- Use FastAPI test clients for route-level behavior where needed.
- Keep integration fixtures small and representative.

### Test behavior, not implementation trivia

- Assert visible outputs, persisted facts, and projection results.
- Avoid tests that pin internal helper call counts unless there is no better signal.

### Golden-rule cases for this project

- heading absorption
- section boundary window reset semantics
- code/list/blockquote/table chunk rules
- empty-bucket countdown conditions
- free re-read behavior at or below high-water
- projection rebuild from event history
- rechunk re-anchoring via source offset overlap

---

## Formatting, Linting, and Tooling

Use boring tools consistently.

- Ruff for linting and formatting
- Pytest for tests
- Mypy if configured for the repo
- `pyproject.toml` as the tool configuration source
- `pathlib.Path` over `os.path`

Reasonable Ruff baseline:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM", "RUF"]

[tool.ruff.format]
quote-style = "double"
```

Use stricter rules only if they improve clarity rather than create churn.

---

## Security and Safety

- Never hardcode secrets.
- Validate uploaded filenames and content types at the boundary.
- Treat uploaded Markdown as untrusted input.
- Use `subprocess.run(..., check=True)` and avoid `shell=True` with user-controlled input.
- Keep file access scoped to intended data directories.

For this project, correctness of document boundaries and stored state matters as much as classical security hygiene.

---

## Scope Discipline

- Solve the problem the spec defines, not the imagined framework-generalized version.
- Record out-of-scope observations as `TODO(out-of-scope): ...` only when they are useful.
- Before implementing a non-trivial change, be able to state:
  - source of truth
  - pure transform
  - side-effect boundary
  - tests that should prove it

---

## Anti-Patterns

- domain code importing FastAPI, SQLite connection helpers, or filesystem APIs directly
- route handlers containing chunking, bucket, or projection business rules
- projection tables treated as canonical facts
- hidden reads of current time inside pure logic
- ad hoc dict payloads for core domain records
- broad mutable objects carrying state across unrelated operations
- "generic" abstractions added before there is a second real use
- swallowing exceptions and continuing with corrupted reading state
- clever one-liners where a short explicit block would be clearer

---

## Review Questions

When reviewing code, ask:

1. Is the source of truth obvious?
2. Is the business logic expressed as a pure transform where it can be?
3. Are framework and storage concerns kept at the boundary?
4. Are the data shapes explicit?
5. Would a Python reader find this direct and readable?
6. Can this be replayed, rebuilt, or re-run deterministically?
7. Are the tests proving product semantics rather than implementation accidents?

If the answer to several of these is "no", the code is not ready.
