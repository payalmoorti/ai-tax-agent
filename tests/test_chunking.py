from amsted_tax_ingestion.chunking import TokenChunker
from amsted_tax_ingestion.models import NormalizedDocument,EnrichmentMetadata
def test_deterministic_ids():
 d=NormalizedDocument(document_id="d",source_file_name="a.txt",source_path="a.txt",document_type="txt",content="hello world "*100,enrichment=EnrichmentMetadata(source_id="s"))
 c=TokenChunker(20,5); assert [x.chunk_id for x in c.chunk(d)]==[x.chunk_id for x in c.chunk(d)]
