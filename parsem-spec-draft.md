# Parsem Project Specification

## 1. Project Summary

**Parsem** is a self-hosted, local-first web application for deep reading of documents using a **Progressive Reveal Reading** interface.

The application allows a reader to upload or import a document, process it into readable semantic nodes, and move through the document deliberately. Instead of free scrolling, the reader reveals one part of the document at a time and only progresses when they explicitly choose to settle the current passage.

The MVP is intentionally narrow:

- Markdown-first
- PDF support only via Pandoc conversion to Markdown
- No images
- No scanned PDFs
- No complex layout preservation
- No multi-user support
- Local database and local file storage
- Optional later support for local LLM tutoring

The purpose of the MVP is to validate the Progressive Reveal Reading interaction model, not to solve universal document parsing.

---

## 2. Product Aim

Parsem aims to help readers engage more deeply with documents that require thought, attention, and judgement.

Standard digital reading interfaces optimise for speed, scrolling, search, and skimming. Parsem deliberately introduces a small amount of productive friction so the reader must make conscious progress through the document.

The product thesis is:

> Some documents deserve more than skimming. Parsem creates a reading chamber where text is revealed deliberately, uncertainty can be acknowledged, and progress is earned rather than accidental.

---

## 3. Core Goals

### 3.1 Validate Progressive Reveal Reading

The central goal is to test whether a document reader that controls reveal and progression improves attention, comprehension, and reader self-awareness.

The MVP should answer:

- Does progressive reveal feel useful rather than annoying?
- Do readers notice uncertainty earlier?
- Does deliberate settling change the reading posture?
- Is concealment meaningful as a reader action?
- Does returning to a previous semantic point improve resumption?

### 3.2 Build a Minimal Self-Hosted Reading App

The app should run on a home server with minimal dependencies.

It should provide:

- A browser-based interface
- A document library
- Markdown upload
- Experimental PDF-to-Markdown import via Pandoc
- Local SQLite database
- Local file storage
- Persistent reading state
- Keyboard-first reader interaction

### 3.3 Establish a Foundation for Later Tutor Features

The MVP should leave room for a local LLM tutor layer, but it should not depend on it.

Future tutor capabilities may include:

- Explain current passage
- Summarise revealed-so-far
- Ask comprehension questions
- Identify key concepts
- Show themes and claims
- Support document-grounded Q&A

The reader must remain useful without an LLM.

---

## 4. Non-Goals for MVP

The MVP explicitly does **not** attempt to solve:

- General-purpose PDF parsing
- Scanned PDF OCR
- Image extraction or rendering
- Complex tables
- Layout-faithful document display
- Multi-user access control
- Mobile-native app development
- Full annotation workflows
- Vector search
- Agent workflows
- Spaced repetition
- Full document chat
- Production authentication

These may be future features, but they are out of scope for the first build.

---

## 5. Target User

The initial user is a single reader operating a self-hosted instance.

The reader wants to deeply engage with documents such as:

- Strategy papers
- Policy documents
- Research papers converted to Markdown
- Governance documents
- Technical documents
- Long essays
- Board papers
- Internal proposals

The reader is not trying to consume documents quickly. They are trying to understand, remember, question, and think with them.

---

## 6. Core User Story

> As a reader, I want to upload a document I need to read deeply, so that Parsem can reveal it progressively, allow me to settle each passage before moving on, and help me avoid skimming past uncertainty.

Supporting stories:

> As a reader, I want to see a library of previously uploaded documents, so I can resume reading where I left off.

> As a reader, I want Parsem to reopen a document slightly before my last position, so I can re-enter the argument with context.

> As a reader, I want to conceal a passage if I am not ready for it, so that I can return to the previous settled point.

> As a reader, I want to persist a passage, so that I can keep it visible while I check later material.

> As a reader, I want subtle signals such as reading weight and key terms, so that I can notice dense or important passages.

---

## 7. Core Interaction Model

