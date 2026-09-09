# Test Runbook — Amsted AI Tax Agent Ingestion

All commands run from the **project root** (the folder containing `pyproject.toml`).
Every script exits `0` on success, `1` on failure.

---

## 0. One-time setup

```bash
# Activate the virtual environment
.venv\Scripts\activate                    # Windows PowerShell
# source .venv/bin/activate               # macOS / Linux

# Install the package in editable mode
python -m pip install --upgrade pip
pip install -e .

# Confirm the package imports
python -c "import amsted_tax_ingestion; print('package OK')"
```

Copy the fixed files into place before anything else:

```text
src/amsted_tax_ingestion/enrichment.py     <- replace (duplicate class removed)
src/amsted_tax_ingestion/indexing.py       <- replace (wired to search_schema)
src/amsted_tax_ingestion/config.py         <- replace (adds scenario_manifest_path)
scripts/apply_patches.py                   <- new
tests/test_markdown_headers.py             <- new
```

Then apply the Markdown header patch:

```bash
python scripts/apply_patches.py --check     # report what needs patching
python scripts/apply_patches.py             # apply (writes .bak backups)
```

---

## 1. Offline tests — no Azure, no cost

Run these first. If any fail, stop and fix before touching Azure.

```bash
# Markdown email-header recovery (34 assertions)
python tests/test_markdown_headers.py
python tests/test_markdown_headers.py --live      # verifies src is patched

# Module unit tests
python tests/test_all.py                          # 52 assertions
python tests/test_pipeline.py                     # 24 assertions
```

Expected: `34 passed`, `52 passed`, `24 passed`.

---

## 2. Verify Azure connectivity

```bash
python scripts/test_connection.py
python scripts/test_connection.py --verbose       # echo non-secret values
```

Checks Blob Storage, Table Storage, AI Search, the chat deployment, and the
embedding deployment — including that the returned vector width matches
`EMBEDDING_DIMENSIONS`.

Fix any `FAIL` line before continuing.

---

## 3. Create the search index

The schema changed, so an existing index must be rebuilt.

```bash
python scripts/create_search_index.py             # create or update
python scripts/recreate_search_index.py --yes     # drop and rebuild
```

Use `recreate` if the index already exists or if `EMBEDDING_DIMENSIONS` changed.

---

## 4. Stage-by-stage inspection

Work through one representative document of each type: an email thread, a PDF
export of a thread, and a plain tax memo.

### 4a. Extraction — no Azure calls

```bash
python scripts/test_extraction.py local_test_files\dense_tax_scenario.pdf
python scripts/test_extraction.py local_test_files\Netherlands_SARS.pdf


python scripts/test_extraction.py samples\nexus_memo.pdf --preview 3000
python scripts/test_extraction.py samples\thread.msg --messages    # full message bodies
python scripts/test_extraction.py samples\nexus_memo.pdf --full    # entire text
```

Confirm: `Email thread: True` for threads, and a message count matching the source.

### 4b. Normalization — no Azure calls

```bash
python scripts/test_normalization.py local_test_files\Netherlands_SARS.pdf
python scripts/test_normalization.py local_test_files\dense_tax_scenario.pdf
python scripts/test_normalization.py samples\thread.msg --diff
python scripts/test_normalization.py samples\nexus_memo.pdf --diff --diff-limit 60
python scripts/test_normalization.py samples\memo.pdf --scenario SCN-014 --scenario SCN-022
python scripts/test_normalization.py samples\thread.msg --messages --full
```

`--diff` is the important one: it lists exactly which lines the cleaner deleted.
Watch for over-deletion of substantive tax text.

The script prints a `document_id` — use it for the next two stages.

### 4c. Metadata enrichment — requires the chat deployment

```bash
python scripts/test_metadata_enrichment.py doc_a1b2c3d4
python scripts/test_metadata_enrichment.py local_test_files\Netherlands_SARS.pdf
python scripts/test_metadata_enrichment.py samples\nexus_memo.pdf
python scripts/test_metadata_enrichment.py doc_a1b2c3d4 --repeat 3    # stability check
```

Confirm the three governance fields stay `n/a`:

```text
OK - all three correctly left as n/a.
```

### 4d. Chunking — no Azure calls with `--skip-enrichment-check`

```bash
python scripts/test_chunking.py doc_a1b2c3d4
python scripts/test_chunking.py samples\thread.msg --skip-enrichment-check
python scripts/test_chunking.py doc_a1b2c3d4 --size 400 --overlap 60
python scripts/test_chunking.py doc_a1b2c3d4 --show 5
python scripts/test_chunking.py doc_a1b2c3d4 --all --verbose
```

For a thread, confirm `strategy=email_message` and one chunk per message.

---

## 5. Pipeline dry runs

```bash
# Single document, nothing indexed
python scripts/run_pipeline.py --dry-run --limit 1

# Small batch
python scripts/run_pipeline.py --dry-run --limit 5

# Restrict to a folder in the container
python scripts/run_pipeline.py --dry-run --prefix memos/

# No LLM call at all — structure only
python scripts/run_pipeline.py --dry-run --skip-enrichment --limit 3
```

