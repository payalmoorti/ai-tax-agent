# Metadata Schema — Seed Corpus v0.1

The metadata contract for the Amsted AI Tax Agent ingestion pipeline. Every field
travels with the document, each email message, and each chunk.

`DocumentMetadata` in `src/amsted_tax_ingestion/models.py` is the single source of
truth. `Chunk.to_search_record()` flattens it into the Azure AI Search document
shape defined in `search_schema.py`.

**Unknown values are the string `"n/a"`** — never an empty string, never `null`.
This is deliberate: an empty string is indistinguishable from "we never tried,"
which is exactly how metadata silently went missing in an earlier revision.

---

## Contract flow

```text
BlobDocumentRef
      │
      ▼
ExtractedDocument          raw extractor output + parsed EmailMessage[]
      │
      ▼
NormalizedDocument         canonical form; deterministic metadata populated
      │
      ▼
EnrichedDocument           same object, semantic metadata filled by the LLM
      │
      ▼
Chunk                      metadata inherited; message fields overridden
      │
      ▼
SearchRecord               flat dict for Azure AI Search
```

---

## The 18 fields

### Identity and provenance

| Field | Type | Populated by | Notes |
|---|---|---|---|
| `scenario_id` | str | Corpus manifest, else enricher | Primary scenario. Corpus-assigned values always win. |
| `scenario_ids` | list[str] | Corpus manifest | Multi-valued, per "Scenario ID(s)" in the action plan. |
| `source_id` | str | Normalization | Deterministic `src_` + sha256 of `document_id`. |
| `source_file` | str | Normalization | Original file name. |
| `source_type` | str | Normalization | Controlled vocabulary — see below. |
| `source_date` | str | Normalization | ISO 8601. Earliest message in a thread; upload date otherwise. |
| `author` | str | Normalization | Earliest sender on the document; per-message sender on message chunks. |
| `page_number` | int \| None | Normalization | From Docling page markers; per-message on thread chunks. |

### Tax classification

| Field | Type | Populated by | Notes |
|---|---|---|---|
| `tax_topic` | str | Enricher | Controlled vocabulary. |
| `jurisdiction` | str | Enricher | Controlled vocabulary. |
| `tax_year_or_effective_period` | str | Normalization → Enricher | Regex first; LLM only if not found deterministically. |
| `authority_level` | str | Enricher | Controlled vocabulary, ordered by authority. |

### Organizational

| Field | Type | Populated by | Notes |
|---|---|---|---|
| `legal_entity` | str | Enricher | Free text, e.g. "Amsted Rail Company, Inc." `n/a` if unnamed. |
| `business_unit` | str | Enricher | Free text, e.g. "Corporate Tax". `n/a` if not stated. |

### Email-specific

| Field | Type | Populated by | Notes |
|---|---|---|---|
| `thread_subject` | str | Normalization | `Re:`/`Fwd:` stripped so all messages share one value. |
| `message_date` | str | Normalization | Latest message on the document; exact message date on message chunks. |

### Governance — deferred

| Field | Current value |
|---|---|
| `validity_status` | `n/a` |
| `current_or_superseded` | `n/a` |
| `access_classification` | `n/a` |

These three exist in the model **and** in the search index as filterable and
facetable, so Amsted can backfill them later **without an index rebuild**.

The enrichment prompt does not ask for them. `test_metadata_enrichment.py` asserts
they remain `n/a` and warns if the model populates them anyway:

```text
GOVERNANCE FIELDS (must remain n/a)
  validity_status                  n/a
  current_or_superseded            n/a
  access_classification            n/a

  OK - all three correctly left as n/a.
```

Placeholder vocabularies are already defined in `models.py` for when the scheme
arrives:

```python
VALIDITY_STATUSES     = ["Valid", "Under Review", "Invalid", "n/a"]
CURRENT_OR_SUPERSEDED = ["Current", "Superseded", "n/a"]
ACCESS_CLASSIFICATIONS = ["Public", "Internal", "Confidential", "Restricted", "n/a"]
```

---

## Enrichment provenance

Not part of the business schema, but carried on every document and chunk so a
classification can be audited later.

