"""Unit tests for the Step 3 retrieval slice.

No Azure account required -- ``InternalSearchTool`` depends on Protocols.

The tests that matter most are in the jurisdiction section. They prove a
negative: that no code path can hard-filter jurisdiction and thereby make
PARTIAL_MATCH unreachable.

Run:  pytest runtime/tests/unit/test_internal_search.py -v
"""

from __future__ import annotations

import asyncio

import pytest

from runtime.models.enums import QueryIntent
from runtime.models.query import QueryUnderstandingResult, SearchFilters
from runtime.providers.embeddings import AzureOpenAIEmbeddingProvider
from runtime.providers.odata import build_filter, build_scoring_parameters, escape_odata_string
from runtime.tests.fakes import (
    EmptySearchBackend,
    FakeEmbeddingProvider,
    FakeSearchBackend,
    make_search_doc,
)
from runtime.tools.internal_search import InternalSearchTool
from shared.contracts.index_schema import F, VECTOR_FIELD_DIMENSIONS


def run(coro):
    """Minimal async runner so these tests do not require pytest-asyncio."""
    return asyncio.run(coro)


# ===========================================================================
# JURISDICTION -- the PARTIAL_MATCH guard
# ===========================================================================

def test_jurisdiction_never_appears_in_odata_filter():
    """The core regression guard, at the filter-builder level."""
    filters = SearchFilters(
        entities=["ADS"],
        boost_jurisdictions=["Philadelphia", "Pennsylvania"],
    )
    odata = build_filter(filters)
    assert odata is not None
    assert F.JURISDICTIONS not in odata
    assert "Philadelphia" not in odata
    assert "entities/any" in odata


def test_philadelphia_question_retrieves_nyc_evidence():
    """GS-002. If this fails, PARTIAL_MATCH is unreachable and the system
    silently collapses to NO_PRECEDENT for every cross-jurisdiction question."""
    nyc_doc = make_search_doc(
        source_id="SRC-0009",
        jurisdictions=["New York City"],
        entities=["ADS"],
    )
    backend = FakeSearchBackend(documents=[nyc_doc])
    tool = InternalSearchTool(backend=backend, embeddings=FakeEmbeddingProvider())

    understanding = QueryUnderstandingResult(
        request_id="REQ-1",
        intent=QueryIntent.CURRENT_TAX_QUESTION,
        entities=["ADS"],
        jurisdictions=["Philadelphia"],
        internal_search_query="ADS service activity nexus broader tax exposure",
    )

    pkg = run(tool.search_for_understanding(understanding))

    assert "SRC-0009" in pkg.source_ids, (
        "Philadelphia question failed to retrieve NYC evidence -- "
        "jurisdiction is being hard-filtered somewhere."
    )
    assert backend.last_odata_filter is not None
    assert "Philadelphia" not in backend.last_odata_filter


def test_jurisdiction_routed_to_scoring_parameters():
    """Jurisdiction influences ranking, never membership."""
    filters = SearchFilters(boost_jurisdictions=["Philadelphia"])
    assert build_filter(filters) is None
    params = build_scoring_parameters(filters)
    assert params == ["jurisdictionTag-Philadelphia"]


def test_search_filters_has_no_jurisdiction_field():
    """Structural guard -- the wrong thing is not expressible."""
    assert "jurisdictions" not in SearchFilters.model_fields
    assert "boost_jurisdictions" in SearchFilters.model_fields


def test_understanding_routes_jurisdiction_to_boost():
    understanding = QueryUnderstandingResult(
        request_id="REQ-1",
        intent=QueryIntent.CURRENT_TAX_QUESTION,
        jurisdictions=["Philadelphia"],
        internal_search_query="q",
    )
    filters = understanding.to_filters()
    assert filters.boost_jurisdictions == ["Philadelphia"]
    assert build_filter(filters) is None


# ===========================================================================
# OData construction
# ===========================================================================

def test_empty_filters_produce_no_filter():
    assert build_filter(SearchFilters()) is None


def test_collection_field_uses_any_syntax():
    odata = build_filter(SearchFilters(entities=["ADS", "Burgess-Norton"]))
    assert odata == "entities/any(v: v eq 'ADS' or v eq 'Burgess-Norton')"


def test_scalar_field_uses_eq():
    odata = build_filter(SearchFilters(tax_topic="State and Local Tax"))
    assert odata == "tax_topic eq 'State and Local Tax'"


def test_multiple_clauses_joined_with_and():
    odata = build_filter(SearchFilters(entities=["ADS"], tax_topic="SALT"))
    assert " and " in odata
    assert "entities/any" in odata
    assert "tax_topic eq 'SALT'" in odata


def test_single_quotes_escaped():
    assert escape_odata_string("O'Brien") == "O''Brien"
    odata = build_filter(SearchFilters(tax_topic="O'Brien"))
    assert odata == "tax_topic eq 'O''Brien'"


def test_date_range_clauses():
    odata = build_filter(
        SearchFilters(sent_after="2020-01-01T00:00:00Z", sent_before="2024-12-31T00:00:00Z")
    )
    assert "sent_date ge 2020-01-01T00:00:00Z" in odata
    assert "sent_date le 2024-12-31T00:00:00Z" in odata


# ===========================================================================
# Retrieval behaviour
# ===========================================================================

def test_search_returns_typed_evidence_with_provenance():
    tool = InternalSearchTool(FakeSearchBackend(), FakeEmbeddingProvider())
    pkg = run(tool.search("ADS NYC service activity"))

    assert len(pkg.evidence) == 1
    ev = pkg.evidence[0]
    assert ev.source_id == "SRC-0009"
    assert ev.chunk_id == "SRC-0009::c1"
    assert ev.page_number == 4
    assert ev.tax_concepts == []          # null coerced, not crashed
    assert ev.search_score == 0.87
    assert "p. 4" in ev.citation_label()


