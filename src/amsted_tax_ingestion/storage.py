from datetime import datetime,timezone
from pathlib import Path
import json
from azure.storage.blob import ContainerClient
from azure.data.tables import TableServiceClient, UpdateMode
class BlobSource:
    def __init__(self,connection_string,container):
        self.client=ContainerClient.from_connection_string(connection_string,container)
    def list(self,prefix=""):
        for blob in self.client.list_blobs(name_starts_with=prefix):
            yield blob
    def download(self,name:str,target_dir:Path)->Path:
        path=target_dir/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(self.client.download_blob(name).readall())
        return path
class StatusStore:
    def __init__(self,connection_string,table_name):
        service=TableServiceClient.from_connection_string(connection_string)
        self.table=service.create_table_if_not_exists(table_name)
    def update(self,document_id,file_name,blob_path,document_type,status,chunk_count=0,error=""):
        now=datetime.now(timezone.utc).isoformat()
        old=None
        try: old=self.table.get_entity("document",document_id)
        except Exception: pass
        entity={"PartitionKey":"document","RowKey":document_id,"DocumentId":document_id,
          "FileName":file_name,"BlobPath":blob_path,"DocumentType":document_type,
          "ProcessingStatus":status,"CreatedDate":old.get("CreatedDate",now) if old else now,
          "LastUpdatedDate":now,"ChunkCount":chunk_count,"Error":error[:1000]}
        self.table.upsert_entity(entity,mode=UpdateMode.MERGE)

class MetadataStore:
    """Store business metadata for documents in a separate Azure Table.

    Writes a small set of flattened columns for quick faceting/filters plus a
    `MetadataJson` column that contains the full metadata payload as JSON.
    """
    def __init__(self, connection_string, table_name):
        service = TableServiceClient.from_connection_string(connection_string)
        self.table = service.create_table_if_not_exists(table_name)

    def upsert(self, document_id: str, metadata: dict):
        now = datetime.now(timezone.utc).isoformat()
        old = None
        try:
            old = self.table.get_entity("document", document_id)
        except Exception:
            old = None

        # Flatten a few common fields for easy filtering in the table; keep
        # the full metadata as JSON as well (truncated to table size limits).
        entity = {
            "PartitionKey": "document",
            "RowKey": document_id,
            "DocumentId": document_id,
            "CreatedDate": old.get("CreatedDate", now) if old else now,
            "LastUpdatedDate": now,
            "Jurisdiction": (metadata or {}).get("jurisdiction") or (metadata or {}).get("Jurisdiction") or "n/a",
            "TaxTopic": (metadata or {}).get("tax_topic") or (metadata or {}).get("TaxTopic") or "n/a",
            "ScenarioId": (metadata or {}).get("scenario_id") or "n/a",
            "SourceType": (metadata or {}).get("source_type") or "n/a",
            # Store the full metadata as JSON (truncate to 32k to be safe)
            "MetadataJson": json.dumps(metadata or {})[:32000],
        }

        self.table.upsert_entity(entity, mode=UpdateMode.MERGE)