Parsem is built around five primary reader actions:

1. **Reveal**
2. **Settle**
3. **Conceal**
4. **Persist**
5. **Ask**

### 7.1 Reveal

Reveal brings the next reading node into view.

The reader uses this when they are ready to see the next part of the document.

### 7.2 Settle

Settle marks the current node as accepted into reading progress.

Important rule:

> Revealed does not equal settled.

A reader has only progressed when they settle the current node.

### 7.3 Conceal

Conceal hides the current revealed node and returns the reader to the previous settled state.

This represents:

> I am not ready for this yet.

Concealment is not failure. It is a first-class reading action.

### 7.4 Persist

Persist keeps a node visible or pinned while the reader continues.

This represents:

> I need to hold this in working memory.

Persisted nodes may later appear in a side panel or pinned reference area.

### 7.5 Ask

Ask opens an assistance panel for the current node.

For MVP this can be a placeholder or notes panel. In later versions it will connect to a local LLM tutor.

---

## 8. Keyboard Controls

The reader should be keyboard-first.

Suggested MVP controls:

| Key | Action | Meaning |
|---|---|---|
| Space | Reveal next node | Show me the next thought |
| Enter | Settle current node | I am comfortable enough to continue |
| Backspace | Conceal current node | I am not ready for this |
| P | Persist/unpersist current node | Keep this visible as reference |
| U | Mark unclear | I can continue, but this needs attention |
| ? | Ask / open assist panel | Help me understand this |
| Esc | Focus mode / close panel | Return attention to reading |

Mouse/touch controls can mirror these actions, but keyboard controls define the core interaction grammar.

---

## 9. Reader Experience

### 9.1 Library View

On launch, the user sees a document library.

Each document card or row should show:

- Title
- Source type
- Status
- Reading progress
- Last opened time
- Current section or resume point
- Import warning if applicable

Example:

```text
Parsem

[Upload Markdown] [Import PDF via Pandoc]

Documents
- Responsible Use of AI Policy       37%   Resume
- Research Paper on Attention        Ready  Start
- Board Paper May                    Processing...
- Converted Vendor PDF               Ready  Warning: converted from PDF
```

### 9.2 Upload View

The upload screen supports:

- Markdown file upload
- PDF file upload, converted to Markdown using Pandoc

Later ingestion paths may include:

- URL import
- API upload
- Drag/drop
- Paste raw text

### 9.3 Document Opening

When a document opens for the first time, the reader initially sees only the title.

Example:

```text
Responsible Use of AI Policy

[Begin]
```

The document should not immediately spill its content. The reader enters the document deliberately.

### 9.4 Resume Behaviour

When reopening a document, Parsem should resume at or just before the last settled node.

MVP behaviour:

- Store `last_settled_position`
- On resume, open at the nearest previous heading or earlier node
- Allow the reader to continue from there

Future behaviour:

- Resume from previous semantic nodal point
- Offer options:
  - Resume from previous concept
  - Resume exactly where I left off
  - Start section again

### 9.5 Reading Surface

The reading surface should be minimal and low-distraction.

Suggested layout:

```text
Document Title
Current Section

Previously settled text, slightly faded

Current revealed node in full focus

[Settle] [Conceal] [Persist] [Ask]

Reading weight: Medium
```

The UI should feel closer to a reading chamber than a PDF viewer.

---

## 10. Document Model

The MVP document model is a linear list of typed reading nodes derived from Markdown.

A full semantic AST is not required for the MVP.

### 10.1 Supported Node Types

Initial node types:

- `title`
- `heading`
- `paragraph`
- `list`
- `blockquote`
- `code`
- `horizontal_rule`
- `image_placeholder`

Images are not rendered in the MVP. Markdown image syntax should be converted to a placeholder node or omitted with a warning.

### 10.2 Reading Node

A reading node is the atomic unit of reveal.

Example:

