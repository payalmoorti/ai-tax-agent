"""Run the full ingestion pipeline.

    python scripts/run_pipeline.py --dry-run --limit 3
    python scripts/run_pipeline.py --prefix memos/
    python scripts/run_pipeline.py --on-enrichment-failure fail
    python scripts/run_pipeline.py --force

Exit codes: 0 = all documents succeeded, 1 = one or more failed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli import header, kv, section  # noqa: E402

from amsted_tax_ingestion.config import Settings  # noqa: E402
from amsted_tax_ingestion.pipeline import Pipeline  # noqa: E402
from amsted_tax_ingestion.utils import configure_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Amsted tax ingestion pipeline")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true",
                        help="Stop before embeddings and indexing")
    parser.add_argument("--skip-enrichment", action="store_true",
                        help="Skip the LLM call; metadata stays n/a")
    parser.add_argument("--on-enrichment-failure", choices=["fail", "index_anyway"],
                        default="index_anyway")
    parser.add_argument("--force", action="store_true",
                        help="Reprocess even if content is unchanged")
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level)

    header("AMSTED TAX INGESTION")
    kv("Container", settings.azure_blob_container_name)
    kv("Prefix", args.prefix or "(all)")
    kv("Search index", settings.azure_search_index_name)
    kv("Embedding dimensions", settings.embedding_dimensions)
    kv("Chunk size / overlap",
       f"{settings.chunk_size_tokens} / {settings.chunk_overlap_tokens}")
    kv("Dry run", args.dry_run)
    kv("Skip enrichment", args.skip_enrichment)
    kv("On enrichment failure", args.on_enrichment_failure)

    if args.dry_run:
        print("\n  DRY RUN - no embeddings generated, nothing indexed.")
    if args.skip_enrichment:
        print("\n  WARNING: enrichment disabled. All semantic metadata will be n/a.")

    result = Pipeline(settings,
                      on_enrichment_failure=args.on_enrichment_failure).run(
        prefix=args.prefix, dry_run=args.dry_run,
        skip_enrichment=args.skip_enrichment, limit=args.limit, force=args.force)

    section("SUMMARY")
    kv("Discovered", result.discovered)
    kv("Succeeded", result.succeeded)
    kv("Failed", result.failed)
    kv("Skipped (unsupported)", result.skipped)
    kv("Unchanged", result.unchanged)
    kv("Chunks created", result.chunks_created)
    kv("Chunks indexed", result.chunks_indexed)
    kv("Enrichment failures", result.enrichment_failures)

    if result.failures:
        section("FAILURES")
        for failure in result.failures:
            print(f"  {failure['blob']}\n    {failure['error']}")

    if result.enrichment_failures and args.on_enrichment_failure == "index_anyway":
        print(f"\n  Note: {result.enrichment_failures} document(s) indexed with n/a "
              f"metadata because enrichment failed.")

    print(f"\nArtifacts: {settings.output_dir}")
    if not args.dry_run and result.succeeded:
        print("\nNext: python scripts/validate_search_index.py")
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
