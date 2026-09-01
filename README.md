# How the Pipeline Works

An end-to-end walkthrough of the Amsted AI Tax Agent ingestion pipeline: what
each module does, in the order it runs, and what happens to a document as it
moves through.

For the field-by-field metadata contract see `docs/METADATA_SCHEMA.md`.
For the command reference see `docs/RUNBOOK.md`.

---

## What the pipeline does

It converts tax source documents sitting in Azure Blob Storage into searchable,
citable retrieval units in Azure AI Search.

```text
Azure Blob Storage                 a PDF, .msg, .docx, .xlsx someone uploaded
        │
        ▼
Discovery          storage.py      list blobs, skip unsupported types
        │
        ▼
Extraction         extraction.py   get text out, routed by file type
        │           pdf_router.py  PDFs routed again by content
        ▼
Normalization      normalization.py  one canonical shape for every source
        │           cleaning.py       strip signatures, phones, disclaimers
        │           email_thread.py   split threads into individual messages
        │           email_headers.py  recover senders/dates from PDF exports
        ▼
Enrichment         enrichment.py   LLM assigns tax_topic, jurisdiction, …
        │
        ▼
Chunking           chunking.py     one chunk per message, or token windows
        │
        ▼
Embeddings         embeddings.py   Foundry model → vectors
        │
        ▼
Indexing           indexing.py     flat records → Azure AI Search
                    search_schema.py  the index definition

Throughout: pipeline.py orchestrates, storage.py records status,
utils.py provides deterministic IDs and artifact writing.
```

Scope **ends at indexing**. No chatbot, retrieval, orchestration, or UI.

---

## Stage 1 — Discovery

**`storage.py` → `BlobSource.list()`**

Lists blobs in the container. `pipeline.py` checks each name against
`extraction.is_supported()` and records unsupported files as `SKIPPED` rather
than failing them.

Before any work begins, `pipeline.py` computes:

```python
source_path = f"{container}/{blob_name}"
document_id = stable_id(source_path, prefix="doc_")
```

This matters. `document_id` derives from the blob path alone, so it exists even
if the download fails — which is what lets a status row be written on every
failure path.

---

## Stage 2 — Extraction

**`extraction.py` → `ExtractionRouter.extract()`**

A declarative map sends each extension to an extractor:

| Extensions | Extractor |
|---|---|
| `.msg` | extract-msg |
| `.eml` | Python `email` (stdlib) |
| `.csv` `.tsv` | csv → Markdown table |
| `.txt` `.log` `.json` | plain text |
| `.pdf` | **routed again — see below** |
| `.docx` `.pptx` `.xlsx` `.md` `.html` | Docling |

Adding a format means writing an extractor and adding it to `_EXTRACTORS`.
Nothing else changes.

### PDFs get a second routing decision

**`pdf_router.py` → `extract_pdf()`**

Docling parses an Outlook header block as a *table* and serializes it
column-wise, producing labels and values in separate runs. That destroys sender
and date recovery. So PDFs route on content, not extension:

```text
no text layer          → Docling (OCR required)
text layer + email     → pdfplumber (preserves inline "From: Name")
text layer + tables    → Docling (table structure matters)
otherwise              → pdfplumber, Docling on failure
```

The chosen engine is recorded in `extraction_metadata.extractor`, so you can
audit which path each document took after a run.

pdfplumber emits `<!-- page N -->` markers between pages so page numbers survive
into chunk metadata.

### Email files parse their thread immediately

`.msg` and `.eml` have real headers, so the extractor calls `build_thread()` and
returns `is_email_thread=True` with a populated `messages` list. PDFs do not —
their thread detection happens in the next stage.

**Output:** `ExtractedDocument` — raw text, extractor name, page count,
format-specific metadata, and (for native email) parsed messages.

---

## Stage 3 — Normalization

**`normalization.py` → `normalize()`**

