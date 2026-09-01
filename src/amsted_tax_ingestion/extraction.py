"""Extraction with a declarative router, cached Docling converter, and OCR enabled.

Key fixes vs. the previous version:
  * Docling converter is built ONCE per process (it was reloading models per file).
  * OCR and table structure are explicitly enabled — scanned PDFs returned empty before.
  * Email headers are rendered INTO the body text, not just stashed in metadata,
    so subject/from/to/date actually get embedded and are retrievable.
  * HTML-only emails no longer return empty content.
  * Routing is a declarative extension -> extractor map, not an if/elif chain.
  * is_supported() lets you filter before download instead of raising mid-run.
"""
from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from email import policy
from email.parser import BytesParser
from functools import lru_cache
from pathlib import Path
from .pdf_router import extract_pdf
from bs4 import BeautifulSoup
from .email_thread import build_thread
from .models import NA, ExtractedDocument


class ExtractionError(RuntimeError):
    """Raised when a document cannot be extracted."""


def _html_to_text(raw: str) -> str:
    return BeautifulSoup(raw, "html.parser").get_text("\n", strip=True)


def _build_email_document(headers: dict[str, str], body: str, extractor: str,
                          attachments: list[str]) -> ExtractedDocument:
    """Segment the thread into ordered messages at extraction time."""
    thread_subject, messages = build_thread(headers=headers, body=body or "")
    lines = [f"{k.title()}: {v}" for k, v in headers.items() if v]
    flat = "\n".join(lines) + "\n\n---\n\n" + (body or "").strip()
    return ExtractedDocument(
        text=flat,
        extractor=extractor,
        is_email_thread=bool(messages),
        thread_subject=thread_subject or NA,
        messages=messages,
        metadata={**headers, "attachments": attachments, "extractor": extractor,
                  "message_count": len(messages)},
    )


class Extractor(ABC):
    name: str = "base"
    extensions: tuple[str, ...] = ()

    @abstractmethod
    def extract(self, path: Path) -> ExtractedDocument: ...


