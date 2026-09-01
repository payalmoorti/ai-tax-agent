"""Inspect chunking output.

Accepts an enriched artifact, a normalized artifact, a document_id, or a source
file. Enriched input is preferred; --skip-enrichment-check chunks without the LLM.

    python scripts/test_chunking.py doc_a1b2c3d4
    python scripts/test_chunking.py samples/thread.msg --skip-enrichment-check
    python scripts/test_chunking.py doc_a1b2c3d4 --size 400 --overlap 60
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli import (  # noqa: E402
    artifact_dir, fail, header, kv, preview, resolve_input, saved, section,
)

from amsted_tax_ingestion.chunking import Chunker, EnrichmentMissingError  # noqa: E402
from amsted_tax_ingestion.config import Settings  # noqa: E402
from amsted_tax_ingestion.extraction import is_supported  # noqa: E402
from amsted_tax_ingestion.models import NA, NormalizedDocument  # noqa: E402
from amsted_tax_ingestion.utils import configure_logging, read_json, write_json  # noqa: E402


def load_document(path: Path, settings: Settings) -> NormalizedDocument:
    if path.exists() and is_supported(path) and path.suffix.lower() != ".json":
        from amsted_tax_ingestion.extraction import ExtractionRouter
        from amsted_tax_ingestion.normalization import normalize
        print(f"Source file detected - extracting and normalizing {path.name} first...")
        extracted = ExtractionRouter().extract(path)
        return normalize(path,
                         f"{settings.azure_blob_container_name or 'raw'}/{path.name}",
                         extracted)

    for stage in ("enriched", "normalized"):
        directory = artifact_dir(settings, stage)
        for candidate in (Path(path), directory / path.name,
                          directory / f"{path.name}.json"):
            if candidate.exists() and candidate.suffix == ".json":
                print(f"Loaded {stage} artifact: {candidate.name}")
                return NormalizedDocument.model_validate(read_json(candidate))

    return NormalizedDocument.model_validate(
        read_json(resolve_input(path, settings, "enriched")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Test chunking")
    parser.add_argument("file", type=Path)
    parser.add_argument("--size", type=int)
    parser.add_argument("--overlap", type=int)
    parser.add_argument("--skip-enrichment-check", action="store_true",
                        help="Chunk without enrichment (metadata will be n/a)")
    parser.add_argument("--show", type=int, default=3)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level)
    doc = load_document(args.file, settings)

    size = args.size or settings.chunk_size_tokens
    overlap = args.overlap if args.overlap is not None else settings.chunk_overlap_tokens

    header(f"CHUNKING - {doc.source_file_name}")
    kv("document_id", doc.document_id)
    kv("Strategy", "email_message" if doc.is_email_thread else "token")
    kv("Chunk size / overlap", f"{size} / {overlap} tokens")
    kv("Enrichment status", doc.metadata.enrichment_status)
    kv("Content characters", f"{doc.char_count:,}")
    if doc.is_email_thread:
        kv("Messages in thread", len(doc.messages))

    try:
        chunker = Chunker(size, overlap,
                          require_enrichment=not args.skip_enrichment_check)
        chunks = chunker.chunk(doc)
    except EnrichmentMissingError as exc:
        fail(f"{exc}\n\n"
             f"Either run:  python scripts/test_metadata_enrichment.py {doc.document_id}\n"
             f"or re-run this script with --skip-enrichment-check.")
    except ValueError as exc:
        fail(str(exc))

    if not chunks:
        fail("Chunking produced no chunks.")

    tokens = [c.token_count for c in chunks]
    section("SUMMARY")
    kv("Chunks", len(chunks))
    kv("Tokens min / avg / max",
       f"{min(tokens)} / {sum(tokens) // len(tokens)} / {max(tokens)}")
    kv("Over budget", sum(1 for t in tokens if t > size))

    if doc.is_email_thread:
        per_message: dict[int, int] = {}
        for chunk in chunks:
            per_message[chunk.message_index] = per_message.get(chunk.message_index, 0) + 1
        kv("Messages -> chunks",
           ", ".join(f"msg{k}:{v}" for k, v in sorted(per_message.items())))
        split = [k for k, v in per_message.items() if v > 1]
        if split:
            kv("Sub-split messages", split)

    na_fields = [n for n in ("scenario_id", "tax_topic", "jurisdiction", "legal_entity",
                             "business_unit", "authority_level",
                             "tax_year_or_effective_period")
                 if getattr(chunks[0].metadata, n) == NA]
    if na_fields:
        print(f"\n  Note: n/a on chunks -> {', '.join(na_fields)}")

    limit = len(chunks) if args.all else args.show
    section(f"CHUNKS (showing {min(limit, len(chunks))} of {len(chunks)})")
    for chunk in chunks[:limit]:
        print(f"\n  [{chunk.chunk_number}/{chunk.total_chunks}] {chunk.chunk_id}")
        kv("tokens", chunk.token_count, width=16)
        kv("strategy", chunk.chunk_strategy, width=16)
        if chunk.message_index is not None:
            kv("message", f"{chunk.message_index}/{chunk.message_total}", width=16)
            kv("author", chunk.metadata.author, width=16)
            kv("message_date", chunk.metadata.message_date, width=16)
            kv("page_number", chunk.metadata.page_number, width=16)
        kv("citation", chunk.citation, width=16)
        kv("topic / jurisdiction",
           f"{chunk.metadata.tax_topic} / {chunk.metadata.jurisdiction}", width=16)
        body = chunk.content if args.verbose else preview(chunk.content, 400)
        print("      " + body.replace("\n", "\n      "))

    if not args.no_save:
        path = artifact_dir(settings, "chunked") / f"{doc.document_id}.json"
        write_json(path, [c.model_dump(mode="json") for c in chunks])
        saved(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
