"""Internal retrieval tool.

The workflow's single entry point to Amsted historical evidence. Composes an
embedding provider and a search backend, and maps raw documents onto typed
``InternalEvidence`` -- after which no downstream code touches an index field
name.

Both dependencies are Protocols, so the tool is fully testable against fixtures
without an Azure account.
"""

from __future__ import annotations

import logging

from runtime.models.evidence import InternalEvidence, InternalEvidencePackage
from runtime.models.query import QueryUnderstandingResult, SearchFilters
from runtime.providers.azure_search import SearchBackend
from runtime.providers.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 10


class InternalSearchTool:
    """Retrieve Amsted historical evidence for a query.

    Parameters
    ----------
    backend:
        Anything satisfying ``SearchBackend`` -- normally ``AzureSearchProvider``.
    embeddings:
        Anything satisfying ``EmbeddingProvider``. When ``None``, retrieval
        degrades to keyword-only, which is useful for isolating whether a
        retrieval failure is a vector problem or a text problem.
    """

    def __init__(
        self,
        backend: SearchBackend,
        embeddings: EmbeddingProvider | None = None,
        default_top_k: int = DEFAULT_TOP_K,
    ) -> None:
        self._backend = backend
        self._embeddings = embeddings
        self._default_top_k = default_top_k

    async def search(
        self,
        query: str,
        filters: SearchFilters | None = None,
        top_k: int | None = None,
    ) -> InternalEvidencePackage:
        """Run hybrid retrieval and return typed evidence.

        Never raises on empty results -- an empty package is a legitimate
        outcome that downstream precedent analysis reads as NO_PRECEDENT.
        """
        filters = filters or SearchFilters()
        k = top_k or self._default_top_k

        query_vector: list[float] | None = None
        if self._embeddings is not None:
            query_vector = await self._embeddings.embed_query(query)

        documents, total, used_reranker = await self._backend.hybrid_search(
            query_text=query,
            query_vector=query_vector,
            filters=filters,
            top_k=k,
        )

        evidence = [InternalEvidence.from_search_document(doc) for doc in documents]

        if not evidence:
            logger.info("No evidence retrieved for query: %s", query)

        return InternalEvidencePackage(
            query=query,
            evidence=evidence,
            total_hits=total,
            used_semantic_reranker=used_reranker,
        )

    async def search_for_understanding(
        self,
        understanding: QueryUnderstandingResult,
        top_k: int | None = None,
    ) -> InternalEvidencePackage:
        """Convenience path used by the workflow.

        Uses the agent's rewritten ``internal_search_query`` and routes
        jurisdictions to boost-only via ``to_filters()``.
        """
        return await self.search(
            query=understanding.internal_search_query,
            filters=understanding.to_filters(),
            top_k=top_k,
        )