| Field | Type | Notes |
|---|---|---|
| `enrichment_status` | str | `pending` \| `ok` \| `failed` \| `skipped` |
| `enrichment_confidence` | float | 0.0–1.0 |
| `enrichment_rationale` | str | One sentence; holds the error message on failure |
| `enrichment_model` | str | Deployment name used |
| `enrichment_timestamp` | str | ISO 8601 |

`enrichment_status` is the guard that prevents silent metadata loss. `Chunker`
raises `EnrichmentMissingError` unless the status is `ok`:

```text
ERROR: thread.msg: enrichment_status is 'pending', not 'ok'.

Either run:  python scripts/test_metadata_enrichment.py doc_a1b2c3d4
or re-run this script with --skip-enrichment-check.
```

---

## Controlled vocabularies

Defined in `models.py`. The enricher **coerces** the model's answer onto these
lists — anything unrecognized falls back rather than being trusted, so search
facets stay clean.

### `source_type`

```text
Email Thread · Email Message · Tax Memo · Tax Research · Working Paper
Statute · Regulation · Agency Guidance · Court Decision · Ruling
Financial Statement · Spreadsheet · Presentation · Other
```

Assigned deterministically from the file extension, overridden to `Email Thread`
when thread segmentation succeeds, and to `Email Message` on individual message
chunks.

### `jurisdiction`

```text
Federal · Multistate · Illinois · Indiana · Ohio · Michigan · Wisconsin
Texas · California · New York · Pennsylvania · Canada · Mexico
International · Unknown
```

### `tax_topic`

```text
Nexus · Apportionment · State Income Tax · Sales and Use Tax
Transfer Pricing · Foreign Tax Credit · Tax Depreciation
Tax Credits and Incentives · Entity Structuring · Withholding
Property Tax · Indirect Tax · Tax Provision · Audit Defense · Other
```

### `authority_level`

Ordered from most to least authoritative:

```text
Statute · Regulation · IRS Publication · Federal Guidance · State Guidance
Case Law · Legal Opinion · External Tax Research · Internal Tax Research
Internal Memo · Internal Email · Unknown
```

This ordering matters downstream: the agent should prefer higher-authority
evidence and disclose when an answer rests only on internal correspondence.

---

## Deterministic vs. LLM-populated

The split is deliberate. Anything the source can supply is never guessed.

| Deterministic (normalization) | LLM (enrichment) | Deferred |
|---|---|---|
| `source_id` `source_file` `source_type` `source_date` `author` `page_number` `thread_subject` `message_date` `scenario_id(s)` | `tax_topic` `jurisdiction` `legal_entity` `business_unit` `authority_level` | `validity_status` `current_or_superseded` `access_classification` |

Two fields are hybrid:

- **`tax_year_or_effective_period`** — regex looks for "tax year 2024" / "fiscal
  year 2023" first. The LLM is only consulted if nothing is found.
- **`scenario_id`** — a corpus-assigned ID from the manifest always wins. The LLM
  generates a fallback slug only when none was supplied.

---

## Scenario manifest

Corpus-assigned scenario IDs come from a JSON file at `SCENARIO_MANIFEST_PATH`
(default `scenarios.json`). Keys may be full blob paths or bare file names:

```json
{
  "memos/nexus_il.pdf": ["SCN-014", "SCN-022"],
  "threads/apportionment.msg": "SCN-031",
  "transfer_pricing_memo.pdf": ["SCN-007"]
}
```

A string or a list is accepted. `scenario_id` holds the first value for simple
filtering; `scenario_ids` holds all of them. If the file is absent, both stay
`n/a` and the pipeline continues normally.

Test locally without the manifest:

```bash
python scripts/test_normalization.py memo.pdf --scenario SCN-014 --scenario SCN-022
```

---

## Email thread chunking

Threads produce **one chunk per message**, not token windows. Each chunk carries
everything the action plan requires:

| Requirement | Field |
|---|---|
| Source file name | `source_file` |
| Thread subject | `thread_subject` |
| Individual message sender | `author` |
| Recipients | rendered into chunk content |
| Message date/time | `message_date` |
| Message body | `content` |
| Order within the thread | `message_index` / `message_total` |
| PDF page number | `page_number` |
| Scenario ID(s) | `scenario_id` / `scenario_ids` |
| Source type | `source_type` = `Email Message` |

