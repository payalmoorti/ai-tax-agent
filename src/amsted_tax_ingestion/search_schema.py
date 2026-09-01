"""Azure AI Search index definition for the Seed Corpus v0.1 metadata model.

Every business metadata field is filterable and facetable so retrieval can be
scoped by scenario, jurisdiction, entity, business unit, authority level, and
period — and so you can profile corpus coverage before the agent is built.
"""
from __future__ import annotations

from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

VECTOR_PROFILE = "amsted-vector-profile"
HNSW_CONFIG = "amsted-hnsw"
SEMANTIC_CONFIG = "amsted-semantic"

String = SearchFieldDataType.String
Int32 = SearchFieldDataType.Int32
Collection = SearchFieldDataType.Collection


def build_index(index_name: str, dimensions: int) -> SearchIndex:
    fields = [
        # --- Keys and chunk identity ---
        SimpleField(name="chunk_id", type=String, key=True, filterable=True),
        SimpleField(name="document_id", type=String, filterable=True, facetable=True),
        SimpleField(name="chunk_number", type=Int32, filterable=True, sortable=True),
        SimpleField(name="total_chunks", type=Int32, filterable=True),

        # --- Content ---
        SearchableField(name="content", type=String, analyzer_name="en.microsoft"),
        SearchField(
            name="content_vector",
            type=Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name=VECTOR_PROFILE,
        ),

        # --- Identity / provenance ---
        SearchableField(name="scenario_id", type=String, filterable=True, facetable=True),
        SearchField(name="scenario_ids", type=Collection(String),
                    searchable=True, filterable=True, facetable=True),
        SimpleField(name="source_id", type=String, filterable=True),
        SearchableField(name="source_file", type=String, filterable=True, facetable=True),
        SearchableField(name="source_type", type=String, filterable=True, facetable=True),
        SimpleField(name="source_date", type=String, filterable=True, sortable=True),
        SearchableField(name="author", type=String, filterable=True, facetable=True),
        SimpleField(name="page_number", type=Int32, filterable=True, sortable=True),

        # --- Tax classification ---
        SearchableField(name="tax_topic", type=String, filterable=True, facetable=True),
        SearchableField(name="jurisdiction", type=String, filterable=True, facetable=True),
        SearchableField(name="tax_year_or_effective_period", type=String,
                        filterable=True, facetable=True),
        SearchableField(name="authority_level", type=String, filterable=True, facetable=True),

        # --- Organizational ---
        SearchableField(name="legal_entity", type=String, filterable=True, facetable=True),
        SearchableField(name="business_unit", type=String, filterable=True, facetable=True),

        # --- Email thread ---
        SearchableField(name="thread_subject", type=String, filterable=True, facetable=True),
        SimpleField(name="message_date", type=String, filterable=True, sortable=True),
        SimpleField(name="message_index", type=Int32, filterable=True, sortable=True),

        # --- Governance (deferred; currently always "n/a") ---
        SimpleField(name="validity_status", type=String, filterable=True, facetable=True),
        SimpleField(name="current_or_superseded", type=String, filterable=True, facetable=True),
        SimpleField(name="access_classification", type=String, filterable=True, facetable=True),

        # --- Citation / operational ---
        SimpleField(name="citation", type=String, filterable=True),
        SimpleField(name="token_count", type=Int32, filterable=True, sortable=True),
        SimpleField(name="chunk_strategy", type=String, filterable=True, facetable=True),
        SimpleField(name="chunk_timestamp", type=String, filterable=True, sortable=True),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name=HNSW_CONFIG)],
        profiles=[VectorSearchProfile(
            name=VECTOR_PROFILE, algorithm_configuration_name=HNSW_CONFIG
        )],
    )

    semantic_search = SemanticSearch(configurations=[
        SemanticConfiguration(
            name=SEMANTIC_CONFIG,
            prioritized_fields=SemanticPrioritizedFields(
                title_field=SemanticField(field_name="thread_subject"),
                content_fields=[SemanticField(field_name="content")],
                keywords_fields=[
                    SemanticField(field_name="tax_topic"),
                    SemanticField(field_name="jurisdiction"),
                    SemanticField(field_name="authority_level"),
                    SemanticField(field_name="legal_entity"),
                ],
            ),
        )
    ])

    return SearchIndex(
        name=index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )
