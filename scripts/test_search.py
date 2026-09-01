"""Query the index to sanity-check retrieval before building the agent.

    python scripts/test_search.py "Illinois nexus remote employees"
    python scripts/test_search.py "payroll factor" --filter "jurisdiction eq 'Illinois'"
    python scripts/test_search.py "transfer pricing" --top 10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli import fail, header, kv, section  # noqa: E402

from amsted_tax_ingestion.config import Settings  # noqa: E402
from amsted_tax_ingestion.indexing import search_preview  # noqa: E402
from amsted_tax_ingestion.utils import configure_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Query the search index")
    parser.add_argument("query")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--filter", dest="filter_expression",
                        help="OData filter, e.g. \"jurisdiction eq 'Illinois'\"")
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level)

    header("SEARCH PREVIEW")
    kv("Index", settings.azure_search_index_name)
    kv("Query", args.query)
    kv("Filter", args.filter_expression or "(none)")

    try:
        results = search_preview(settings, args.query, args.top, args.filter_expression)
    except Exception as exc:  # noqa: BLE001
        fail(f"Search failed: {exc}")

    if not results:
        print("\nNo results. Check the index is populated and the filter is valid.")
        return 1

    section(f"RESULTS ({len(results)})")
    for position, item in enumerate(results, start=1):
        reranker = item.get("reranker_score")
        score = f"{item['score']:.3f}" if item.get("score") else "?"
        if reranker:
            score += f" (reranker {reranker:.3f})"
        print(f"\n  {position}. {item['source_file']}  [{score}]")
        kv("jurisdiction", item["jurisdiction"], width=18)
        kv("tax_topic", item["tax_topic"], width=18)
        kv("authority_level", item["authority_level"], width=18)
        kv("citation", item["citation"], width=18)
        print(f"      {item['preview']}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