Headers are prepended into chunk content so context survives retrieval:

```text
Subject: Illinois nexus - remote employees
From: Tristan Lopez <tristan.lopez@amsted.com>
To: Payal Moorti <payal.moorti@protiviti.com>
Date: 2026-08-18T14:14:00

Does the payroll factor change for 2024?
```

Citation format:

```text
thread_export.pdf#message=2&page=3&part=1
```

`&part=` appears only when a single message exceeded the token budget and was
sub-split. The message keeps its `message_index` across parts.

Ordering is normalized to **chronological** — Outlook threads are top-posted, so
messages are reversed such that `message_index=1` is the earliest.

---

## Example — normalized document

```json
{
  "document_id": "doc_d48271bd346d18e6",
  "source_file_name": "thread_export.pdf",
  "source_path": "raw/memos/thread_export.pdf",
  "document_type": "pdf",
  "extractor": "docling",
  "content_hash": "b0eff93a1b49d5ecf5baa2e2...",
  "char_count": 743,
  "page_count": 2,
  "is_email_thread": true,
  "metadata": {
    "scenario_id": "SCN-014",
    "scenario_ids": ["SCN-014", "SCN-022"],
    "source_id": "src_ae5651eaf325c27e",
    "source_file": "thread_export.pdf",
    "source_type": "Email Thread",
    "source_date": "2026-08-17T09:02:00",
    "author": "Payal Moorti <payal.moorti@protiviti.com>",
    "page_number": 1,

    "tax_topic": "Nexus",
    "jurisdiction": "Illinois",
    "tax_year_or_effective_period": "2024",
    "authority_level": "Internal Email",

    "legal_entity": "Amsted Industries Inc.",
    "business_unit": "Corporate Tax",

    "thread_subject": "Illinois nexus - remote employees",
    "message_date": "2026-08-18T14:14:00",

    "validity_status": "n/a",
    "current_or_superseded": "n/a",
    "access_classification": "n/a",

    "enrichment_status": "ok",
    "enrichment_confidence": 0.91,
    "enrichment_rationale": "Thread analyzes PL 86-272 for Illinois remote employees.",
    "enrichment_model": "gpt-4o-mini",
    "enrichment_timestamp": "2026-08-28T19:48:50Z"
  }
}
```

## Example — search record

`Chunk.to_search_record()` output. Note it is **flat** — no nested `metadata`
object, because the index declares top-level fields.

```json
{
  "chunk_id": "chk_377b660aafc268eb",
  "document_id": "doc_d48271bd346d18e6",
  "chunk_number": 1,
  "total_chunks": 3,
  "content": "Subject: Illinois nexus - remote employees\nFrom: Payal Moorti...",
  "content_vector": [0.0123, -0.0456, "... 3070 more"],

  "scenario_id": "SCN-014",
  "scenario_ids": ["SCN-014", "SCN-022"],
  "source_id": "src_ae5651eaf325c27e",
  "source_file": "thread_export.pdf",
  "source_type": "Email Message",
  "source_date": "2026-08-17T09:02:00",
  "author": "Payal Moorti <payal.moorti@protiviti.com>",
  "page_number": 1,

  "tax_topic": "Nexus",
  "jurisdiction": "Illinois",
  "tax_year_or_effective_period": "2024",
  "authority_level": "Internal Email",

  "legal_entity": "Amsted Industries Inc.",
  "business_unit": "Corporate Tax",

  "thread_subject": "Illinois nexus - remote employees",
  "message_date": "2026-08-17T09:02:00",
  "message_index": 1,

  "validity_status": "n/a",
  "current_or_superseded": "n/a",
  "access_classification": "n/a",

  "citation": "thread_export.pdf#message=1",
  "token_count": 84,
  "chunk_strategy": "email_message",
  "chunk_timestamp": "2026-08-28T19:48:50Z"
}
```

> **Do not send `Chunk.model_dump()` to the index.** It emits a nested `metadata`
> object that the flat index does not declare, so every metadata field lands
> empty. `indexing.upload()` must call `to_search_record()`.

---

## Search index mapping

