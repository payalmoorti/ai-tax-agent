from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    azure_storage_connection_string:str=""
    azure_blob_container_name:str=""
    azure_table_name:str="TaxAgentIngestionStatus"
    azure_search_endpoint:str=""
    azure_search_api_key:str=""
    azure_search_index_name:str="amsted-tax-documents"
    azure_openai_endpoint:str=""
    azure_openai_api_key:str=""
    azure_openai_api_version:str="2024-10-21"
    embedding_deployment_name:str=""
    chat_deployment_name:str=""
    foundry_project_name:str=""
    embedding_dimensions:int=1536
    chunk_size_tokens:int=800
    chunk_overlap_tokens:int=120
    embedding_batch_size:int=16
    search_upload_batch_size:int=100
    output_dir:Path=Path("sample_outputs")
    log_level:str="INFO"
    def require(self,*names:str):
        missing=[n for n in names if not getattr(self,n)]
        if missing: raise ValueError("Missing configuration: "+", ".join(missing))
        return self