def test_empty_results_are_not_an_error():
    """An empty package is a legitimate NO_PRECEDENT signal."""
    tool = InternalSearchTool(EmptySearchBackend(), FakeEmbeddingProvider())
    pkg = run(tool.search("transfer pricing Malaysia"))

    assert pkg.is_empty
    assert pkg.source_ids == set()
    assert pkg.evidence == []


def test_query_is_embedded_before_search():
    embeddings = FakeEmbeddingProvider()
    backend = FakeSearchBackend()
    tool = InternalSearchTool(backend, embeddings)

    run(tool.search("ADS service activity"))

    assert embeddings.calls == ["ADS service activity"]
    assert backend.last_query_vector is not None
    assert len(backend.last_query_vector) == VECTOR_FIELD_DIMENSIONS


def test_keyword_only_when_no_embedding_provider():
    """Useful for isolating whether a retrieval failure is vector or text."""
    backend = FakeSearchBackend()
    tool = InternalSearchTool(backend, embeddings=None)

    pkg = run(tool.search("ADS"))

    assert backend.last_query_vector is None
    assert not pkg.is_empty


def test_default_top_k_is_ten():
    """Precedent analysis needs enough material to detect differences.
    Trimming this to 3 is a common and damaging over-optimisation."""
    backend = FakeSearchBackend()
    tool = InternalSearchTool(backend, FakeEmbeddingProvider())

    run(tool.search("q"))
    assert backend.last_top_k == 10


def test_top_k_override_respected():
    backend = FakeSearchBackend()
    tool = InternalSearchTool(backend, FakeEmbeddingProvider())
    run(tool.search("q", top_k=25))
    assert backend.last_top_k == 25


def test_uses_rewritten_query_from_understanding():
    backend = FakeSearchBackend()
    tool = InternalSearchTool(backend, FakeEmbeddingProvider())

    understanding = QueryUnderstandingResult(
        request_id="REQ-1",
        intent=QueryIntent.HISTORICAL_PRECEDENT,
        internal_search_query="ADS service activity nexus rewritten",
        entities=["ADS"],
    )
    run(tool.search_for_understanding(understanding))

    assert backend.last_query_text == "ADS service activity nexus rewritten"


# ===========================================================================
# Ranking / metrics helpers
# ===========================================================================

def test_ranked_source_ids_deduplicates_preserving_order():
    docs = [
        make_search_doc(source_id="SRC-0009", chunk_id="SRC-0009::c1", score=0.9),
        make_search_doc(source_id="SRC-0009", chunk_id="SRC-0009::c2", score=0.8),
        make_search_doc(source_id="SRC-0011", chunk_id="SRC-0011::c1", score=0.7),
    ]
    tool = InternalSearchTool(FakeSearchBackend(docs), FakeEmbeddingProvider())
    pkg = run(tool.search("q"))

    assert pkg.ranked_source_ids() == ["SRC-0009", "SRC-0011"]


def test_superseded_sources_surfaced():
    docs = [
        make_search_doc(source_id="SRC-0009", semantic_state="SETTLED"),
        make_search_doc(source_id="SRC-0010", chunk_id="SRC-0010::c1",
                        semantic_state="SUPERSEDED"),
    ]
    tool = InternalSearchTool(FakeSearchBackend(docs), FakeEmbeddingProvider())
    pkg = run(tool.search("q"))

    assert pkg.superseded_source_ids() == {"SRC-0010"}


def test_distinct_jurisdictions_available_for_difference_detection():
    docs = [
        make_search_doc(source_id="SRC-0009", jurisdictions=["New York City"]),
        make_search_doc(source_id="SRC-0011", chunk_id="SRC-0011::c1",
                        jurisdictions=["Illinois"]),
    ]
    tool = InternalSearchTool(FakeSearchBackend(docs), FakeEmbeddingProvider())
    pkg = run(tool.search("q"))

    assert pkg.distinct_jurisdictions() == {"New York City", "Illinois"}


# ===========================================================================
# Embedding dimension guard
# ===========================================================================

def test_dimension_mismatch_raises_with_actionable_message():
    """A model mismatch returns plausible garbage rather than an error, so it
    must be caught explicitly."""
    provider = AzureOpenAIEmbeddingProvider(
        endpoint="https://example.openai.azure.com/",
        deployment="text-embedding-3-large",
        expected_dimensions=3072,
    )
    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        provider.validate_dimensions([0.1] * 1536)


def test_correct_dimensions_pass():
    provider = AzureOpenAIEmbeddingProvider(
        endpoint="https://example.openai.azure.com/",
        deployment="text-embedding-3-large",
        expected_dimensions=3072,
    )
    provider.validate_dimensions([0.1] * 3072)


# ===========================================================================
# Provenance
# ===========================================================================

def test_email_provenance_prefers_message_locator():
    doc = make_search_doc(
        source_id="SRC-0020",
        source_file="tax_team_thread.pdf",
        page_number=None,
        **{
            F.MESSAGE_ID: "msg-3",
            F.SENDER: "j.smith@amsted.com",
            F.SENT_DATE: "2023-06-14T10:04:00Z",
        },
    )
    tool = InternalSearchTool(FakeSearchBackend([doc]), FakeEmbeddingProvider())
    pkg = run(tool.search("q"))

    label = pkg.evidence[0].citation_label()
    assert "j.smith@amsted.com" in label
    assert "2023-06-14" in label
