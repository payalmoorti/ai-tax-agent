"""Query embedding provider.

The single most important property of this module: it must embed queries with
**the same model deployment ingestion used to embed chunks**. A mismatch does
not raise -- it returns plausible-looking but semantically meaningless results.
``EmbeddingProvider.validate_dimensions`` exists to turn that silent failure
into a loud one at startup.

The Azure SDK is imported lazily inside ``_client`` so this module can be
imported (and the Protocol used in tests) without the SDK installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from shared.contracts.index_schema import VECTOR_FIELD_DIMENSIONS

if TYPE_CHECKING:  # pragma: no cover
    from openai import AsyncAzureOpenAI


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Interface the search tool depends on. Fakeable in tests."""

    async def embed_query(self, text: str) -> list[float]:
        ...


class AzureOpenAIEmbeddingProvider:
    """Embeds queries using a Foundry-hosted Azure OpenAI deployment.

    Parameters
    ----------
    endpoint:
        Foundry / Azure OpenAI endpoint.
    deployment:
        Embedding deployment name. Must match the deployment ingestion used.
    api_version:
        Azure OpenAI API version.
    api_key:
        Local development only. When ``None``, ``DefaultAzureCredential`` is
        used, which resolves to ``az login`` locally and to the Container App's
        Managed Identity in Azure.
    expected_dimensions:
        Asserted against the first embedding returned.
    """

    def __init__(
        self,
        endpoint: str,
        deployment: str,
        api_version: str = "2024-10-21",
        api_key: str | None = None,
        expected_dimensions: int = VECTOR_FIELD_DIMENSIONS,
    ) -> None:
        self._endpoint = endpoint
        self._deployment = deployment
        self._api_version = api_version
        self._api_key = api_key
        self._expected_dimensions = expected_dimensions
        self._cached_client: "AsyncAzureOpenAI | None" = None

    def _client(self) -> "AsyncAzureOpenAI":
        if self._cached_client is not None:
            return self._cached_client

        from openai import AsyncAzureOpenAI

        if self._api_key:
            client = AsyncAzureOpenAI(
                azure_endpoint=self._endpoint,
                api_key=self._api_key,
                api_version=self._api_version,
            )
        else:
            from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

            token_provider = get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            )
            client = AsyncAzureOpenAI(
                azure_endpoint=self._endpoint,
                azure_ad_token_provider=token_provider,
                api_version=self._api_version,
            )

        self._cached_client = client
        return client

    async def embed_query(self, text: str) -> list[float]:
        response = await self._client().embeddings.create(
            model=self._deployment,
            input=text,
        )
        vector = response.data[0].embedding
        self.validate_dimensions(vector)
        return vector

    def validate_dimensions(self, vector: list[float]) -> None:
        """Fail loudly on an embedding/index dimension mismatch.

        Raises
        ------
        ValueError
            If the vector length does not match the index vector field.
        """
        if len(vector) != self._expected_dimensions:
            raise ValueError(
                f"Embedding dimension mismatch: deployment "
                f"{self._deployment!r} returned {len(vector)} dimensions but the "
                f"search index vector field expects {self._expected_dimensions}. "
                "Confirm the runtime embedding deployment matches the one "
                "ingestion used, and that VECTOR_FIELD_DIMENSIONS in "
                "shared/contracts/index_schema.py is correct."
            )

    async def aclose(self) -> None:
        if self._cached_client is not None:
            await self._cached_client.close()
            self._cached_client = None
