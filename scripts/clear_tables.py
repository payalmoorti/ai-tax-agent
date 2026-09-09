"""Delete all rows from the tracking and metadata tables.

The tables themselves are preserved. Table Storage has no schema, so the next
upsert recreates rows with the same shape.

    python scripts/clear_tables.py --yes
    python scripts/clear_tables.py --table status --yes
    python scripts/clear_tables.py --document-id doc_9eaf947f --yes
    python scripts/clear_tables.py --status FAILED --yes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli import header, kv, section  # noqa: E402

from azure.core.exceptions import ResourceNotFoundError  # noqa: E402
from azure.data.tables import TableServiceClient  # noqa: E402

from amsted_tax_ingestion.config import Settings  # noqa: E402
from amsted_tax_ingestion.utils import configure_logging  # noqa: E402

BATCH_SIZE = 100


def clear(service, table_name: str, query: str | None) -> int:
    """Delete matching rows. Returns the count."""
    table = service.get_table_client(table_name)
    try:
        rows = list(table.query_entities(query) if query else table.list_entities())
    except ResourceNotFoundError:
        print(f"  {table_name}: does not exist, skipping")
        return 0

    if not rows:
        print(f"  {table_name}: already empty")
        return 0

    # A transaction cannot span partitions, so group first.
    by_partition: dict[str, list] = {}
    for row in rows:
        by_partition.setdefault(row["PartitionKey"], []).append(row)

    deleted = 0
    for partition_rows in by_partition.values():
        for start in range(0, len(partition_rows), BATCH_SIZE):
            batch = [("delete", r) for r in partition_rows[start : start + BATCH_SIZE]]
            table.submit_transaction(batch)
            deleted += len(batch)

    print(f"  {table_name}: deleted {deleted} row(s)")
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear ingestion tables")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    parser.add_argument("--table", choices=["status", "metadata", "both"],
                        default="both")
    parser.add_argument("--document-id", help="Clear one document from both tables")
    parser.add_argument("--status", help="Status table only, e.g. FAILED")
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level)

    status_table = settings.azure_table_name
    metadata_table = getattr(settings, "azure_metadata_table_name", "TaxAgentMetadata")

    targets = []
    if args.table in ("status", "both"):
        targets.append(status_table)
    if args.table in ("metadata", "both"):
        targets.append(metadata_table)

    header("CLEAR TABLES")
    kv("Tables", ", ".join(targets))
    kv("Filter", args.document_id or args.status or "(all rows)")

    if not args.yes:
        print(f"\nThis deletes rows from: {', '.join(targets)}")
        print("The tables themselves are preserved.")
        if input("Type CLEAR to confirm: ").strip() != "CLEAR":
            print("Aborted.")
            return 1

    service = TableServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    )

    section("DELETING")
    total = 0
    for name in targets:
        # --status only applies to the status table; metadata has no such column.
        if args.document_id:
            query = f"RowKey eq '{args.document_id}'"
        elif args.status and name == status_table:
            query = f"ProcessingStatus eq '{args.status}'"
        elif args.status:
            continue
        else:
            query = None
        total += clear(service, name, query)

    section("RESULT")
    kv("Rows deleted", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())