from amsted_tax_ingestion.config import Settings
from amsted_tax_ingestion.utils import configure_logging

import argparse
from amsted_tax_ingestion.pipeline import Pipeline
p=argparse.ArgumentParser();p.add_argument("--prefix",default="");p.add_argument("--dry-run",action="store_true");p.add_argument("--skip-enrichment",action="store_true")
a=p.parse_args();s=Settings();configure_logging(s.log_level);Pipeline(s).run(a.prefix,a.dry_run,a.skip_enrichment)
