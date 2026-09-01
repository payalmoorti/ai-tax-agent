"""Inspect normalization: extraction -> cleaned, consistent internal representation.

Shows what cleaning removed and which metadata fields were populated
deterministically versus left for the enricher. No Azure calls required.

    python scripts/test_normalization.py samples/nexus_memo.pdf
    python scripts/test_normalization.py thread.msg --diff
    python scripts/test_normalization.py memo.pdf --scenario SCN-014 --scenario SCN-022
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli import (  # noqa: E402
    artifact_dir, enrichment_status_line, fail, header, kv,
    metadata_table, preview, saved, section,
)

from amsted_tax_ingestion.config import Settings  # noqa: E402
from amsted_tax_ingestion.extraction import ExtractionRouter, is_supported  # noqa: E402
from amsted_tax_ingestion.models import NA  # noqa: E402
from amsted_tax_ingestion.normalization import normalize  # noqa: E402
from amsted_tax_ingestion.utils import configure_logging, write_json  # noqa: E402


def show_diff(raw: str, cleaned: str, limit: int, *, is_thread: bool = False) -> None:
    """Lines removed by cleaning.

    For threads the content is also RESTRUCTURED chronologically, so a raw line
    diff overstates deletions. Lines that merely moved are filtered out.
    """
    raw_lines = [l.rstrip() for l in raw.split("\n")]
    clean_lines = [l.rstrip() for l in cleaned.split("\n")]

    removed, added = [], []
    for line in difflib.unified_diff(raw_lines, clean_lines, n=0, lineterm=""):
        if line[:3] in ("---", "+++", "@@ "):
            continue
        if line.startswith("-") and line[1:].strip():
            removed.append(line[1:])
        elif line.startswith("+") and line[1:].strip():
            added.append(line[1:])

    if is_thread:
        blob = "\n".join(clean_lines)
        moved = [l for l in removed if l.strip() and l.strip() in blob]
        removed = [l for l in removed if l.strip() and l.strip() not in blob]
        if moved:
            print(f"\n  ({len(moved)} line(s) reordered into chronological thread "
                  f"structure, not deleted)")

    section(f"REMOVED BY CLEANING ({len(removed)} lines)")
    if not removed:
        print("  (nothing removed)")
    for line in removed[:limit]:
        print(f"  - {line}")
    if len(removed) > limit:
        print(f"  ... and {len(removed) - limit} more")

    rewritten = [l for l in added if "removed]" in l]
    if rewritten:
        section(f"REDACTED IN PLACE ({len(rewritten)} lines)")
        for line in rewritten[:limit]:
            print(f"  ~ {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test normalization")
    parser.add_argument("file", type=Path)
    parser.add_argument("--scenario", action="append", dest="scenarios", default=[],
                        help="Corpus-assigned scenario ID (repeatable)")
    parser.add_argument("--diff", action="store_true", help="Show what cleaning removed")
    parser.add_argument("--diff-limit", type=int, default=40)
    parser.add_argument("--preview", type=int, default=1500)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--messages", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    if not args.file.exists():
        fail(f"File not found: {args.file}")
    if not is_supported(args.file):
        fail(f"Unsupported file type '{args.file.suffix}'")

    settings = Settings()
    configure_logging(settings.log_level)

    extracted = ExtractionRouter().extract(args.file)
    doc = normalize(
        args.file,
        f"{settings.azure_blob_container_name or 'raw'}/{args.file.name}",
        extracted,
        scenario_ids=args.scenarios,
    )

    header(f"NORMALIZATION - {args.file.name}")
    kv("document_id", doc.document_id)
    kv("document_type", doc.document_type)
    kv("extractor", doc.extractor)
    kv("content_hash", doc.content_hash[:24] + "...")
    kv("page_count", doc.page_count)
    kv("Email thread", doc.is_email_thread)
    if doc.is_email_thread:
        kv("Messages", len(doc.messages))

    raw_chars, clean_chars = len(extracted.text), doc.char_count
    delta = raw_chars - clean_chars
    percent = (delta / raw_chars * 100) if raw_chars else 0.0
    section("CLEANING IMPACT")
    kv("Raw characters", f"{raw_chars:,}")
    kv("Normalized characters", f"{clean_chars:,}")
    kv("Net change", f"{delta:,} ({percent:.1f}%)")
    if doc.is_email_thread:
        print("\n  Note: thread content is restructured chronologically as well as "
              "cleaned,\n  so this reflects both operations.")
    elif percent > 60:
        print("\n  WARNING: more than 60% of the text was removed.")
        print("  Re-run with --diff to confirm the cleaner is not over-deleting.")

    section("METADATA (Seed Corpus v0.1)")
    print("  Legend:  . = n/a (expected before enrichment for semantic fields)")
    metadata_table(doc.metadata)
    enrichment_status_line(doc.metadata)

    pending = [n for n in ("tax_topic", "jurisdiction", "legal_entity",
                           "business_unit", "authority_level")
               if getattr(doc.metadata, n) == NA]
    if pending:
        print(f"\n  Awaiting enrichment: {', '.join(pending)}")

    if doc.messages:
        section(f"MESSAGES ({len(doc.messages)}, chronological)")
        for message in doc.messages:
            print(f"\n  [{message.message_index}/{len(doc.messages)}] {message.sender}")
            kv("date", message.message_date, width=12)
            kv("to", ", ".join(message.recipients) or "(none)", width=12)
            kv("page", message.page_number, width=12)
            body = message.body if args.messages else preview(message.body, 300)
            print("      " + body.replace("\n", "\n      "))

    if args.diff:
        show_diff(extracted.text, doc.content, args.diff_limit,
                  is_thread=doc.is_email_thread)

    section("NORMALIZED CONTENT" if args.full else f"CONTENT PREVIEW ({args.preview} chars)")
    print(doc.content if args.full else preview(doc.content, args.preview))

    if not args.no_save:
        path = artifact_dir(settings, "normalized") / f"{doc.document_id}.json"
        write_json(path, doc)
        saved(path)
        print(f"\nNext: python scripts/test_metadata_enrichment.py {doc.document_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
