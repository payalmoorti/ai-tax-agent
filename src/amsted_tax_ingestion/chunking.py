import tiktoken
from .models import NormalizedDocument,Chunk
from .utils import stable_id
class TokenChunker:
    def __init__(self,size=800,overlap=120):
        if size<=0 or overlap<0 or overlap>=size: raise ValueError("Require size > overlap >= 0")
        self.size,self.overlap=size,overlap
        self.enc=tiktoken.get_encoding("cl100k_base")
    def chunk(self,doc:NormalizedDocument):
        tokens=self.enc.encode(doc.content); out=[]; start=0; number=1
        while start<len(tokens):
            text=self.enc.decode(tokens[start:start+self.size]).strip()
            if text:
                e=doc.enrichment
                out.append(Chunk(chunk_id=stable_id(doc.document_id,str(number),text,prefix="chk_"),document_id=doc.document_id,
                  chunk_number=number,content=text,source_file_name=doc.source_file_name,source_path=doc.source_path,
                  document_type=doc.document_type,jurisdiction=e.jurisdiction,source_id=e.source_id,topic=e.topic,
                  scenario_id=e.scenario_id,authority_level=e.authority_level,
                  citation=f"{doc.source_file_name}#chunk={number}"))
                number+=1
            if start+self.size>=len(tokens): break
            start+=self.size-self.overlap
        return out
