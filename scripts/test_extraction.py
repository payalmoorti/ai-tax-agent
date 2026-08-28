import argparse
from pathlib import Path
from amsted_tax_ingestion.extraction import ExtractionRouter
from amsted_tax_ingestion.utils import write_json
p=argparse.ArgumentParser();p.add_argument("file",type=Path);a=p.parse_args();r=ExtractionRouter().extract(a.file)
print(r.text);print(r.metadata);write_json(Path("sample_outputs/extracted")/(a.file.name+".json"),r)
