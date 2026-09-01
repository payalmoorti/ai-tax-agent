"""Inspect LLM metadata enrichment. Requires the Foundry chat deployment.

Accepts a normalized JSON artifact, a document_id, or a raw source file.

    python scripts/test_metadata_enrichment.py doc_a1b2c3d4
    python scripts/test_metadata_enrichment.py samples/nexus_memo.pdf
    python scripts/test_metadata_enrichment.py doc_a1b2c3d4 --repeat 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli import (  # noqa: E402
    artifact_dir, enrichment_status_line, fail, header, kv,
    metadata_table, resolve_input, saved, section,
)

from amsted_tax_ingestion.config import Settings  # noqa: E402
from amsted_tax_ingestion.enrichment import MetadataEnricher  # noqa: E402
from amsted_tax_ingestion.extraction import is_supported  # noqa: E402
from amsted_tax_ingestion.models import NA, NormalizedDocument  # noqa: E402
from amsted_tax_ingestion.utils import configure_logging, read_json, write_json  # noqa: E402

SEMANTIC_FIELDS = ("tax_topic", "jurisdiction", "legal_entity",
                   "business_unit", "tax_year_or_effective_period", "authority_level")
GOVERNANCE_FIELDS = ("validity_status", "current_or_superseded", "access_classification")


def load_document(path: Path, settings: Settings) -> NormalizedDocument:
    if path.exists() and is_supported(path) and path.suffix.lower() != ".json":
        from amsted_tax_ingestion.extraction import ExtractionRouter
        from amsted_tax_ingestion.normalization import normalize
        print(f"Source file detected - extracting and normalizing {path.name} first...")
        extracted = ExtractionRouter().extract(path)
        return normalize(path,
                         f"{settings.azure_blob_container_name or 'raw'}/{path.name}",
                         extracted)
    return NormalizedDocument.model_validate(
        read_json(resolve_input(path, settings, "normalized")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Test LLM metadata enrichment")
    parser.add_argument("file", type=Path,
                        help="Normalized JSON, document_id, or source file")
    parser.add_argument("--repeat", type=int, default=1,
                        help="Call N times to check classification stability")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level)
    doc = load_document(args.file, settings)

    header(f"METADATA ENRICHMENT - {doc.source_file_name}")
    kv("document_id", doc.document_id)
    kv("source_type", doc.metadata.source_type)
    kv("Characters sent", f"{min(len(doc.content), 12000):,} of {doc.char_count:,}")
    kv("Chat deployment", settings.chat_deployment_name or "?")

    before = {n: getattr(doc.metadata, n) for n in SEMANTIC_FIELDS}

    enricher = MetadataEnricher(settings)
    runs = []
    for attempt in range(1, args.repeat + 1):
        if args.repeat > 1:
            print(f"\nRun {attempt} of {args.repeat}...")
        doc = enricher.enrich(doc)
        runs.append({n: getattr(doc.metadata, n) for n in SEMANTIC_FIELDS})

    section("RESULT")
    enrichment_status_line(doc.metadata)

    if doc.metadata.enrichment_status == "failed":
        section("FULL METADATA")
        metadata_table(doc.metadata)
        fail("Enrichment failed - see the rationale above.")

    section("FIELDS POPULATED BY THE LLM")
    for name in SEMANTIC_FIELDS:
        old, new = before[name], getattr(doc.metadata, name)
        marker = "unchanged" if old == new else (
            "still n/a" if new == NA else f"{old} -> {new}")
        print(f"  {name:<32} {new:<28} ({marker})")

    section("GOVERNANCE FIELDS (must remain n/a)")
    leaked = [f for f in GOVERNANCE_FIELDS if getattr(doc.metadata, f) != NA]
    for name in GOVERNANCE_FIELDS:
        print(f"  {name:<32} {getattr(doc.metadata, name)}")
    if leaked:
        print(f"\n  WARNING: the model populated deferred fields: {', '.join(leaked)}")
        print("  These must stay 'n/a' until Amsted supplies the classification scheme.")
    else:
        print("\n  OK - all three correctly left as n/a.")

    if args.repeat > 1:
        section(f"STABILITY ACROSS {args.repeat} RUNS")
        for name in SEMANTIC_FIELDS:
            values = {run[name] for run in runs}
            print(f"  {name:<32} "
                  f"{'stable' if len(values) == 1 else f'VARIES: {sorted(values)}'}")

    section("FULL METADATA")
    metadata_table(doc.metadata)

    if not args.no_save:
        path = artifact_dir(settings, "enriched") / f"{doc.document_id}.json"
        write_json(path, doc)
        saved(path)
        print(f"\nNext: python scripts/test_chunking.py {doc.document_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
