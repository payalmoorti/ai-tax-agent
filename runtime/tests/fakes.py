"""Test doubles for retrieval and query understanding."""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel
from runtime.models.query import SearchFilters
from runtime.providers.odata import build_filter
from shared.contracts.index_schema import F, VECTOR_FIELD_DIMENSIONS


def make_search_doc(source_id: str = "SRC-0009", chunk_id: str | None = None,
                     source_file: str = "ADS_NYC_Tax_Analysis.pdf",
                     content: str = "Historical analysis of ADS service activity in New York City.",
                     jurisdictions: list[str] | None = None, entities: list[str] | None = None,
                     semantic_state: str = "SETTLED", page_number: int | None = 4,
                     score: float = 0.87, **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        F.SOURCE_ID: source_id,
        F.CHUNK_ID: chunk_id or f"{source_id}::c1",
        F.SOURCE_FILE: source_file,
        F.CONTENT: content,
        F.JURISDICTIONS: jurisdictions if jurisdictions is not None else ["New York City"],
        F.ENTITIES: entities if entities is not None else ["ADS"],
        F.TAX_CONCEPTS: None,
        F.PAGE_NUMBER: page_number,
        F.SEMANTIC_STATE: semantic_state,
        "@search.score": score,
    }
    doc.update(overrides)
    return doc


class FakeEmbeddingProvider:
    def __init__(self, dimensions: int = VECTOR_FIELD_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.calls: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.01] * self.dimensions


class WrongDimensionEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(dimensions=1536)


class FakeSearchBackend:
    def __init__(self, documents: list[dict[str, Any]] | None = None) -> None:
        self.documents = documents if documents is not None else [make_search_doc()]
        self.last_query_text: str | None = None
        self.last_query_vector: list[float] | None = None
        self.last_filters: SearchFilters | None = None
        self.last_odata_filter: str | None = None
        self.last_top_k: int | None = None
        self.call_count = 0

    async def hybrid_search(self, query_text: str, query_vector: list[float] | None,
                             filters: SearchFilters, top_k: int):
        self.call_count += 1
        self.last_query_text = query_text
        self.last_query_vector = query_vector
        self.last_filters = filters
        self.last_top_k = top_k
        self.last_odata_filter = build_filter(filters)
        matched = [d for d in self.documents if self._matches(d, filters)]
        return matched[:top_k], len(matched), True

    @staticmethod
    def _matches(doc: dict[str, Any], filters: SearchFilters) -> bool:
        if filters.entities:
            doc_entities = doc.get(F.ENTITIES) or []
            if not set(filters.entities) & set(doc_entities):
                return False
        if filters.tax_topic and doc.get(F.TAX_TOPIC) != filters.tax_topic:
            return False
        if filters.evidence_types:
            if doc.get(F.EVIDENCE_TYPE) not in filters.evidence_types:
                return False
        return True


class EmptySearchBackend(FakeSearchBackend):
    def __init__(self) -> None:
        super().__init__(documents=[])


# ===========================================================================
# NEW FOR STEP 4 -- Query Understanding
# ===========================================================================

class ScriptedChatModel:
    """Returns a pre-scripted structured response regardless of the model.

    Used to unit-test QueryUnderstandingAgent's plumbing (request_id
    injection, prompt loading, error propagation) without a live model. For
    testing the agent's actual reasoning quality against real questions, use
    scripts/smoke_query_understanding.py against a live deployment.

    Pass an Exception instance instead of a BaseModel to simulate a model
    failure or refusal.
    """

    def __init__(self, response: BaseModel | Exception) -> None:
        self._response = response
        self.calls: list[tuple[str, str]] = []

    async def complete_structured(self, system: str, user: str, response_model: type[Any]) -> Any:
        self.calls.append((system, user))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class SequencedChatModel:
    """Returns successive scripted responses on successive calls.

    Useful for testing a caller that invokes the same agent for a sequence of
    different questions (e.g. an eval runner) without needing one fake per
    question.
    """

    def __init__(self, responses: list[BaseModel]) -> None:
        self._responses = list(responses)
        self._index = 0
        self.calls: list[tuple[str, str]] = []

    async def complete_structured(self, system: str, user: str, response_model: type[Any]) -> Any:
        self.calls.append((system, user))
        if self._index >= len(self._responses):
            raise IndexError("SequencedChatModel exhausted its scripted responses")
        response = self._responses[self._index]
        self._index += 1
        return response
