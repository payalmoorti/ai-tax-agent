"""Delete the Azure AI Search index. Destructive.

    python scripts/delete_search_index.py --yes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli import header, kv  # noqa: E402

from amsted_tax_ingestion.config import Settings  # noqa: E402
from amsted_tax_ingestion.indexing import delete_index  # noqa: E402
from amsted_tax_ingestion.utils import configure_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete the search index")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level)
    name = settings.azure_search_index_name

    header("DELETE SEARCH INDEX")
    kv("Index", name)

    if not args.yes:
        print(f"\nThis permanently deletes '{name}' and every indexed record.")
        if input("Type the index name to confirm: ").strip() != name:
            print("Aborted.")
            return 1

    print(f"\n{delete_index(settings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