```json
{
  "id": 123,
  "document_id": 45,
  "position": 37,
  "node_type": "paragraph",
  "level": null,
  "text": "This policy establishes the requirements for responsible use of AI systems...",
  "reading_weight": 0.68,
  "metadata": {
    "key_terms": ["policy", "AI systems", "responsible use"],
    "obligation_terms": ["requirements"]
  }
}
```

### 10.3 Reading Tree Later

Later versions may evolve from a linear node list to a richer tree:

```text
Document
  Section
    Heading
    Paragraph
    Paragraph
  Section
    Heading
    List
    Paragraph
```

For the MVP, the linear list plus heading levels is enough.

---

## 11. Reading Weight and Semantic Signals

The MVP should include a simple reading weight signal.

User-facing label:

> Reading weight

Internal term:

> Semantic density

### 11.1 MVP Reading Weight Heuristic

The first version can use a simple heuristic based on:

- Node length
- Unique significant words
- Capitalised terms
- Obligation words
- Sentence count

Example obligation words:

```text
must
should
may
requires
required
prohibits
permits
approves
reviews
escalates
```

### 11.2 Display

Display subtly:

```text
Reading weight: Light
Reading weight: Medium
Reading weight: Heavy
```

or:

```text
Reading weight: ▂▆
```

Do not overemphasise the signal. It is an aid, not the main content.

### 11.3 Highlights

MVP highlight layers may include:

- Key terms
- New terms
- Obligation words

Highlights should be subtle and optional.

Avoid excessive colour or visual noise.

---

## 12. Ingestion Scope

### 12.1 Markdown

Markdown is the primary supported input format.

Processing steps:

```text
Upload .md
  → store original file
  → parse Markdown into blocks
  → create reading nodes
  → compute reading weight
  → save nodes
  → mark document ready
```

### 12.2 PDF via Pandoc

PDF support is experimental and implemented via Pandoc conversion to Markdown.

Processing steps:

```text
Upload .pdf
  → store original PDF
  → run Pandoc to produce Markdown
  → store converted Markdown
  → parse Markdown into nodes
  → compute reading weight
  → save nodes
  → mark document ready with conversion warning
```

User-facing warning:

```text
Converted from PDF using Pandoc. Formatting may be imperfect. Images are not included in this MVP.
```

### 12.3 Images

Images are out of scope for the MVP.

Markdown image syntax should produce either:

```text
[Image omitted: alt text]
```

or a skipped node with an import warning.

---

## 13. Technical Architecture

### 13.1 Minimal System

```text
Browser UI
   ↓
Web App / API Server
   ↓
SQLite Database
   ↓
Local File Storage

Background Worker
   ↓
Document Processing
   ↓
Pandoc where required
```

### 13.2 Foreground Loop

The foreground loop handles reading interaction:

```text
User opens document
  → reveal node
  → settle/conceal/persist
  → save reading event
  → update reading state
  → render next view
```

### 13.3 Background Loop

The background loop handles processing:

```text
New document uploaded
  → create processing job
  → worker picks up job
  → extract/convert/parse
  → create nodes
  → mark ready or failed
```

---

## 14. Suggested Tech Stack

### Backend

**Python FastAPI**

Rationale:

- Simple web API
- Good document processing ecosystem
- Easy integration with Pandoc and local LLMs
- Fast enough for single-user self-hosted use

### Frontend

**Jinja templates + HTMX + small amount of JavaScript**

Rationale:

- Minimal frontend complexity
- Server-rendered HTML is sufficient
- HTMX allows interactive partial updates without a full SPA
- Keyboard handling can be done with lightweight JavaScript

### Database

**SQLite**

Rationale:

- Excellent for self-hosted single-user app
- No database server required
- Easy backup
- Good enough for documents, nodes, events, and state

### Storage

**Local filesystem**

Suggested structure:

```text
data/
  Parsem.db
  originals/
  markdown/
  processed/
```

### Background Jobs

**SQLite-backed jobs table + worker process**