| Field | Type | Key | Searchable | Filterable | Facetable | Sortable |
|---|---|:--:|:--:|:--:|:--:|:--:|
| `chunk_id` | String | ✓ | | ✓ | | |
| `document_id` | String | | | ✓ | ✓ | |
| `chunk_number` | Int32 | | | ✓ | | ✓ |
| `total_chunks` | Int32 | | | ✓ | | |
| `content` | String | | ✓ | | | |
| `content_vector` | Collection(Single) | | ✓ vector | | | |
| `scenario_id` | String | | ✓ | ✓ | ✓ | |
| `scenario_ids` | Collection(String) | | ✓ | ✓ | ✓ | |
| `source_id` | String | | | ✓ | | |
| `source_file` | String | | ✓ | ✓ | ✓ | |
| `source_type` | String | | ✓ | ✓ | ✓ | |
| `source_date` | String | | | ✓ | | ✓ |
| `author` | String | | ✓ | ✓ | ✓ | |
| `page_number` | Int32 | | | ✓ | | ✓ |
| `tax_topic` | String | | ✓ | ✓ | ✓ | |
| `jurisdiction` | String | | ✓ | ✓ | ✓ | |
| `tax_year_or_effective_period` | String | | ✓ | ✓ | ✓ | |
| `authority_level` | String | | ✓ | ✓ | ✓ | |
| `legal_entity` | String | | ✓ | ✓ | ✓ | |
| `business_unit` | String | | ✓ | ✓ | ✓ | |
| `thread_subject` | String | | ✓ | ✓ | ✓ | |
| `message_date` | String | | | ✓ | | ✓ |
| `message_index` | Int32 | | | ✓ | | ✓ |
| `validity_status` | String | | | ✓ | ✓ | |
| `current_or_superseded` | String | | | ✓ | ✓ | |
| `access_classification` | String | | | ✓ | ✓ | |
| `citation` | String | | | ✓ | | |
| `token_count` | Int32 | | | ✓ | | ✓ |
| `chunk_strategy` | String | | | ✓ | ✓ | |
| `chunk_timestamp` | String | | | ✓ | | ✓ |

Vector profile `amsted-vector-profile` (HNSW). Semantic configuration
`amsted-semantic`: title `thread_subject`, content `content`, keywords
`tax_topic` / `jurisdiction` / `authority_level` / `legal_entity`.

`chunk_id` is hashed rather than raw text because Azure AI Search keys allow only
letters, digits, `_`, `-`, and `=`.

Example filters the agent can use:

```text
jurisdiction eq 'Illinois'
authority_level eq 'Statute' or authority_level eq 'Regulation'
scenario_ids/any(s: s eq 'SCN-014')
source_type eq 'Email Message' and tax_topic eq 'Nexus'
```

---

## Determinism

| Value | Derivation | Guarantee |
|---|---|---|
| `document_id` | `doc_` + sha256(`source_path`)[:16] | Same blob → same ID, independent of file size or re-download |
| `source_id` | `src_` + sha256(`document_id`)[:16] | Stable |
| `content_hash` | sha256(normalized content) | Detects genuine content change |
| `chunk_id` (token) | `chk_` + sha256(`document_id\|chunk_number`)[:16] | Stable across re-runs |
| `chunk_id` (message) | `chk_` + sha256(`document_id\|msg\|message_index\|part`)[:16] | Stable; independent of chunk text |

Because IDs never depend on content, re-running the pipeline **upserts** rather
than duplicating. Changing `CHUNK_SIZE_TOKENS` or `CHUNK_OVERLAP_TOKENS` changes
chunk boundaries for token-strategy documents, so recreate the index after
adjusting them.

---

## Adding a metadata field

1. Add it to `DocumentMetadata` in `models.py` with a default of `NA`.
2. Add it to `Chunk.to_search_record()`.
3. Add the field to `search_schema.build_index()`.
4. Populate it — in `normalization.normalize()` if deterministic, or in the
   enrichment prompt and `enrich()` if semantic.
5. Add it to `_cli.metadata_table()` so the test scripts display it.
6. Run `python scripts/recreate_search_index.py --yes`, then re-ingest.

Adding a controlled vocabulary value only requires step 1 plus a prompt update —
no index rebuild, since the field already exists.
