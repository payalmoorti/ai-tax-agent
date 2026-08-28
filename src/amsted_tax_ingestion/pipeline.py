import logging
from pathlib import Path
from .storage import BlobSource,StatusStore
from .extraction import ExtractionRouter
from .normalization import normalize
from .enrichment import MetadataEnricher
from .chunking import TokenChunker
from .embeddings import EmbeddingGenerator
from .indexing import upload
from .utils import write_json,safe_name
log=logging.getLogger(__name__)
class Pipeline:
    def __init__(self,s): self.s=s
    def run(self,prefix="",dry_run=False,skip_enrichment=False):
        s=self.s; s.require("azure_storage_connection_string","azure_blob_container_name")
        out=s.output_dir; source=BlobSource(s.azure_storage_connection_string,s.azure_blob_container_name)
        status=None
        if s.azure_storage_connection_string:
            status=StatusStore(s.azure_storage_connection_string,s.azure_table_name)
        for blob in source.list(prefix):
            local=None; doc=None
            try:
                local=source.download(blob.name,out/"raw")
                extracted=ExtractionRouter().extract(local)
                write_json(out/"extracted"/(safe_name(blob.name)+".json"),extracted)
                doc=normalize(local,blob.name,extracted,str(getattr(blob,"last_modified","")))
                write_json(out/"normalized"/(doc.document_id+".json"),doc)
                if not skip_enrichment: doc=MetadataEnricher(s).enrich(doc)
                write_json(out/"enriched"/(doc.document_id+".json"),doc)
                chunks=TokenChunker(s.chunk_size_tokens,s.chunk_overlap_tokens).chunk(doc)
                write_json(out/"chunked"/(doc.document_id+".json"),[c.model_dump(mode="json") for c in chunks])
                if not dry_run:
                    chunks=EmbeddingGenerator(s).apply(chunks)
                    write_json(out/"embeddings"/(doc.document_id+".json"),[c.model_dump(mode="json") for c in chunks])
                    upload(s,chunks)
                if status: status.update(doc.document_id,local.name,blob.name,doc.document_type,"DRY_RUN" if dry_run else "INDEXED",len(chunks))
            except Exception as e:
                log.exception("Failed blob %s",blob.name)
                write_json(out/"indexing_logs"/(safe_name(blob.name)+".error.json"),{"blob":blob.name,"error":str(e)})
                if status and doc: status.update(doc.document_id,local.name,blob.name,doc.document_type,"FAILED",error=str(e))
