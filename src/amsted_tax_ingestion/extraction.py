from abc import ABC,abstractmethod
from email import policy
from email.parser import BytesParser
from pathlib import Path
import csv,html
from bs4 import BeautifulSoup
from .models import ExtractedDocument
class Extractor(ABC):
    @abstractmethod
    def extract(self,path:Path)->ExtractedDocument: ...
class DoclingExtractor(Extractor):
    def __init__(self):
        from docling.document_converter import DocumentConverter
        self.converter=DocumentConverter()
    def extract(self,path):
        result=self.converter.convert(path)
        text=result.document.export_to_markdown()
        return ExtractedDocument(text=text,metadata={"extractor":"docling"})
class EmlExtractor(Extractor):
    def extract(self,path):
        msg=BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        body=[]
        parts=msg.walk() if msg.is_multipart() else [msg]
        for part in parts:
            if part.get_content_type()=="text/plain" and not part.get_filename():
                body.append(part.get_content())
        meta={k:str(msg.get(k,"")) for k in ["subject","from","to","cc","date"]}
        return ExtractedDocument(text="\n".join(body),metadata=meta|{"extractor":"email"})
class MsgExtractor(Extractor):
    def extract(self,path):
        import extract_msg
        msg=extract_msg.Message(str(path))
        meta={"subject":msg.subject or "","from":msg.sender or "","to":msg.to or "",
              "cc":msg.cc or "","date":str(msg.date or ""),"extractor":"extract-msg"}
        return ExtractedDocument(text=msg.body or "",metadata=meta)
class TextExtractor(Extractor):
    def extract(self,path):
        raw=path.read_text(encoding="utf-8",errors="replace")
        if path.suffix.lower() in {".html",".htm"}: raw=BeautifulSoup(raw,"html.parser").get_text("\n")
        return ExtractedDocument(text=raw,metadata={"extractor":"text"})
class CsvExtractor(Extractor):
    def extract(self,path):
        with path.open(encoding="utf-8-sig",errors="replace",newline="") as f:
            rows=list(csv.reader(f))
        return ExtractedDocument(text="\n".join(" | ".join(r) for r in rows),metadata={"extractor":"csv","rows":len(rows)})
class ExtractionRouter:
    DOCLING={".pdf",".docx",".pptx",".xlsx"}
    def extract(self,path:Path):
        ext=path.suffix.lower()
        if ext==".eml": return EmlExtractor().extract(path)
        if ext==".msg": return MsgExtractor().extract(path)
        if ext==".csv": return CsvExtractor().extract(path)
        if ext in {".txt",".html",".htm",".md"}: return TextExtractor().extract(path)
        if ext in self.DOCLING: return DoclingExtractor().extract(path)
        raise ValueError(f"Unsupported file type: {ext}")
