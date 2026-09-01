"""PDF extraction routing: text layer first, Docling only when it earns its place.

Rationale
---------
Docling parses an Outlook header block as a TABLE and serializes it column-wise,
producing:

    From:  Sent:  To:  Cc:  Subject:
    Franson, Marilyn  Wed, Aug 2 6:06 PM  Lopez, Tristan  ...

That transposition is what broke sender/date/recipient recovery. pdfplumber and
PyMuPDF read the same PDF as ordinary lines:

    From: Franson, Marilyn <MDF@amsted.com>
    Sent: Wednesday, August 2, 2023 6:06 PM

which is the format the header regex was written for.

Docling is still the right tool for scanned PDFs (OCR) and for tax memos with
genuine data tables. So route on content, not on file extension:

    no text layer          -> Docling (OCR)
    text layer + email     -> pdfplumber (clean header lines)
    text layer + tables    -> Docling (table structure matters)
    text layer, otherwise  -> pdfplumber, Docling on failure

Drop this in as src/amsted_tax_ingestion/pdf_router.py and call
`extract_pdf(path)` from your PdfExtractor.
"""
from __future__ import annotations

import logging
import re

from pathlib import Path

log = logging.getLogger(__name__)

# Minimum characters of extractable text before we trust the text layer.
TEXT_LAYER_MIN_CHARS = 100

# Header patterns that mark a PDF as an email export.
_EMAIL_SIGNAL = re.compile(
    r"(?im)^[>\-\*\#\|\s_]*(?:"
    r"from[\*_\s]*:"
    r"|sent[\*_\s]*:"
    r"|-{2,}\s*original\s+message"
    r"|-{2,}\s*forwarded\s+message"
    r"|on\s+.{4,80}\s+wrote\s*:"
    r")"
)


def text_layer_chars(path: Path) -> int:
    """Characters recoverable without OCR. Zero means a scanned document."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return 0
    try:
        with fitz.open(str(path)) as document:
            return sum(len(page.get_text().strip()) for page in document)
    except Exception as exc:  # noqa: BLE001
        log.debug("Text-layer probe failed on %s: %s", path.name, exc)
        return 0


def extract_text_layer(path: Path) -> str:
    """pdfplumber first (best line reconstruction), PyMuPDF as a fallback."""
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        text = "\n\n".join(pages).strip()
        if text:
            return text
    except ImportError:
        log.debug("pdfplumber unavailable; falling back to PyMuPDF.")
    except Exception as exc:  # noqa: BLE001
        log.debug("pdfplumber failed on %s: %s", path.name, exc)

    import fitz

    with fitz.open(str(path)) as document:
        return "\n\n".join(page.get_text() for page in document).strip()


def looks_like_email(text: str, *, min_signals: int = 2) -> bool:
    """Two or more header signals: treat as an email export."""
    return len(_EMAIL_SIGNAL.findall(text or "")) >= min_signals


def has_data_tables(path: Path, *, min_rows: int = 3) -> bool:
    """True when the PDF contains real tabular data worth Docling's structure."""
    try:
        import pdfplumber
    except ImportError:
        return False
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages[:5]:
                for table in page.find_tables():
                    if len(table.rows) >= min_rows and len(table.columns) >= 2:
                        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("Table probe failed on %s: %s", path.name, exc)
    return False


def extract_with_docling(path: Path) -> str:
    """Docling with OCR and table structure enabled. Converter is cached upstream."""
    from .extraction import _docling_converter  # reuse the cached converter

    result = _docling_converter().convert(str(path))
    return result.document.export_to_markdown()


def choose_extractor(path: Path) -> tuple[str, str]:
    """Return (engine, reason). Engine is 'docling' or 'text'."""
    chars = text_layer_chars(path)

    if chars < TEXT_LAYER_MIN_CHARS:
        return "docling", f"no usable text layer ({chars} chars) — OCR required"

    sample = extract_text_layer(path)[:8000]

    if looks_like_email(sample):
        return "text", "email export — text layer preserves header lines"

    if has_data_tables(path):
        return "docling", "contains data tables — structure matters"

    return "text", "text layer present, no tables"


def extract_pdf(path: Path) -> tuple[str, str]:
    """Extract a PDF using the appropriate engine.

    Returns (text, engine_used) so the engine can be recorded in
    extraction_metadata for auditing.
    """
    engine, reason = choose_extractor(path)
    log.info("PDF %s -> %s (%s)", path.name, engine, reason)

    if engine == "text":
        try:
            text = extract_text_layer(path)
            if text.strip():
                return text, "pdfplumber"
            log.warning("Text layer empty for %s; falling back to Docling.", path.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("Text extraction failed for %s (%s); using Docling.",
                        path.name, exc)

    return extract_with_docling(path), "docling"