# --------------------------------------------------------------------------- #
# Docling
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _docling_converter():
    """Expensive model load — build once and reuse for the whole run."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = True              # scanned PDFs were silently producing no text
    options.do_table_structure = True  # keep tax tables intact
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


class DoclingExtractor(Extractor):
    name = "docling"
    extensions = (".pdf", ".docx", ".pptx", ".xlsx", ".md", ".html", ".htm")

    from .pdf_router import extract_pdf   # add at top


class DoclingExtractor(Extractor):
    name = "docling"
    extensions = (".pdf", ".docx", ".pptx", ".xlsx", ".md", ".html", ".htm")

    def extract(self, path: Path) -> ExtractedDocument:
        # PDFs are routed by content: text layer -> pdfplumber, scanned or
        # table-heavy -> Docling. Everything else goes straight to Docling.
        if path.suffix.lower() == ".pdf":
            try:
                text, engine = extract_pdf(path)
            except Exception as exc:
                raise ExtractionError(f"PDF extraction failed on {path.name}: {exc}") from exc

            if not text.strip():
                raise ExtractionError(f"No text recovered from {path.name}")

            return ExtractedDocument(
                text=text,
                extractor=engine,          # "pdfplumber" or "docling"
                page_count=None,
                metadata={"extractor": engine, "format": "text",
                          "ocr_enabled": engine == "docling"},
            )

        # existing Docling path for docx / pptx / xlsx / html / md — unchanged
        ...

# --------------------------------------------------------------------------- #
# Email
# --------------------------------------------------------------------------- #

class EmlExtractor(Extractor):
    name = "email-eml"
    extensions = (".eml",)

    def extract(self, path: Path) -> ExtractedDocument:
        try:
            msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"Failed to parse .eml {path.name}: {exc}") from exc

        headers = {k: str(msg.get(k, "")) for k in ("subject", "from", "to", "cc", "date")}

        # get_body() handles multipart correctly and falls back to HTML,
        # which the previous text/plain-only walk did not.
        part = msg.get_body(preferencelist=("plain", "html"))
        body = part.get_content() if part is not None else ""
        if part is not None and part.get_content_type() == "text/html":
            body = _html_to_text(body)

        attachments = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]

        return _build_email_document(headers, body, self.name, attachments)


class MsgExtractor(Extractor):
    name = "extract-msg"
    extensions = (".msg",)

    def extract(self, path: Path) -> ExtractedDocument:
        import extract_msg

        try:
            msg = extract_msg.Message(str(path))
            headers = {
                "subject": msg.subject or "",
                "from": msg.sender or "",
                "to": msg.to or "",
                "cc": msg.cc or "",
                "date": str(msg.date or ""),
            }
            body = msg.body or ""
            if not body.strip() and msg.htmlBody:
                body = _html_to_text(msg.htmlBody.decode("utf-8", "ignore"))
            attachments = [a.longFilename or a.shortFilename or "" for a in (msg.attachments or [])]
            msg.close()
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"Failed to parse .msg {path.name}: {exc}") from exc

        return _build_email_document(headers, body, self.name, attachments)


# --------------------------------------------------------------------------- #
# Plain formats
# --------------------------------------------------------------------------- #

class TextExtractor(Extractor):
    name = "text"
    extensions = (".txt", ".log", ".json")

    def extract(self, path: Path) -> ExtractedDocument:
        raw = path.read_text(encoding="utf-8", errors="replace")
        return ExtractedDocument(
            text=raw,
            extractor=self.name,
            metadata={"extractor": self.name, "line_count": raw.count("\n") + 1},
        )


class CsvExtractor(Extractor):
    name = "csv"
    extensions = (".csv", ".tsv")

    def extract(self, path: Path) -> ExtractedDocument:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
            rows = list(csv.reader(fh, delimiter=delimiter))
        if not rows:
            raise ExtractionError(f"{path.name} contained no rows.")

        # Markdown table keeps the header associated with each value for retrieval.
        header, *body = rows
        lines = [" | ".join(header), " | ".join("---" for _ in header)]
        lines += [" | ".join(r) for r in body]
        return ExtractedDocument(
            text="\n".join(lines),
            extractor=self.name,
            metadata={"extractor": self.name, "rows": len(rows), "columns": len(header)},
        )


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #

_EXTRACTORS: tuple[Extractor, ...] = (
    MsgExtractor(),
    EmlExtractor(),
    CsvExtractor(),
    TextExtractor(),
    DoclingExtractor(),
)

# Declarative extension -> extractor map. Add a format by adding an extractor above.
_ROUTES: dict[str, Extractor] = {
    ext: extractor for extractor in _EXTRACTORS for ext in extractor.extensions
}

SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(sorted(_ROUTES))

_TYPE_LABELS = {
    ".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx",
    ".msg": "email", ".eml": "email",
    ".html": "html", ".htm": "html", ".md": "markdown",
    ".txt": "text", ".log": "text", ".json": "json",
    ".csv": "csv", ".tsv": "csv",
}


def is_supported(file_name: str | Path) -> bool:
    return Path(file_name).suffix.lower() in _ROUTES


def detect_document_type(file_name: str | Path) -> str:
    """Semantic type, not just the extension — .msg and .eml both become 'email'."""
    return _TYPE_LABELS.get(Path(file_name).suffix.lower(), "unknown")


class ExtractionRouter:
    def extractor_for(self, path: Path) -> Extractor:
        extractor = _ROUTES.get(path.suffix.lower())
        if extractor is None:
            raise ExtractionError(
                f"Unsupported file type '{path.suffix}'. "
                f"Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
            )
        return extractor

    def extract(self, path: Path) -> ExtractedDocument:
        return self.extractor_for(path).extract(path)