Avoid Celery, Redis, or other infrastructure for MVP.

### Document Conversion

**Pandoc**

Used only for PDF-to-Markdown conversion during MVP testing.

### Markdown Parsing

Use a Python Markdown parser capable of block-level parsing.

Possible options:

- `markdown-it-py`
- `mistune`
- `python-markdown`

Prefer a parser that exposes tokens or block structure.

### Local LLM Later

Optional future support:

- Ollama
- LM Studio local server
- OpenAI-compatible local endpoint

---

## 15. Proposed Project Structure

```text
Parsem/
  app/
    main.py
    config.py
    db.py
    models.py
    routes/
      library.py
      upload.py
      reader.py
      api.py
    services/
      ingest.py
      markdown_parse.py
      pandoc.py
      reading_weight.py
      jobs.py
      llm.py
    templates/
      base.html
      library.html
      upload.html
      reader.html
      document_status.html
    static/
      app.css
      reader.js
  data/
    Parsem.db
    originals/
    markdown/
    processed/
  worker.py
  README.md
```

---

## 16. Database Schema — MVP

### 16.1 documents

```sql
CREATE TABLE documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  source_type TEXT NOT NULL,
  original_path TEXT,
  markdown_path TEXT,
  status TEXT NOT NULL,
  import_warning TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Suggested `source_type` values:

- `markdown`
- `pandoc_pdf_markdown`

Suggested `status` values:

- `uploaded`
- `processing`
- `ready`
- `failed`

### 16.2 document_nodes

```sql
CREATE TABLE document_nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL,
  position INTEGER NOT NULL,
  node_type TEXT NOT NULL,
  level INTEGER,
  text TEXT NOT NULL,
  reading_weight REAL,
  metadata_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id)
);
```

### 16.3 reading_state

```sql
CREATE TABLE reading_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL UNIQUE,
  last_settled_position INTEGER DEFAULT 0,
  current_position INTEGER DEFAULT 0,
  persisted_node_ids_json TEXT,
  unclear_node_ids_json TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id)
);
```

### 16.4 reading_events

```sql
CREATE TABLE reading_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL,
  node_id INTEGER,
  event_type TEXT NOT NULL,
  event_data_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id),
  FOREIGN KEY(node_id) REFERENCES document_nodes(id)
);
```

Suggested `event_type` values:

- `reveal`
- `settle`
- `conceal`
- `persist`
- `unpersist`
- `mark_unclear`
- `unmark_unclear`
- `open_document`
- `close_document`

### 16.5 jobs

```sql
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL,
  document_id INTEGER,
  payload_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  FOREIGN KEY(document_id) REFERENCES documents(id)
);
```

---

## 17. API and Routes

### 17.1 Browser Routes

```text
GET  /
GET  /library
GET  /upload
POST /upload
GET  /documents/{document_id}/reader
POST /documents/{document_id}/reveal
POST /documents/{document_id}/settle
POST /documents/{document_id}/conceal
POST /documents/{document_id}/persist
POST /documents/{document_id}/unclear
```

For HTMX, action routes can return HTML fragments.

### 17.2 API Routes Later

```text
GET  /api/documents
POST /api/documents
GET  /api/documents/{document_id}
GET  /api/documents/{document_id}/nodes
GET  /api/documents/{document_id}/state
POST /api/documents/{document_id}/events
```

API upload can be added after the browser flow is stable.

---

## 18. Background Worker Design

The MVP worker can be a simple Python process.

Pseudo-loop:

```python
while True:
    job = get_next_pending_job()
    if not job:
        sleep(2)
        continue

    mark_job_running(job)

    try:
        run_job(job)
        mark_job_complete(job)
    except Exception as e:
        mark_job_failed(job, str(e))
```

Initial job type:

```text
process_document
```

Processing logic:

```text
If source_type is markdown:
  parse Markdown directly

If source_type is pdf:
  run Pandoc to Markdown
  parse resulting Markdown
