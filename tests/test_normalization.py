from amsted_tax_ingestion.models import ExtractedDocument
from amsted_tax_ingestion.normalization import normalize
def test_normalize(tmp_path):
 p=tmp_path/"a.txt";p.write_text("x");d=normalize(p,"folder/a.txt",ExtractedDocument(text="x"));assert d.content=="x" and d.document_id.startswith("doc_")
