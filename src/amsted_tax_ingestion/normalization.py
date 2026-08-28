from pathlib import Path
from .models import ExtractedDocument,NormalizedDocument
from .utils import stable_id
def normalize(path:Path,source_path:str,extracted:ExtractedDocument,upload_timestamp=""):
    doc_id=stable_id(source_path,str(path.stat().st_size),prefix="doc_")
    return NormalizedDocument(document_id=doc_id,source_file_name=path.name,source_path=source_path,
      document_type=path.suffix.lower().lstrip("."),upload_timestamp=upload_timestamp,
      metadata=extracted.metadata,content=extracted.text)
