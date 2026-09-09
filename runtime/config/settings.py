"""Runtime configuration.

All endpoints, deployment names and connection strings come from environment or
Key Vault. No secrets in source, no secrets in prompts, no secrets in the image.

Azure-to-Azure auth uses Managed Identity via ``DefaultAzureCredential``; API
keys are supported only for local development and are rejected when
``environment`` is ``prod``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven runtime settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Environment ------------------------------------------------------
    environment: Literal["local", "dev", "prod"] = "local"
    log_level: str = "INFO"

    # --- Azure OpenAI / Foundry ------------------------------------------
    azure_openai_endpoint: str = Field(default="", alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_api_version: str = "2024-10-21"

    #: Small/fast model -- query understanding only.
    query_model_deployment: str = Field(default="", alias="QUERY_MODEL_DEPLOYMENT")
    #: Strong reasoning model -- precedent analysis.
    precedent_model_deployment: str = Field(default="", alias="PRECEDENT_MODEL_DEPLOYMENT")
    #: Strong reasoning model -- evidence synthesis.
    synthesis_model_deployment: str = Field(default="", alias="SYNTHESIS_MODEL_DEPLOYMENT")

    #: Local development only. Must be empty in prod -- use Managed Identity.
    azure_openai_api_key: str | None = Field(default=None, alias="AZURE_OPENAI_API_KEY")

    # --- Azure AI Search --------------------------------------------------
    azure_search_endpoint: str = Field(default="", alias="AZURE_SEARCH_ENDPOINT")
    azure_search_index: str = Field(default="", alias="AZURE_SEARCH_INDEX")
    azure_search_api_key: str | None = Field(default=None, alias="AZURE_SEARCH_API_KEY")

    #: Retrieval tuning. Keep top_k generous -- precedent analysis needs enough
    #: material to detect differences, not just the single best hit.
    search_top_k: int = Field(default=10, ge=1, le=50)
    use_semantic_reranker: bool = True

    # --- Platform ---------------------------------------------------------
    key_vault_uri: str | None = Field(default=None, alias="KEY_VAULT_URI")
    applicationinsights_connection_string: str | None = Field(
        default=None, alias="APPLICATIONINSIGHTS_CONNECTION_STRING"
    )

    # --- External research ------------------------------------------------
    #: 'mock' until a real provider is contractually and technically confirmed.
    external_tax_provider: str = Field(default="mock", alias="EXTERNAL_TAX_PROVIDER")

    # --- Policy -----------------------------------------------------------
    policy_config_path: str = "runtime/config/policy.yaml"

    # --- Prompt versioning ------------------------------------------------
    query_prompt_version: str = "query_understanding_v1"
    precedent_prompt_version: str = "precedent_analysis_v1"
    synthesis_prompt_version: str = "synthesis_v1"

    @property
    def use_managed_identity(self) -> bool:
        return not self.azure_openai_api_key

    @model_validator(mode="after")
    def _no_keys_in_prod(self) -> "Settings":
        if self.environment == "prod":
            if self.azure_openai_api_key or self.azure_search_api_key:
                raise ValueError(
                    "API keys are not permitted in prod. Use Managed Identity."
                )
        return self

    @model_validator(mode="after")
    def _required_when_deployed(self) -> "Settings":
        if self.environment == "local":
            return self
        missing = [
            name
            for name, value in (
                ("AZURE_OPENAI_ENDPOINT", self.azure_openai_endpoint),
                ("AZURE_SEARCH_ENDPOINT", self.azure_search_endpoint),
                ("AZURE_SEARCH_INDEX", self.azure_search_index),
                ("QUERY_MODEL_DEPLOYMENT", self.query_model_deployment),
                ("PRECEDENT_MODEL_DEPLOYMENT", self.precedent_model_deployment),
                ("SYNTHESIS_MODEL_DEPLOYMENT", self.synthesis_model_deployment),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                f"Missing required settings for environment="
                f"{self.environment}: {', '.join(missing)}"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Inject via FastAPI ``Depends`` rather than importing
    a module-level singleton -- keeps tests able to override."""
    return Settings()