```

---

## 19. Error Handling and Import Warnings

Parsem should be honest about import quality.

For MVP, common warnings:

```text
Converted from PDF using Pandoc. Formatting may be imperfect.
Images are not included in this MVP.
No headings detected. Document will be read as a simple flow.
Some Markdown blocks could not be classified cleanly.
```

If processing fails:

```text
Document failed to process.
Reason: Pandoc conversion failed.
```

The library should show failed documents clearly and allow deletion or retry later.

---

## 20. UI Design Principles

### 20.1 Minimal

The UI should be calm, sparse, and text-first.

Avoid dashboards, complex panels, or heavy visual decoration in the MVP.

### 20.2 Keyboard-First

Keyboard interaction is not an add-on. It is the primary reading mode.

### 20.3 Productive Friction

The app should slow the reader gently.

Micro-delay after reveal or settle may be added later, but the core MVP already creates friction by separating reveal from settle.

### 20.4 No False Cleverness

Do not pretend to understand the document more deeply than the parser allows.

If structure is weak, treat the document as flow.

### 20.5 Source Text Integrity

The app may clean whitespace and formatting, but it should not silently rewrite source text.

LLM-generated explanations must remain separate from the document text.

---

## 21. MVP Build Plan

### Phase 1 — Reading Mechanic Prototype

Goal: prove Progressive Reveal Reading with a hardcoded Markdown file.

Build:

- Markdown parsing
- Node list generation
- Reader screen
- Reveal
- Settle
- Conceal
- Persist
- Basic keyboard controls

No upload required yet.

### Phase 2 — Library and Markdown Upload

Goal: make the app usable with uploaded Markdown.

Build:

- Library screen
- Upload screen
- Store original Markdown
- Parse uploaded Markdown
- Save nodes
- Resume state
- Delete document

### Phase 3 — Reading State and Events

Goal: make sessions durable and inspectable.

Build:

- Reading state persistence
- Reading events
- Resume from previous heading/current position
- Persisted node tracking
- Unclear node tracking

### Phase 4 — Reading Weight and Highlights

Goal: add simple semantic signals.

Build:

- Reading weight heuristic
- Key term extraction
- Obligation word highlighting
- Subtle display in reader

### Phase 5 — Pandoc PDF Import

Goal: allow experimental PDF-to-Markdown reading.

Build:

- PDF upload
- Pandoc conversion
- Store converted Markdown
- Parse converted Markdown
- Show conversion warning

### Phase 6 — Local Tutor Placeholder

Goal: prepare for local LLM support.

Build:

- Ask panel UI
- Current node context
- Seen-so-far context builder
- Placeholder response
- Later connect to Ollama/local endpoint

---

## 22. Future Features

Potential later enhancements:

- Local LLM tutor
- Document-grounded Q&A
- Explain current node
- Summarise revealed-so-far
- Ask comprehension questions
- Concept extraction
- Theme detection
- Semantic nodal resume
- Better PDF parsing
- OCR
- Image support
- Tables
- URL import
- API ingestion
- Export notes
- Spaced review
- Multi-user mode
- Authentication
- Reader analytics
- Document structure repair UI

---

## 23. Success Criteria for MVP

The MVP is successful if:

1. A user can upload Markdown and read it through progressive reveal.
2. A user can settle, conceal, persist, and resume reading.
3. The reading experience feels meaningfully different from scrolling.
4. The app preserves reading state reliably.
5. Markdown-derived reading nodes are good enough for real use.
6. Pandoc PDF conversion is usable for controlled test documents.
7. The system remains small enough to understand and self-host.

The MVP is not judged by how clever the AI is. It is judged by whether the reading mechanic works.

---

## 24. One-Sentence Definition

**Parsem is a self-hosted Markdown-first deep reading app that turns documents into a deliberate progressive reveal experience, allowing readers to reveal, settle, conceal, persist, and eventually ask for local tutor assistance as they move through dense material.**