`--dry-run` stops before embeddings and indexing, so it costs nothing beyond the
enrichment calls.

---

## 6. Full run

```bash
python scripts/run_pipeline.py --limit 5          # start small
python scripts/run_pipeline.py                    # full corpus

# Block documents whose enrichment failed instead of indexing them with n/a
python scripts/run_pipeline.py --on-enrichment-failure fail

# Reprocess everything, ignoring content_hash
python scripts/run_pipeline.py --force
```

---

## 7. Validate the index

```bash
python scripts/validate_search_index.py
python scripts/validate_search_index.py --samples 10
python scripts/validate_search_index.py --document-id doc_a1b2c3d4
python scripts/validate_search_index.py --json
```

Read the facet histogram carefully. Large `n/a` counts on `jurisdiction`,
`tax_topic`, or `authority_level` mean enrichment is underperforming.

---

## 8. Retrieval sanity check

```bash
python scripts/test_search.py "Illinois nexus remote employees"
python scripts/test_search.py "payroll factor apportionment" --top 10
python scripts/test_search.py "nexus" --filter "jurisdiction eq 'Illinois'"
python scripts/test_search.py "transfer pricing" --filter "authority_level eq 'Internal Memo'"
python scripts/test_search.py "PL 86-272" --filter "source_type eq 'Email Message'"
```

Confirm results are relevant and that `citation` points back to the right file
and message.

---

## Full sequence, first time through

```bash
pip install -e .
python scripts/apply_patches.py
python tests/test_markdown_headers.py --live
python tests/test_all.py
python tests/test_pipeline.py
python scripts/test_connection.py
python scripts/recreate_search_index.py --yes
python scripts/test_extraction.py samples\thread.msg
python scripts/test_normalization.py samples\thread.msg --diff
python scripts/test_metadata_enrichment.py <document_id>
python scripts/test_chunking.py <document_id>
python scripts/run_pipeline.py --dry-run --limit 3
python scripts/run_pipeline.py
python scripts/validate_search_index.py
python scripts/test_search.py "Illinois nexus remote employees"
```

---

## Cleanup and recovery

```bash
# Undo the source patch
python scripts/apply_patches.py --revert

# Delete the index
python scripts/delete_search_index.py --yes

# Clear local artifacts
rmdir /s /q sample_outputs                  # Windows
# rm -rf sample_outputs                     # macOS / Linux
```

---

## Where output lands

`output_dir` from `.env` (default `sample_outputs/`):

```text
sample_outputs/
├── raw/              downloaded blobs
├── extracted/        raw extractor output
├── normalized/       canonical NormalizedDocument JSON
├── enriched/         normalized + LLM metadata
├── chunked/          chunk records
├── embeddings/       vector dimensions + preview
└── indexing_logs/    per-document results, _run_summary.json, _validation.json
```

---

## Quick reference

| Command | Azure? | Purpose |
|---|:--:|---|
| `tests/test_markdown_headers.py` | no | Header regex, 34 assertions |
| `tests/test_all.py` | no | Module units, 52 assertions |
| `tests/test_pipeline.py` | no | Failure paths, 24 assertions |
| `scripts/apply_patches.py` | no | Apply the Markdown patch |
| `scripts/test_extraction.py` | no | Extractor output |
| `scripts/test_normalization.py` | no | Cleaning + metadata |
| `scripts/test_chunking.py` | no* | Chunk boundaries |
| `scripts/test_connection.py` | yes | Connectivity |
| `scripts/test_metadata_enrichment.py` | yes | LLM classification |
| `scripts/create_search_index.py` | yes | Create index |
| `scripts/recreate_search_index.py` | yes | Rebuild index |
| `scripts/delete_search_index.py` | yes | Delete index |
| `scripts/validate_search_index.py` | yes | Coverage + samples |
| `scripts/test_search.py` | yes | Retrieval check |
| `scripts/run_pipeline.py` | yes | Full ingestion |

\* no Azure with `--skip-enrichment-check`








# Setup
pip install -e .
python scripts/apply_patches.py

# Offline — no Azure, no cost. Fix failures here before going further.
python tests/test_markdown_headers.py --live
python tests/test_all.py
python tests/test_pipeline.py

# Azure connectivity
python scripts/test_connection.py

# Index (schema changed, so rebuild)
python scripts/recreate_search_index.py --yes

# Stage by stage — each prints the document_id for the next
python scripts/test_extraction.py samples\thread.msg
python scripts/test_normalization.py samples\thread.msg --diff
python scripts/test_metadata_enrichment.py <document_id>
python scripts/test_chunking.py <document_id>

# Pipeline
python scripts/run_pipeline.py --dry-run --limit 3
python scripts/run_pipeline.py

# Verify
python scripts/validate_search_index.py
python scripts/test_search.py "Illinois nexus remote employees"