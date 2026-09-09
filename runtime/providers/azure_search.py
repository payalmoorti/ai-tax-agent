"""Azure AI Search client wrapper.

Owns exactly one thing: turning a query plus filters into raw search documents.
It performs no reasoning, no relevance judgement, and no mapping onto domain
types -- ``InternalSearchTool`` does that.

The SDK is imported lazily inside ``_client`` so that this module, and the
``SearchBackend`` Protocol it defines, can be imported in unit tests without the
``azure-search-documents`` package or any credential present.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from runtime.models.query import SearchFilters
from runtime.providers.odata import build_filter, build_scoring_parameters
from shared.contracts.index_schema import (
    F,
    RETRIEVAL_SELECT_FIELDS,
    SEMANTIC_CONFIG_NAME,
)

if TYPE_CHECKING:  # pragma: no cover
    from azure.search.documents.aio import SearchClient

logger = logging.getLogger(__name__)


@runtime_checkable
class SearchBackend(Protocol):
    """What ``InternalSearchTool`` actually depends on.

    Depending on this Protocol rather than the concrete class is what lets the
    retrieval tool be tested against fixtures with no Azure account.
    """

    async def hybrid_search(
        self,
        query_text: str,
        query_vector: list[float] | None,
        filters: SearchFilters,
        top_k: int,
    ) -> tuple[list[dict[str, Any]], int | None, bool]:
        """Return ``(documents, total_count, used_semantic_reranker)``."""
        ...


class AzureSearchProvider:
    """Hybrid retrieval against the Amsted evidence index.

    Parameters
    ----------
    endpoint:
        Search service endpoint.
    index_name:
        Index populated by the ingestion pipeline.
    api_key:
        Local development only. When ``None``, ``DefaultAzureCredential`` is
        used. The runtime identity needs only ``Search Index Data Reader`` --
        it must never be able to write to the index.
    use_semantic_reranker:
        Requires a semantic configuration on the index and a supporting SKU.
        Falls back gracefully when unavailable.
    """

    def __init__(
        self,
        endpoint: str,
        index_name: str,
        api_key: str | None = None,
        use_semantic_reranker: bool = True,
        semantic_config_name: str = SEMANTIC_CONFIG_NAME,
        vector_field: str = F.CONTENT_VECTOR,
    ) -> None:
        self._endpoint = endpoint
        self._index_name = index_name
        self._api_key = api_key
        self._use_semantic_reranker = use_semantic_reranker
        self._semantic_config_name = semantic_config_name
        self._vector_field = vector_field
        self._cached_client: "SearchClient | None" = None

    def _client(self) -> "SearchClient":
        if self._cached_client is not None:
            return self._cached_client

        from azure.search.documents.aio import SearchClient

        if self._api_key:
            from azure.core.credentials import AzureKeyCredential

            credential: Any = AzureKeyCredential(self._api_key)
        else:
            from azure.identity.aio import DefaultAzureCredential

            credential = DefaultAzureCredential()

        self._cached_client = SearchClient(
            endpoint=self._endpoint,
            index_name=self._index_name,
            credential=credential,
        )
        return self._cached_client

    async def hybrid_search(
        self,
        query_text: str,
        query_vector: list[float] | None,
        filters: SearchFilters,
        top_k: int = 10,
    ) -> tuple[list[dict[str, Any]], int | None, bool]:
        """Run keyword + vector search with optional semantic reranking.

        ``top_k`` should stay generous (10 by default). Precedent analysis needs
        enough material to detect material differences, not just the single best
        hit -- trimming this to 3 is a common and damaging over-optimisation.
        """
        from azure.search.documents.models import VectorizedQuery

        odata_filter = build_filter(filters)
        scoring_parameters = build_scoring_parameters(filters)

        kwargs: dict[str, Any] = {
            "search_text": query_text,
            "top": top_k,
            "select": list(RETRIEVAL_SELECT_FIELDS),
            "include_total_count": True,
        }

        if odata_filter:
            kwargs["filter"] = odata_filter

        if query_vector is not None:
            kwargs["vector_queries"] = [
                VectorizedQuery(
                    vector=query_vector,
                    k_nearest_neighbors=top_k,
                    fields=self._vector_field,
                )
            ]

        if scoring_parameters:
            # Only applies when the index defines a matching scoring profile.
            kwargs["scoring_parameters"] = scoring_parameters

        used_reranker = False
        if self._use_semantic_reranker:
            kwargs["query_type"] = "semantic"
            kwargs["semantic_configuration_name"] = self._semantic_config_name
            used_reranker = True

        try:
            results = await self._client().search(**kwargs)
            documents, total = await self._collect(results)
        except Exception as exc:  # noqa: BLE001
            if used_reranker and self._is_semantic_unavailable(exc):
                logger.warning(
                    "Semantic reranking unavailable (%s). Retrying without it. "
                    "Check the index semantic configuration and service SKU.",
                    exc,
                )
                kwargs.pop("query_type", None)
                kwargs.pop("semantic_configuration_name", None)
                used_reranker = False
                results = await self._client().search(**kwargs)
                documents, total = await self._collect(results)
            else:
                raise

        logger.debug(
            "search returned %d docs (total=%s, filter=%s, reranker=%s)",
            len(documents), total, odata_filter, used_reranker,
        )
        return documents, total, used_reranker

    @staticmethod
    async def _collect(results: Any) -> tuple[list[dict[str, Any]], int | None]:
        documents = [dict(doc) async for doc in results]
        try:
            total = await results.get_count()
        except Exception:  # noqa: BLE001
            total = None
        return documents, total

    @staticmethod
    def _is_semantic_unavailable(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "semantic",
                "not enabled",
                "not supported",
                "featurenotsupported",
            )
        )

    async def aclose(self) -> None:
        if self._cached_client is not None:
            await self._cached_client.close()
            self._cached_client = None
