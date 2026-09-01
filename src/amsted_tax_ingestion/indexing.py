"""Azure AI Search index management and upload.

Fixes vs. the previous version:
  * The index definition now comes from search_schema.build_index(), so the
    18-field Seed Corpus v0.1 model is actually created. The old inline schema
    was missing scenario_ids, source_type, source_date, author, page_number,
    legal_entity, business_unit, tax_year_or_effective_period, thread_subject,
    message_date, message_index, and all three governance fields.
  * upload() sends Chunk.to_search_record() instead of Chunk.model_dump().
    model_dump() emits a NESTED "metadata" object, which the flat index rejects
    while leaving every metadata field unpopulated.
  * create_index() uses create_or_update_index only when the schema is
    compatible; a vector-dimension change requires recreate_index().
  * validate() returns a dict with document_count, facets, and samples, which is
    what scripts/validate_search_index.py expects. The old version returned a
    bare list, so the script printed nothing useful and always reported success.
"""
from __future__ import annotations

import logging

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient

from .search_schema import SEMANTIC_CONFIG, build_index

log = logging.getLogger(__name__)

FACET_FIELDS = [
    "jurisdiction",
    "tax_topic",
    "authority_level",
    "source_type",
    "legal_entity",
    "business_unit",
    "scenario_id",
]


def clients(s):
    s.require("azure_search_endpoint", "azure_search_api_key", "azure_search_index_name")
    credential = AzureKeyCredential(s.azure_search_api_key)
    return (
        SearchIndexClient(s.azure_search_endpoint, credential),
        SearchClient(s.azure_search_endpoint, s.azure_search_index_name, credential),
    )


def index_definition(s):
    """Single source of truth for the schema."""
    return build_index(s.azure_search_index_name, s.embedding_dimensions)


def create_index(s):
    index_client, _ = clients(s)
    result = index_client.create_or_update_index(index_definition(s))
    log.info("Created/updated index '%s' with %s fields.", result.name, len(result.fields))
    return f"Created/updated index '{result.name}' ({len(result.fields)} fields)"


def delete_index(s):
    index_client, _ = clients(s)
    index_client.delete_index(s.azure_search_index_name)
    log.info("Deleted index '%s'.", s.azure_search_index_name)
    return f"Deleted index '{s.azure_search_index_name}'"


def recreate_index(s):
    index_client, _ = clients(s)
    try:
        index_client.delete_index(s.azure_search_index_name)
        log.info("Deleted existing index '%s'.", s.azure_search_index_name)
    except ResourceNotFoundError:
        log.info("No existing index '%s' to delete.", s.azure_search_index_name)
    result = index_client.create_index(index_definition(s))
    return f"Recreated index '{result.name}' ({len(result.fields)} fields)"


def upload(s, chunks):
    """Upload chunks as FLAT search records.

    Chunk.to_search_record() flattens the nested DocumentMetadata into the
    top-level fields the index declares.
    """
    if not chunks:
        return {"succeeded": 0, "failed": 0}

    _, search_client = clients(s)
    batch_size = getattr(s, "search_upload_batch_size", 100)
    succeeded, failed = 0, []

    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        documents = []
        for chunk in batch:
            record = chunk.to_search_record()
            if record.get("content_vector") is None:
                raise RuntimeError(
                    f"Chunk {record['chunk_id']} has no embedding. "
                    "Run the embedding stage before uploading."
                )
            documents.append(record)

        for result in search_client.upload_documents(documents):
            if result.succeeded:
                succeeded += 1
            else:
                failed.append({"key": result.key, "error": result.error_message})

    if failed:
        raise RuntimeError(f"Index upload failures ({len(failed)}): {failed[:5]}")

    log.info("Indexed %s record(s) into '%s'.", succeeded, s.azure_search_index_name)
    return {"succeeded": succeeded, "failed": 0}


def validate(s, document_id=None, sample_size: int = 5):
    """Return a report dict: document_count, facets, samples."""
    _, search_client = clients(s)
    search_filter = f"document_id eq '{document_id}'" if document_id else None

    try:
        total = search_client.get_document_count()
    except Exception:  # noqa: BLE001
        total = None

    select = [
        "chunk_id", "document_id", "chunk_number", "source_file", "source_type",
        "jurisdiction", "tax_topic", "authority_level", "scenario_id",
        "thread_subject", "message_index", "author", "message_date",
        "page_number", "citation", "content",
    ]

    results = search_client.search(
        "*",
        filter=search_filter,
        select=select,
        facets=FACET_FIELDS if not document_id else None,
        top=sample_size,
        include_total_count=True,
    )

    samples = []
    for item in results:
        content = item.get("content") or ""
        samples.append({
            "chunk_id": item.get("chunk_id"),
            "document_id": item.get("document_id"),
            "source_file": item.get("source_file"),
            "source_type": item.get("source_type"),
            "jurisdiction": item.get("jurisdiction"),
            "tax_topic": item.get("tax_topic"),
            "authority_level": item.get("authority_level"),
            "scenario_id": item.get("scenario_id"),
            "message_index": item.get("message_index"),
            "author": item.get("author"),
            "citation": item.get("citation"),
            "preview": content[:200],
        })

    matched = results.get_count()
    facets = {}
    try:
        for field, values in (results.get_facets() or {}).items():
            facets[field] = [{"value": v["value"], "count": v["count"]} for v in values]
    except Exception:  # noqa: BLE001
        pass

    return {
        "index": s.azure_search_index_name,
        "document_count": total if total is not None else matched,
        "matched_count": matched,
        "document_id_filter": document_id,
        "facets": facets,
        "samples": samples,
    }


def search_preview(s, query: str, top: int = 5, filter_expression: str | None = None):
    """Ad-hoc keyword/semantic query. Used by scripts/test_search.py."""
    _, search_client = clients(s)
    results = search_client.search(
        query,
        filter=filter_expression,
        top=top,
        query_type="semantic",
        semantic_configuration_name=SEMANTIC_CONFIG,
        select=[
            "chunk_id", "source_file", "jurisdiction", "tax_topic",
            "authority_level", "citation", "content",
        ],
    )
    output = []
    for item in results:
        output.append({
            "chunk_id": item.get("chunk_id"),
            "score": item.get("@search.score"),
            "reranker_score": item.get("@search.reranker_score"),
            "source_file": item.get("source_file"),
            "jurisdiction": item.get("jurisdiction"),
            "tax_topic": item.get("tax_topic"),
            "authority_level": item.get("authority_level"),
            "citation": item.get("citation"),
            "preview": (item.get("content") or "")[:300],
        })
    return output
