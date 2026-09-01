"""Embedding generation with retry and dimension verification.

Fixes vs. the previous version:
  * `dimensions` is only sent for models that support it (ada-002 errors on it).
  * Retries transient API failures instead of losing the whole run.
  * Verifies the returned vector length matches the configured index dimensions —
    a mismatch otherwise fails much later, at upload time, with an opaque error.
  * Skips empty content instead of sending an invalid request.
"""
from __future__ import annotations

import logging

from openai import AzureOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

# Only v3 embedding models accept the `dimensions` parameter.
_SUPPORTS_DIMENSIONS = ("text-embedding-3",)


class EmbeddingGenerator:
    def __init__(self, settings):
        settings.require(
            "azure_openai_endpoint", "azure_openai_api_key", "embedding_deployment_name"
        )
        self.deployment = settings.embedding_deployment_name
        self.dimensions = settings.embedding_dimensions
        self.batch_size = settings.embedding_batch_size
        self.client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )
        model_hint = (getattr(settings, "embedding_model_name", "") or self.deployment).lower()
        self._send_dimensions = any(m in model_hint for m in _SUPPORTS_DIMENSIONS)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
    def _embed(self, texts: list[str]) -> list[list[float]]:
        kwargs = {"model": self.deployment, "input": texts}
        if self._send_dimensions and self.dimensions:
            kwargs["dimensions"] = self.dimensions
        response = self.client.embeddings.create(**kwargs)
        return [item.embedding for item in response.data]

    def apply(self, chunks):
        pending = [c for c in chunks if c.content and c.content.strip()]
        if len(pending) != len(chunks):
            log.warning("Skipping %s chunk(s) with empty content.", len(chunks) - len(pending))

        for i in range(0, len(pending), self.batch_size):
            batch = pending[i:i + self.batch_size]
            vectors = self._embed([c.content for c in batch])

            for chunk, vector in zip(batch, vectors):
                if self.dimensions and len(vector) != self.dimensions:
                    raise ValueError(
                        f"Embedding dimension mismatch: model returned {len(vector)} "
                        f"but EMBEDDING_DIMENSIONS is {self.dimensions}. "
                        f"Fix .env and recreate the search index."
                    )
                chunk.content_vector = vector

            log.info("Embedded %s/%s chunks", min(i + len(batch), len(pending)), len(pending))

        return chunks