The stage that turns anything into one canonical shape. Four things happen.

### 3a. Header unwrapping

**`email_headers.py` → `unwrap_header_lines()`**

pdfplumber preserves the PDF's visual line wrapping, so a long `Cc:` spills onto
the next line with no label:

```text
Cc: Sherlock, Kyle <k@amsted.com>; Jackson, Dezarae <d@amsted.com>; Castillo, Dan
<dcastillo@amsted.com>
Subject: Re: SARs - Netherlands
```

Left alone, the orphan line becomes a message body and `Subject:` never matches.
Unwrapping folds it back. Only lines containing `<`, `@`, or `;` that aren't
sentences are folded, so body prose is never absorbed.

### 3b. Thread detection

`looks_like_email_thread()` scans for `From:` / `Sent:` / `-----Original
Message-----` / `On … wrote:` patterns. A PDF or DOCX containing them is treated
as an email export and segmented like a native `.msg`.

### 3c. Thread segmentation

**`email_thread.py` → `split_thread()`**

Finds message boundaries two ways:

- `_HEADER_BLOCK` — Outlook `From:/Sent:/To:/Cc:/Subject:` blocks
- `ON_WROTE` — `On Aug 2, 2023, at 3:45 PM, Sherlock, Kyle <k@a.com> wrote:`

Every captured boundary passes through `clean_boundary()`, which blanks values
that are really header labels and rejects flattened header rows.

Each message body is cleaned, then junk is dropped via `is_junk_message()` —
the mailbox owner's name Outlook prints at the top, orphaned address lines, bare
labels. Genuinely short replies like *"Thanks, that works for me"* are kept.

Finally messages are reversed. Outlook threads are top-posted, so this makes
`message_index=1` the **earliest** message.

### 3d. Cleaning

**`cleaning.py`**

Applied to message bodies and to non-email documents:

| Removed | How |
|---|---|
| Signature blocks | delimiter, sign-off anchor, or contact-density scan |
| Phone numbers | → `[phone removed]` |
| Street addresses, city/state/ZIP | → `[address removed]` |
| Confidentiality and Circular 230 disclaimers | pattern match |
| Quoted-reply markers | leading `>` |
| Page markers | Docling artifacts |
| Repeated running headers/footers | line appearing on 4+ pages |

Redactions leave **visible placeholders** so removals stay auditable. Email
addresses are preserved by default — sender identity is meaningful tax metadata.

Signature detection is deliberately conservative. Over-deletion in a tax corpus
is worse than a stray line, so it anchors on explicit signals rather than
guessing.

### 3e. Deterministic metadata

Everything the source can supply is populated here, never guessed:

```text
source_id  source_file  source_type  source_date  author  page_number
thread_subject  message_date  scenario_id(s)  content_hash  char_count
```

`tax_year_or_effective_period` is attempted by regex ("tax year 2024"). The LLM
only sees it if the regex finds nothing.

Scenario IDs come from an optional `scenarios.json` manifest keyed by blob path.

**Output:** `NormalizedDocument` — cleaned content, ordered messages, and
metadata with semantic fields still `n/a`.

---

## Stage 4 — LLM Metadata Enrichment

**`enrichment.py` → `MetadataEnricher.enrich()`**

Calls a chat model deployed in Azure AI Foundry at `temperature=0` in JSON mode.
It fills only what the source cannot:

```text
tax_topic  jurisdiction  legal_entity  business_unit  authority_level
scenario_id (fallback only)
```

Three guardrails:

**Controlled vocabularies.** Returned values are coerced onto closed lists via
`vocabularies.coerce()` — exact match, then alias (`"SARs"` →
`Equity Compensation`, `"The Netherlands"` → `Netherlands`), then substring, then
fallback to `n/a`. Unrecognized values are never trusted, so search facets stay
clean.

**Deterministic wins.** A corpus-assigned `scenario_id` and a regex-parsed tax
year always override the model.

