from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Blob / Table Storage ---
    azure_storage_connection_string: str = ""
    azure_blob_container_name: str = ""
    azure_table_name: str = "TaxAgentIngestionStatus"
    azure_metadata_table_name: str = "TaxFilesMetadata"

    # --- Azure AI Search ---
    azure_search_endpoint: str = ""
    azure_search_api_key: str = ""
    azure_search_index_name: str = "amsted-tax-documents"

    # --- Azure AI Foundry ---
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-10-21"
    embedding_deployment_name: str = ""
    chat_deployment_name: str = ""
    foundry_project_name: str = ""

    # MUST match the deployed embedding model.
    #   text-embedding-3-large -> 3072
    #   text-embedding-3-small -> 1536
    #   text-embedding-ada-002 -> 1536
    # Changing this requires scripts/recreate_search_index.py.
    embedding_dimensions: int = 1536

    # --- Chunking ---
    chunk_size_tokens: int = 800
    chunk_overlap_tokens: int = 120

    # --- Batching ---
    embedding_batch_size: int = 16
    search_upload_batch_size: int = 100

    # --- Local artifacts ---
    output_dir: Path = Path("sample_outputs")
    log_level: str = "INFO"

    # --- Optional: corpus-assigned scenario IDs ---
    # JSON mapping blob name -> scenario ID(s):
    #   {"memos/nexus_il.pdf": ["SCN-014", "SCN-022"]}
    # Read by pipeline.py. Absent file is fine; scenario_id stays "n/a".
    scenario_manifest_path: Path = Path("scenarios.json")

    # --- Optional: LLM thread-segmentation fallback ---
    enable_llm_thread_fallback: bool = False

    def require(self, *names: str):
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise ValueError("Missing configuration: " + ", ".join(missing))
        return self
