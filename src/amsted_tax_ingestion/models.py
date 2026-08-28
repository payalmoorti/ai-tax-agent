from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field
class ExtractedDocument(BaseModel):
    text:str
    metadata:dict[str,Any]=Field(default_factory=dict)
class EnrichmentMetadata(BaseModel):
    jurisdiction:str=""
    source_id:str=""
    topic:str=""
    scenario_id:str=""
    authority_level:str=""
class NormalizedDocument(BaseModel):
    document_id:str
    source_file_name:str
    source_path:str
    document_type:str
    upload_timestamp:str=""
    extraction_timestamp:str=Field(default_factory=lambda:datetime.now(timezone.utc).isoformat())
    metadata:dict[str,Any]=Field(default_factory=dict)
    content:str
    enrichment:EnrichmentMetadata=Field(default_factory=EnrichmentMetadata)
class Chunk(BaseModel):
    chunk_id:str
    document_id:str
    chunk_number:int
    content:str
    source_file_name:str
    source_path:str
    document_type:str
    jurisdiction:str=""
    source_id:str=""
    topic:str=""
    scenario_id:str=""
    authority_level:str=""
    citation:str=""
    content_vector:list[float]|None=None
