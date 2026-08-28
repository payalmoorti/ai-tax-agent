import argparse
from pathlib import Path
from amsted_tax_ingestion.config import Settings
from amsted_tax_ingestion.models import NormalizedDocument
from amsted_tax_ingestion.chunking import TokenChunker
from amsted_tax_ingestion.utils import read_json,write_json
p=argparse.ArgumentParser();p.add_argument("file",type=Path);a=p.parse_args();s=Settings();doc=NormalizedDocument.model_validate(read_json(a.file));chunks=TokenChunker(s.chunk_size_tokens,s.chunk_overlap_tokens).chunk(doc)
print(f"chunks={len(chunks)}");[print(c.chunk_id,c.chunk_number,len(c.content)) for c in chunks];write_json(Path("sample_outputs/chunked")/(doc.document_id+".json"),[c.model_dump(mode="json") for c in chunks])
