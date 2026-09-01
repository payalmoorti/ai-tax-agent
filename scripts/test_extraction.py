"""Inspect extraction output for a local file. No Azure calls required.

    python scripts/test_extraction.py samples/nexus_memo.pdf
    python scripts/test_extraction.py thread.msg --messages
    python scripts/test_extraction.py memo.pdf --full
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli import artifact_dir, fail, header, kv, preview, saved, section  # noqa: E402

from amsted_tax_ingestion.config import Settings  # noqa: E402
from amsted_tax_ingestion.extraction import (  # noqa: E402
    SUPPORTED_EXTENSIONS, ExtractionRouter, is_supported,
)
from amsted_tax_ingestion.utils import configure_logging, write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Test document extraction")
    parser.add_argument("file", type=Path)
    parser.add_argument("--preview", type=int, default=1500)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--messages", action="store_true",
                        help="Print every parsed email message in full")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    if not args.file.exists():
        fail(f"File not found: {args.file}")
    if not is_supported(args.file):
        fail(f"Unsupported file type '{args.file.suffix}'.\n"
             f"Supported: {', '.join(SUPPORTED_EXTENSIONS)}")

    settings = Settings()
    configure_logging(settings.log_level)
    result = ExtractionRouter().extract(args.file)

    header(f"EXTRACTION - {args.file.name}")
    kv("Extractor", result.extractor or result.metadata.get("extractor", "?"))
    kv("Characters", f"{len(result.text):,}")
    kv("Page count", result.page_count)
    kv("Email thread", result.is_email_thread)
    if result.is_email_thread:
        kv("Thread subject", result.thread_subject)
        kv("Messages parsed", len(result.messages))

    section("EXTRACTION METADATA")
    for key, value in sorted(result.metadata.items()):
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value) or "(none)"
        kv(key, value)

    if result.messages:
        section(f"MESSAGES ({len(result.messages)}, chronological)")
        for message in result.messages:
            print(f"\n  [{message.message_index}] {message.sender}")
            kv("date", message.message_date, width=12)
            kv("to", ", ".join(message.recipients) or "(none)", width=12)
            if message.cc:
                kv("cc", ", ".join(message.cc), width=12)
            kv("subject", message.subject, width=12)
            kv("page", message.page_number, width=12)
            body = message.body if args.messages else preview(message.body, 300)
            print("      " + body.replace("\n", "\n      "))

    section("TEXT" if args.full else f"TEXT PREVIEW (first {args.preview} chars)")
    print(result.text if args.full else preview(result.text, args.preview))

    if not args.no_save:
        path = artifact_dir(settings, "extracted") / f"{args.file.name}.json"
        write_json(path, result)
        saved(path)

    if not result.text.strip():
        fail("Extraction produced no text.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
