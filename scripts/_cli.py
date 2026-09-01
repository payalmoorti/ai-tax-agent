"""Shared helpers for the command-line scripts.

Consistent sections, artifact locations, and exit codes (0 = ok, 1 = problem).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from amsted_tax_ingestion.config import Settings
from amsted_tax_ingestion.models import NA

WIDTH = 72


def header(title: str) -> None:
    print("\n" + "=" * WIDTH)
    print(title)
    print("=" * WIDTH)


def section(title: str) -> None:
    print("\n" + "-" * WIDTH)
    print(title)
    print("-" * WIDTH)


def kv(label: str, value, width: int = 30) -> None:
    print(f"  {label:<{width}} {value}")


def preview(text: str, limit: int = 1500) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... [{len(text) - limit:,} more characters truncated]"


def artifact_dir(settings: Settings, stage: str) -> Path:
    base = getattr(settings, "output_dir", None) or Path("sample_outputs")
    path = Path(base) / stage
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_input(path: Path, settings: Settings, stage: str) -> Path:
    """Accept a full path, a file name, or a document_id."""
    path = Path(path)
    if path.exists():
        return path

    directory = artifact_dir(settings, stage)
    for candidate in (directory / path.name, directory / f"{path.name}.json"):
        if candidate.exists():
            return candidate

    matches = sorted(directory.glob(f"{path.name}*.json"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        fail(f"'{path.name}' matches {len(matches)} files in {directory}:\n  "
             + "\n  ".join(m.name for m in matches[:10]))

    fail(f"Could not find '{path}'.\nLooked in: {directory}\n"
         f"Pass a full path, a file name, or a document_id.")


def metadata_table(metadata) -> None:
    """Print the Seed Corpus v0.1 metadata model in logical groups."""
    groups = {
        "Identity / provenance": [
            ("scenario_id", metadata.scenario_id),
            ("scenario_ids", metadata.scenario_ids or "[]"),
            ("source_id", metadata.source_id),
            ("source_file", metadata.source_file),
            ("source_type", metadata.source_type),
            ("source_date", metadata.source_date),
            ("author", metadata.author),
            ("page_number", metadata.page_number),
        ],
        "Tax classification": [
            ("tax_topic", metadata.tax_topic),
            ("jurisdiction", metadata.jurisdiction),
            ("tax_year_or_effective_period", metadata.tax_year_or_effective_period),
            ("authority_level", metadata.authority_level),
        ],
        "Organizational": [
            ("legal_entity", metadata.legal_entity),
            ("business_unit", metadata.business_unit),
        ],
        "Email": [
            ("thread_subject", metadata.thread_subject),
            ("message_date", metadata.message_date),
        ],
        "Governance (deferred)": [
            ("validity_status", metadata.validity_status),
            ("current_or_superseded", metadata.current_or_superseded),
            ("access_classification", metadata.access_classification),
        ],
    }
    for group, fields in groups.items():
        print(f"\n  {group}")
        for name, value in fields:
            flag = "  " if value not in (NA, None, "[]", []) else " ."
            print(f"   {flag} {name:<30} {value}")


def enrichment_status_line(metadata) -> None:
    status = getattr(metadata, "enrichment_status", "unknown")
    icon = {"ok": "OK", "failed": "FAILED", "skipped": "SKIPPED"}.get(status, "PENDING")
    print(f"\n  Enrichment: {icon}", end="")
    if status == "ok":
        print(f"  (confidence {metadata.enrichment_confidence:.2f}, "
              f"model {metadata.enrichment_model})")
        if metadata.enrichment_rationale:
            print(f"    rationale: {metadata.enrichment_rationale}")
    elif status == "failed":
        print(f"\n    {metadata.enrichment_rationale}")
    else:
        print()


def saved(path: Path) -> None:
    print(f"\nSaved: {path}")


def fail(message: str, code: int = 1) -> None:
    print(f"\nERROR: {message}", file=sys.stderr)
    sys.exit(code)


def dump_json(payload) -> str:
    return json.dumps(payload, indent=2, default=str)
