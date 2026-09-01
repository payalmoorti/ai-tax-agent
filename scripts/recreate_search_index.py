"""Drop and rebuild the index. Required after a schema or dimension change.

    python scripts/recreate_search_index.py --yes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli import header, kv  # noqa: E402

from amsted_tax_ingestion.config import Settings  # noqa: E402
from amsted_tax_ingestion.indexing import recreate_index  # noqa: E402
from amsted_tax_ingestion.utils import configure_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Recreate the search index")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level)
    name = settings.azure_search_index_name

    header("RECREATE SEARCH INDEX")
    kv("Index", name)
    kv("Vector dimensions", settings.embedding_dimensions)

    if not args.yes:
        print(f"\nThis deletes and rebuilds '{name}'. All indexed records will be lost.")
        if input("Type the index name to confirm: ").strip() != name:
            print("Aborted.")
            return 1

    print(f"\n{recreate_index(settings)}")
    print("\nNext: python scripts/run_pipeline.py --force")
    return 0


if __name__ == "__main__":
    sys.exit(main())