**Governance stays untouched.** `validity_status`, `current_or_superseded`, and
`access_classification` remain `n/a` — Amsted hasn't supplied the scheme, and
the prompt doesn't ask for them.

Failure never blocks ingestion. A failed call sets `enrichment_status="failed"`
and writes the reason into `enrichment_rationale`.

---

## Stage 5 — Chunking

**`chunking.py` → `Chunker.chunk()`**

### The guard

Before anything else:

```python
if require_enrichment and doc.metadata.enrichment_status != "ok":
    raise EnrichmentMissingError(...)
```

This is the safeguard against silent metadata loss. Without it, an unenriched
document produces chunks with `n/a` in every field and nothing reports a problem.

### Two strategies

**Email threads → `email_message`.** One chunk per message. Each carries its own
sender, date, page number, and position in the thread. Headers are prepended into
the chunk content so context survives retrieval:

```text
Subject: SARs - Netherlands
From: Franson, Marilyn <MDF@amsted.com>
To: Lopez, Tristan <tlopez@amsted.com>
Date: 2023-08-02T18:06:00

The Netherlands is very pro employee…
```

Messages exceeding the token budget are sub-split but keep their
`message_index`. Citation: `file.pdf#message=2&page=3&part=1`.

**Everything else → `token`.** Paragraph-aware windows with overlap. Oversized
paragraphs fall back to sentence splitting, then to a hard token split. Overlap
carries whole units so context isn't severed mid-argument.

### Metadata inheritance

Every chunk receives the full document metadata. Message chunks override
`author`, `message_date`, `page_number`, and set `source_type="Email Message"`.

**Output:** `Chunk[]` with `content_vector=None`.

---

## Stage 6 — Embeddings

**`embeddings.py` → `EmbeddingGenerator.apply()`**

Batched calls to the Foundry embedding deployment, with exponential-backoff
retry. Deployment name comes from configuration — never hardcoded.

Verifies the returned vector width matches `EMBEDDING_DIMENSIONS` and raises
immediately on mismatch, rather than failing later at upload with an opaque
error. `dimensions` is only sent for `text-embedding-3` models, which is the
only family that accepts it.

Vectors are set in place on each chunk.

---

## Stage 7 — Indexing

**`indexing.py` → `upload()`**

Converts each chunk with `Chunk.to_search_record()`, which **flattens** the
nested metadata into top-level fields. Sending `model_dump()` instead produces a
nested `metadata` object the index doesn't declare, and every field lands empty.

Uploads in batches of 100. Raises on any failure rather than silently reporting
partial success.

**`search_schema.py` → `build_index()`** is the single source of truth for the
index: HNSW vector profile, semantic reranking configuration, and every metadata
field marked filterable and facetable so the agent can scope retrieval by
jurisdiction, topic, scenario, entity, or authority level.

---

## Orchestration

**`pipeline.py` → `Pipeline.run()`**

Iterates blobs and calls `process_blob()` for each. Three behaviours worth
knowing:

### Status is written on every path

```text
DISCOVERED → DOWNLOADED → EXTRACTED → NORMALIZED → ENRICHED
           → CHUNKED → EMBEDDED → INDEXED
                    └──────────→ FAILED / ENRICH_FAILED / SKIPPED / DRY_RUN
```

Because `document_id` is computed before any work, a row exists even when
download or extraction fails. A crashed run still shows how far each document
got.

### One bad document never stops the run

Failures are logged, recorded in Table Storage with the error message, written
to `indexing_logs/*.error.json`, and the loop continues. `run_pipeline.py` exits
non-zero if anything failed.

### Enrichment failure is a policy choice

```python
Pipeline(settings, on_enrichment_failure="index_anyway")  # default
Pipeline(settings, on_enrichment_failure="fail")
```

A rate-limited LLM call shouldn't discard a successfully extracted memo, so the
default indexes it with `n/a` metadata and counts it in `enrichment_failures`.

