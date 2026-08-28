from amsted_tax_ingestion.config import Settings
from amsted_tax_ingestion.utils import configure_logging

import argparse,json
from amsted_tax_ingestion.indexing import validate
p=argparse.ArgumentParser();p.add_argument("--document-id");a=p.parse_args();s=Settings();configure_logging(s.log_level);print(json.dumps(validate(s,a.document_id),indent=2))
