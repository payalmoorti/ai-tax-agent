"""End-to-end ingestion orchestration.

Blob Storage -> Extraction -> Normalization -> Enrichment -> Chunking
             -> Embeddings -> Azure AI Search

Design points that differ from the previous version:

  * document_id is computed from the blob path BEFORE any work starts, so a
    status row can always be written even if download or extraction fails.
  * Status is recorded at every stage, not only on success, so a crashed run
    still shows how far each document got.
  * skip_enrichment is wired through to the chunker's enrichment guard instead
    of colliding with it.
  * on_enrichment_failure chooses whether a failed classification blocks the
    document or indexes it with n/a metadata.
  * Embedding artifacts store dimensions and a short preview, not full vectors.
  * Optional scenario manifest supplies corpus-assigned scenario_id values.
  * content_hash enables skipping documents that have not changed.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .chunking import Chunker, EnrichmentMissingError
from .embeddings import EmbeddingGenerator
from .enrichment import MetadataEnricher
from .extraction import ExtractionRouter, is_supported
from .indexing import upload
from .models import NA
from .normalization import normalize
from .storage import BlobSource, StatusStore, MetadataStore
from .utils import safe_name, stable_id, write_json

log = logging.getLogger(__name__)

# Status values written to Table Storage.
DISCOVERED = "DISCOVERED"
DOWNLOADED = "DOWNLOADED"
EXTRACTED = "EXTRACTED"
NORMALIZED = "NORMALIZED"
ENRICHED = "ENRICHED"
ENRICH_FAILED = "ENRICH_FAILED"
CHUNKED = "CHUNKED"
EMBEDDED = "EMBEDDED"
INDEXED = "INDEXED"
DRY_RUN = "DRY_RUN"
SKIPPED = "SKIPPED"
UNCHANGED = "UNCHANGED"
FAILED = "FAILED"


@dataclass
class RunResult:
    discovered: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    unchanged: int = 0
    chunks_created: int = 0
    chunks_indexed: int = 0
    enrichment_failures: int = 0
    failures: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"discovered={self.discovered} succeeded={self.succeeded} "
            f"failed={self.failed} skipped={self.skipped} unchanged={self.unchanged} "
            f"chunks={self.chunks_created} indexed={self.chunks_indexed} "
            f"enrichment_failures={self.enrichment_failures}"
        )


class Pipeline:
    def __init__(
        self,
        settings,
        *,
        on_enrichment_failure: Literal["fail", "index_anyway"] = "index_anyway",
    ):
        self.s = settings
        self.on_enrichment_failure = on_enrichment_failure
        self._scenarios: dict[str, list[str]] | None = None

    # ---------------- scenario manifest ----------------

    def _load_scenarios(self) -> dict[str, list[str]]:
        """Optional manifest mapping blob name -> corpus-assigned scenario IDs.

        scenarios.json:
            {"memos/nexus_il.pdf": ["SCN-014", "SCN-022"]}
        """
        if self._scenarios is not None:
            return self._scenarios

        path = getattr(self.s, "scenario_manifest_path", None)
        self._scenarios = {}
        if path and Path(path).exists():
            try:
                raw = json.loads(Path(path).read_text(encoding="utf-8"))
                self._scenarios = {
                    k: ([v] if isinstance(v, str) else list(v)) for k, v in raw.items()
                }
                log.info("Loaded scenario manifest with %s entries.", len(self._scenarios))
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not read scenario manifest %s: %s", path, exc)
        return self._scenarios

    def _scenarios_for(self, blob_name: str) -> list[str]:
        manifest = self._load_scenarios()
        return manifest.get(blob_name) or manifest.get(Path(blob_name).name) or []

    # ---------------- helpers ----------------

    def _status(self, store, *, document_id, file_name, blob_name,
                document_type, state, chunk_count=0, error=""):
        if store is None:
            return
        try:
            store.update(
                document_id, file_name, blob_name, document_type,
                state, chunk_count, error=error,
            )
        except TypeError:
            # Tolerate a StatusStore.update() with a narrower signature.
            store.update(document_id, file_name, blob_name, document_type, state, chunk_count)
        except Exception as exc:  # noqa: BLE001 — tracking must never break ingestion
            log.warning("Status update failed for %s: %s", blob_name, exc)

    def _embedding_artifact(self, chunks) -> list[dict]:
        """Full vectors would be hundreds of MB. Store shape + a short preview."""
        return [
            {
                "chunk_id": c.chunk_id,
                "message_index": c.message_index,
                "token_count": c.token_count,
                "vector_dimensions": len(c.content_vector or []),
                "vector_preview": (c.content_vector or [])[:8],
            }
            for c in chunks
        ]

    # ---------------- single document ----------------

    def process_blob(self, blob, source, status, metadata, out: Path, *,
                     dry_run: bool, skip_enrichment: bool,
                     force: bool, result: RunResult) -> None:
        blob_name = blob.name
        source_path = f"{self.s.azure_blob_container_name}/{blob_name}"
        # Computed up front so a status row exists no matter where we fail.
        document_id = stable_id(source_path, prefix="doc_")
        file_name = Path(blob_name).name
        document_type = Path(blob_name).suffix.lower().lstrip(".")

        def mark(state, chunk_count=0, error=""):
            self._status(
                status, document_id=document_id, file_name=file_name,
                blob_name=blob_name, document_type=document_type,
                state=state, chunk_count=chunk_count, error=error,
            )
        if not is_supported(blob_name):
            log.info("Skipping unsupported file: %s", blob_name)
            result.skipped += 1
            mark(SKIPPED, error="Unsupported file type")
            return

        local = None
        try:
            local = source.download(blob_name, out / "raw")
            mark(DOWNLOADED)

            extracted = ExtractionRouter().extract(local)
            write_json(out / "extracted" / f"{document_id}.json", extracted)
            mark(EXTRACTED)

            doc = normalize(
                local, source_path, extracted,
                str(getattr(blob, "last_modified", "")),
                scenario_ids=self._scenarios_for(blob_name),
            )
            write_json(out / "normalized" / f"{doc.document_id}.json", doc)
            mark(NORMALIZED)

            # Skip unchanged documents unless forced.
            if not force and self._is_unchanged(out, doc):
                log.info("Unchanged since last run, skipping: %s", file_name)
                result.unchanged += 1
                mark(UNCHANGED)
                return

            # --- enrichment ---
            if skip_enrichment:
                doc.metadata.enrichment_status = "skipped"
                log.info("Enrichment skipped by flag for %s", file_name)
            else:
                doc = MetadataEnricher(self.s).enrich(doc)
                if doc.metadata.enrichment_status == "ok":
                    mark(ENRICHED)
                else:
                    result.enrichment_failures += 1
                    mark(ENRICH_FAILED, error=doc.metadata.enrichment_rationale)
                    if self.on_enrichment_failure == "fail":
                        raise RuntimeError(
                            f"Enrichment failed: {doc.metadata.enrichment_rationale}"
                        )
                    log.warning(
                        "Indexing %s with n/a metadata (enrichment failed).", file_name
                    )

            write_json(out / "enriched" / f"{doc.document_id}.json", doc)

            # The guard is bypassed only when we have deliberately chosen to
            # proceed without enrichment.
            require = not skip_enrichment and self.on_enrichment_failure == "fail"
            chunker = Chunker(
                self.s.chunk_size_tokens,
                self.s.chunk_overlap_tokens,
                require_enrichment=require,
            )
            chunks = chunker.chunk(doc)
            if not chunks:
                raise RuntimeError("Chunking produced no chunks.")

            write_json(
                out / "chunked" / f"{doc.document_id}.json",
                [c.model_dump(mode="json") for c in chunks],
            )
            # Write business metadata to the optional metadata table.
            if metadata is not None:
                try:
                    metadata.upsert(document_id, doc.metadata.model_dump())
                except Exception as exc:  # noqa: BLE001
                    log.warning("Metadata upsert failed for %s: %s", blob_name, exc)
            result.chunks_created += len(chunks)
            mark(CHUNKED, chunk_count=len(chunks))

            if dry_run:
                result.succeeded += 1
                mark(DRY_RUN, chunk_count=len(chunks))
                return

            chunks = EmbeddingGenerator(self.s).apply(chunks)
            write_json(
                out / "embeddings" / f"{doc.document_id}.json",
                self._embedding_artifact(chunks),
            )
            mark(EMBEDDED, chunk_count=len(chunks))

            upload(self.s, chunks)
            result.chunks_indexed += len(chunks)
            result.succeeded += 1
            mark(INDEXED, chunk_count=len(chunks))
            log.info("Indexed %s (%s chunks)", file_name, len(chunks))

        except EnrichmentMissingError as exc:
            log.error("%s: %s", file_name, exc)
            result.failed += 1
            result.failures.append({"blob": blob_name, "error": str(exc)})
            write_json(
                out / "indexing_logs" / f"{safe_name(blob_name)}.error.json",
                {"blob": blob_name, "document_id": document_id, "error": str(exc)},
            )
            mark(FAILED, error=str(exc))

        except Exception as exc:  # noqa: BLE001 — one bad file must not stop the run
            log.exception("Failed blob %s", blob_name)
            result.failed += 1
            result.failures.append({"blob": blob_name, "error": str(exc)})
            write_json(
                out / "indexing_logs" / f"{safe_name(blob_name)}.error.json",
                {"blob": blob_name, "document_id": document_id,
                 "local_file": str(local) if local else None, "error": str(exc)},
            )
            # Always writes, because document_id never depends on work that failed.
            mark(FAILED, error=str(exc))

    # ---------------- idempotency ----------------

    def _is_unchanged(self, out: Path, doc) -> bool:
        previous = out / "normalized" / f"{doc.document_id}.json"
        marker = out / "indexing_logs" / f"{doc.document_id}.indexed.json"
        if not marker.exists():
            return False
        try:
            return json.loads(marker.read_text(encoding="utf-8")).get(
                "content_hash"
            ) == doc.content_hash
        except Exception:  # noqa: BLE001
            return False

    # ---------------- run ----------------
         # Verify the search index BEFORE spending money on embeddings.

        

    def run(
        self,
        prefix: str = "",
        dry_run: bool = False,
        skip_enrichment: bool = False,
        limit: int | None = None,
        force: bool = False,
    ) -> RunResult:
        s = self.s
        s.require("azure_storage_connection_string", "azure_blob_container_name")
        out = s.output_dir

        source = BlobSource(s.azure_storage_connection_string, s.azure_blob_container_name)
        status = None
        if s.azure_storage_connection_string:
            try:
                status = StatusStore(s.azure_storage_connection_string, s.azure_table_name)
            except Exception as exc:  # noqa: BLE001
                log.warning("Status tracking disabled: %s", exc)
        metadata = None
        # Optional separate metadata table; set `azure_metadata_table_name` in settings
        if s.azure_storage_connection_string and getattr(s, "azure_metadata_table_name", None):
            try:
                metadata = MetadataStore(s.azure_storage_connection_string, s.azure_metadata_table_name)
            except Exception as exc:  # noqa: BLE001
                log.warning("Metadata tracking disabled: %s", exc)

        if not dry_run:
            from azure.core.exceptions import ResourceNotFoundError
            from .indexing import clients

            index_client, _ = clients(s)
            try:
                index = index_client.get_index(s.azure_search_index_name)
            except ResourceNotFoundError:
                raise RuntimeError(
                    f"Search index '{s.azure_search_index_name}' does not exist.\n"
                    f"Run: python scripts/create_search_index.py"
                ) from None

            vector_field = next(
                (f for f in index.fields if f.name == "content_vector"), None
            )
            if vector_field and vector_field.vector_search_dimensions != s.embedding_dimensions:
                raise RuntimeError(
                    f"Dimension mismatch: index has "
                    f"{vector_field.vector_search_dimensions}, .env has "
                    f"{s.embedding_dimensions}.\n"
                    f"Fix .env, then run: python scripts/recreate_search_index.py --yes"
                )

        result = RunResult()
        blobs = list(source.list(prefix))
        if limit:
            blobs = blobs[:limit]
        result.discovered = len(blobs)
        log.info("Discovered %s blob(s).", result.discovered)

        for blob in blobs:
            log.info("--- %s ---", blob.name)
            self.process_blob(
                blob, source, status, metadata, out,
                dry_run=dry_run, skip_enrichment=skip_enrichment,
                force=force, result=result,
            )

        write_json(out / "indexing_logs" / "_run_summary.json", {
            "discovered": result.discovered,
            "succeeded": result.succeeded,
            "failed": result.failed,
            "skipped": result.skipped,
            "unchanged": result.unchanged,
            "chunks_created": result.chunks_created,
            "chunks_indexed": result.chunks_indexed,
            "enrichment_failures": result.enrichment_failures,
            "failures": result.failures,
            "dry_run": dry_run,
            "skip_enrichment": skip_enrichment,
        })
        log.info("Run complete: %s", result.summary())
        
        return result