---

## Supporting modules

| Module | Role |
|---|---|
| `config.py` | All settings from `.env`. Nothing hardcoded. |
| `models.py` | Pydantic contracts between every stage. |
| `utils.py` | Deterministic IDs, logging, artifact JSON writing. |
| `vocabularies.py` | Controlled vocabularies and coercion aliases. |
| `storage.py` | Blob download + Table Storage status tracking. |

---

## Determinism

Re-running the pipeline **upserts** rather than duplicating, because no ID
depends on content:

| Value | Derivation |
|---|---|
| `document_id` | `sha256(source_path)[:16]` |
| `source_id` | `sha256(document_id)[:16]` |
| `content_hash` | `sha256(normalized content)` |
| `chunk_id` (token) | `sha256(document_id\|chunk_number)[:16]` |
| `chunk_id` (message) | `sha256(document_id\|msg\|index\|part)[:16]` |

`content_hash` is the change detector — it's how `--force` decides whether a
document needs reprocessing.

Changing `CHUNK_SIZE_TOKENS` or `CHUNK_OVERLAP_TOKENS` changes chunk boundaries
for token-strategy documents, so recreate the index after adjusting them.

---

## Artifacts

Every stage writes an inspectable file under `OUTPUT_DIR`:

```text
sample_outputs/
├── raw/              downloaded blobs
├── extracted/        raw extractor output
├── normalized/       canonical document JSON
├── enriched/         + LLM metadata
├── chunked/          chunk records
├── embeddings/       dimensions + preview, not full vectors
└── indexing_logs/    per-document results, _run_summary.json
```

Full vectors are deliberately excluded — at 3072 floats per chunk they would add
hundreds of megabytes per run.

This directory is gitignored. It contains client data.

---

## Walkthrough: one email thread

`Netherlands_SARS.pdf`, a five-message Outlook thread printed to PDF.

| Stage | What happens |
|---|---|
| Discovery | `document_id = doc_9eaf947fd4465…` from the blob path |
| Extraction | Text layer present + email signals → **pdfplumber**, not Docling |
| Unwrap | Marilyn's wrapped `Cc:` folded back, Castillo's address recovered |
| Detection | 3 thread signals → segment as a thread |
| Segmentation | 5 messages; `Lopez, Tristan` mailbox artifact dropped |
| Cleaning | Marilyn's signature block and phone removed |
| Normalization | `source_date` = earliest message, `author` = Kyle Sherlock |
| Enrichment | `Equity Compensation` / `Netherlands` / `Internal Email`, confidence 0.9 |
| Chunking | 5 chunks, one per message, each with its own sender and date |
| Embeddings | 5 vectors from the Foundry deployment |
| Indexing | 5 flat records, citations `Netherlands_SARS.pdf#message=N` |

The agent can now retrieve *"who at Amsted decided how Dutch SARs are taxed"* and
cite the specific message, from the specific person, on the specific date.

---

## Extending it

| Change | Where |
|---|---|
| New file type | Extractor in `extraction.py`, register in `_EXTRACTORS` |
| New vocabulary value | `vocabularies.py` — no index rebuild needed |
| New metadata field | `models.py`, `to_search_record()`, `search_schema.py`, then rebuild |
| Different chunking | `chunking.py` — interface is `chunk(doc) -> list[Chunk]` |
| Different embedding model | `.env`, then recreate the index if dimensions change |

---

## Deliberately not built

- **Event-driven ingestion.** An Azure Function with a Blob trigger would change
  only the entry point, not the processing code.
- **LLM thread segmentation.** Considered and rejected as a default: a model
  returning message *text* breaks verbatim citation, and non-determinism breaks
  `chunk_id` stability. Viable only as a validated fallback returning line
  numbers.
- **Incremental deletion.** `content_hash` handles updates; removal of deleted
  blobs from the index is not yet implemented.
