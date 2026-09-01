"""Create (or update) the Azure AI Search index.

    python scripts/create_search_index.py
    python scripts/create_search_index.py --force   # drop and rebuild
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli import header, kv  # noqa: E402

from amsted_tax_ingestion.config import Settings  # noqa: E402
from amsted_tax_ingestion.indexing import create_index, index_definition, recreate_index  # noqa: E402
from amsted_tax_ingestion.utils import configure_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the search index")
    parser.add_argument("--force", action="store_true",
                        help="Drop and rebuild (required if dimensions changed)")
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level)

    header("CREATE SEARCH INDEX")
    kv("Index", settings.azure_search_index_name)
    kv("Endpoint", settings.azure_search_endpoint)
    kv("Vector dimensions", settings.embedding_dimensions)
    kv("Fields", len(index_definition(settings).fields))

    print(f"\n{recreate_index(settings) if args.force else create_index(settings)}")
    print("\nNext: python scripts/run_pipeline.py --dry-run --limit 1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
