from amsted_tax_ingestion.config import Settings
from amsted_tax_ingestion.utils import configure_logging

from amsted_tax_ingestion.indexing import recreate_index
s=Settings();configure_logging(s.log_level);print(recreate_index(s))
