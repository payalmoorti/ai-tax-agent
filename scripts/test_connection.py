"""Verify every Azure connection before running the pipeline.

    python scripts/test_connection.py
    python scripts/test_connection.py --verbose
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli import header, section  # noqa: E402

from amsted_tax_ingestion.config import Settings  # noqa: E402
from amsted_tax_ingestion.utils import configure_logging  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def check_env(verbose: bool) -> None:
    section("CONFIGURATION")
    for name in ("AZURE_STORAGE_CONNECTION_STRING", "AZURE_BLOB_CONTAINER_NAME",
                 "AZURE_SEARCH_ENDPOINT", "AZURE_SEARCH_API_KEY",
                 "AZURE_SEARCH_INDEX_NAME", "AZURE_OPENAI_ENDPOINT",
                 "AZURE_OPENAI_API_KEY", "EMBEDDING_DEPLOYMENT_NAME",
                 "CHAT_DEPLOYMENT_NAME"):
        value = os.getenv(name, "")
        show = value if (verbose and value and "KEY" not in name
                         and "CONNECTION" not in name) else ("set" if value else "MISSING")
        record(name, bool(value), show)


def check_blob(s: Settings) -> None:
    section("BLOB STORAGE")
    try:
        from amsted_tax_ingestion.storage import BlobSource
        blobs = list(BlobSource(s.azure_storage_connection_string,
                                s.azure_blob_container_name).list(""))
        record("Container reachable", True,
               f"{len(blobs)} blob(s) in '{s.azure_blob_container_name}'")
        for blob in blobs[:5]:
            print(f"         - {blob.name}")
        if not blobs:
            record("Documents present", False, "container is empty")
    except Exception as exc:  # noqa: BLE001
        record("Container reachable", False, str(exc)[:160])


def check_table(s: Settings) -> None:
    section("TABLE STORAGE")
    try:
        from amsted_tax_ingestion.storage import StatusStore
        StatusStore(s.azure_storage_connection_string, s.azure_table_name)
        record("Status table reachable", True, s.azure_table_name)
    except Exception as exc:  # noqa: BLE001
        record("Status table reachable", False, str(exc)[:160])


def check_search(s: Settings) -> None:
    section("AZURE AI SEARCH")
    try:
        from amsted_tax_ingestion.indexing import validate
        report = validate(s)
        record("Index reachable", True,
               f"{s.azure_search_index_name} ({report.get('document_count')} documents)")
    except Exception as exc:  # noqa: BLE001
        record("Index reachable", False, str(exc)[:160])
        print("         Run: python scripts/create_search_index.py")


def check_chat(s: Settings) -> None:
    section("CHAT DEPLOYMENT (metadata enrichment)")
    try:
        from openai import AzureOpenAI
        client = AzureOpenAI(azure_endpoint=s.azure_openai_endpoint,
                             api_key=s.azure_openai_api_key,
                             api_version=s.azure_openai_api_version)
        response = client.chat.completions.create(
            model=s.chat_deployment_name,
            messages=[{"role": "user", "content": 'Return JSON {"status":"ok"}'}],
            response_format={"type": "json_object"}, temperature=0)
        record("Chat completion", True,
               f"{s.chat_deployment_name} -> {response.choices[0].message.content.strip()[:60]}")
    except Exception as exc:  # noqa: BLE001
        record("Chat completion", False, str(exc)[:160])


def check_embedding(s: Settings) -> None:
    section("EMBEDDING DEPLOYMENT")
    try:
        from openai import AzureOpenAI
        client = AzureOpenAI(azure_endpoint=s.azure_openai_endpoint,
                             api_key=s.azure_openai_api_key,
                             api_version=s.azure_openai_api_version)
        kwargs = {"model": s.embedding_deployment_name, "input": ["nexus test"]}
        if "text-embedding-3" in s.embedding_deployment_name.lower():
            kwargs["dimensions"] = s.embedding_dimensions
        vector = client.embeddings.create(**kwargs).data[0].embedding
        record("Embedding call", True, s.embedding_deployment_name)
        matches = len(vector) == s.embedding_dimensions
        record("Dimensions match index", matches,
               f"model returned {len(vector)}, configured {s.embedding_dimensions}")
        if not matches:
            print("         Fix EMBEDDING_DIMENSIONS in .env, then recreate the index.")
    except Exception as exc:  # noqa: BLE001
        record("Embedding call", False, str(exc)[:160])


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Azure connectivity")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level)

    header("CONNECTION CHECK")
    check_env(args.verbose)
    check_blob(settings)
    check_table(settings)
    check_search(settings)
    check_chat(settings)
    check_embedding(settings)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    section("RESULT")
    print(f"  {passed} passed, {failed} failed")
    if failed:
        print("\n  Failing checks:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"    - {name}: {detail}")
        return 1
    print("\n  All checks passed. Ready to run the pipeline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
