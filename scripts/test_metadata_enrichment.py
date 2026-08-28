import argparse
from pathlib import Path
from amsted_tax_ingestion.config import Settings
from amsted_tax_ingestion.models import NormalizedDocument
from amsted_tax_ingestion.enrichment import MetadataEnricher
from amsted_tax_ingestion.utils import read_json,write_json
p=argparse.ArgumentParser();p.add_argument("file",type=Path);a=p.parse_args();doc=NormalizedDocument.model_validate(read_json(a.file));doc=MetadataEnricher(Settings()).enrich(doc)
print(doc.enrichment.model_dump());write_json(Path("sample_outputs/enriched")/(doc.document_id+".json"),doc)
