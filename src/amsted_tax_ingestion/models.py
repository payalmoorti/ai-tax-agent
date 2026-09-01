"""Data contracts for the Amsted ingestion pipeline.

Implements the Seed Corpus v0.1 metadata model. Every field in the action plan
travels with the document, the message, and the chunk.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

NA = "n/a"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Jurisdiction
# --------------------------------------------------------------------------- #

US_JURISDICTIONS = [
    "Federal", "Multistate",
    "Illinois", "Indiana", "Ohio", "Michigan", "Wisconsin", "Missouri",
    "Texas", "California", "New York", "Pennsylvania", "Tennessee",
    "Georgia", "North Carolina", "South Carolina", "Kentucky", "Alabama",
]

# Amsted operates globally; these appear across the tax corpus.
INTERNATIONAL_JURISDICTIONS = [
    "Canada", "Mexico", "Brazil", "Argentina",
    "United Kingdom", "Ireland", "Netherlands", "Belgium", "Germany",
    "France", "Italy", "Spain", "Poland", "Czech Republic", "Switzerland",
    "Sweden", "China", "India", "Japan", "South Korea", "Australia",
    "OECD", "European Union",
]

# "Unknown" is deliberately absent: unclassified is represented by NA ("n/a")
# everywhere, so facets do not split across two synonymous values.
JURISDICTIONS = (
    US_JURISDICTIONS
    + INTERNATIONAL_JURISDICTIONS
    + ["Multinational", "International"]
)

# --------------------------------------------------------------------------- #
# Tax topic
# --------------------------------------------------------------------------- #

TAX_TOPICS = [
    # State and local
    "Nexus",
    "Apportionment",
    "State Income Tax",
    "Sales and Use Tax",
    "Property Tax",
    "Unclaimed Property",

    # Federal
    "Federal Income Tax",
    "Tax Depreciation",
    "Tax Credits and Incentives",
    "Research and Development Credit",
    "Interest Deductibility",
    "Net Operating Losses",

    # International
    "Transfer Pricing",
    "Foreign Tax Credit",
    "Permanent Establishment",
    "Subpart F and GILTI",
    "Withholding",
    "Treaty Benefits",
    "Customs and Duties",
    "VAT and GST",
    "Pillar Two",

    # Compensation and payroll — the gap this corpus exposed
    "Equity Compensation",
    "Employment Tax",
    "Payroll Tax",
    "Expatriate and Mobility",
    "Employee Benefits",

    # Entity and transactional
    "Entity Structuring",
    "Mergers and Acquisitions",
    "Restructuring",
    "Intercompany Financing",

    # Reporting and controversy
    "Tax Provision",
    "Tax Compliance",
    "Audit Defense",
    "Indirect Tax",

    "Other",
]

# --------------------------------------------------------------------------- #
# Authority level — unchanged, ordered most to least authoritative
# --------------------------------------------------------------------------- #

AUTHORITY_LEVELS = [
    "Statute",
    "Regulation",
    "IRS Publication",
    "Federal Guidance",
    "State Guidance",
    "Foreign Tax Authority Guidance",
    "Treaty",
    "Case Law",
    "Legal Opinion",
    "External Tax Research",
    "Internal Tax Research",
    "Internal Memo",
    "Internal Email",
]

# --------------------------------------------------------------------------- #
# Source type — unchanged
# --------------------------------------------------------------------------- #

SOURCE_TYPES = [
    "Email Thread", "Email Message", "Tax Memo", "Tax Research", "Working Paper",
    "Statute", "Regulation", "Agency Guidance", "Court Decision", "Ruling",
    "Financial Statement", "Spreadsheet", "Presentation", "Other",
]

# --------------------------------------------------------------------------- #
# Governance — still deferred, awaiting Amsted's classification scheme
# --------------------------------------------------------------------------- #

VALIDITY_STATUSES = ["Valid", "Under Review", "Invalid", NA]
CURRENT_OR_SUPERSEDED = ["Current", "Superseded", NA]
ACCESS_CLASSIFICATIONS = ["Public", "Internal", "Confidential", "Restricted", NA]


# --------------------------------------------------------------------------- #
# Coercion aliases
# --------------------------------------------------------------------------- #

# Common model outputs that should map onto a canonical value rather than
# falling back to "Other". Checked before the fuzzy substring match.
JURISDICTION_ALIASES = {
    "the netherlands": "Netherlands",
    "holland": "Netherlands",
    "nl": "Netherlands",
    "uk": "United Kingdom",
    "great britain": "United Kingdom",
    "usa": "Federal",
    "united states": "Federal",
    "us federal": "Federal",
    "prc": "China",
    "people's republic of china": "China",
    "korea": "South Korea",
    "eu": "European Union",
}

TOPIC_ALIASES = {
    "sars": "Equity Compensation",
    "stock appreciation rights": "Equity Compensation",
    "stock options": "Equity Compensation",
    "share based payment": "Equity Compensation",
    "share-based compensation": "Equity Compensation",
    "rsu": "Equity Compensation",
    "restricted stock": "Equity Compensation",
    "deferred compensation": "Equity Compensation",
    "social security": "Employment Tax",
    "social insurance": "Employment Tax",
    "payroll": "Payroll Tax",
    "expat": "Expatriate and Mobility",
    "global mobility": "Expatriate and Mobility",
    "gilti": "Subpart F and GILTI",
    "subpart f": "Subpart F and GILTI",
    "beat": "Subpart F and GILTI",
    "vat": "VAT and GST",
    "gst": "VAT and GST",
    "r&d credit": "Research and Development Credit",
    "163(j)": "Interest Deductibility",
    "nol": "Net Operating Losses",
    "pe": "Permanent Establishment",
    "m&a": "Mergers and Acquisitions",
}


def coerce(value, allowed: list[str], aliases: dict | None = None,
           fallback: str = NA) -> str:
    """Snap a model answer onto the controlled vocabulary.

    Order: exact match -> alias -> substring -> fallback.
    """
    if not value:
        return fallback
    text = str(value).strip()
    if text.lower() in {"n/a", "na", "none", "unknown", "null", ""}:
        return fallback

    lookup = {a.lower(): a for a in allowed}
    if text.lower() in lookup:
        return lookup[text.lower()]

    if aliases:
        for alias, canonical in aliases.items():
            if alias in text.lower():
                return canonical

    for candidate in allowed:
        if candidate.lower() in text.lower() or text.lower() in candidate.lower():
            return candidate

    return fallback

# --------------------------------------------------------------------------- #
# Metadata model (Seed Corpus v0.1)
# --------------------------------------------------------------------------- #

class DocumentMetadata(BaseModel):
    """The metadata contract that travels with every document, message, and chunk.

    Field groups:
      * Identity / provenance : scenario_id, source_id, source_file, source_type,
                                source_date, author, page_number
      * Tax classification    : tax_topic, jurisdiction, tax_year_or_effective_period,
                                authority_level
      * Organizational        : legal_entity, business_unit
      * Email-specific        : thread_subject, message_date
      * Governance (deferred) : validity_status, current_or_superseded,
                                access_classification
    """

    # --- Identity / provenance ---
    scenario_id: str = NA          # may be multi-valued; see scenario_ids
    source_id: str = NA
    source_file: str = NA
    source_type: str = NA
    source_date: str = NA          # ISO 8601 where known
    author: str = NA
    page_number: int | None = None

    # Scenario IDs can be multi-valued per the action plan ("Scenario ID(s)").
    # scenario_id above holds the primary value for simple filtering.
    scenario_ids: list[str] = Field(default_factory=list)

    # --- Tax classification ---
    tax_topic: str = NA
    jurisdiction: str = NA
    tax_year_or_effective_period: str = NA
    authority_level: str = NA

    # --- Organizational ---
    legal_entity: str = NA
    business_unit: str = NA

    # --- Email-specific ---
    thread_subject: str = NA
    message_date: str = NA

    # --- Governance: deferred, always n/a until Amsted supplies classifications ---
    validity_status: str = NA
    current_or_superseded: str = NA
    access_classification: str = NA

    # --- Enrichment provenance (not part of the business schema) ---
    enrichment_status: Literal["pending", "ok", "failed", "skipped"] = "pending"
    enrichment_confidence: float = 0.0
    enrichment_rationale: str = ""
    enrichment_model: str = ""
    enrichment_timestamp: str = ""

    @property
    def is_enriched(self) -> bool:
        return self.enrichment_status == "ok"

    def merge(self, **overrides) -> "DocumentMetadata":
        """Return a copy with non-empty overrides applied. Used to layer
        message-level metadata over document-level metadata."""
        clean = {k: v for k, v in overrides.items() if v not in (None, "", [], NA)}
        return self.model_copy(update=clean)


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

class EmailMessage(BaseModel):
    """A single message parsed out of an email thread."""

    message_index: int              # order within the thread, 1 = earliest
    sender: str = NA
    recipients: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    message_date: str = NA
    subject: str = NA
    body: str = ""
    page_number: int | None = None  # for threads printed to PDF


class ExtractedDocument(BaseModel):
    text: str
    extractor: str = ""
    page_count: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Populated only for email sources.
    is_email_thread: bool = False
    thread_subject: str = NA
    messages: list[EmailMessage] = Field(default_factory=list)


class NormalizedDocument(BaseModel):
    document_id: str
    source_file_name: str
    source_path: str
    document_type: str
    extractor: str = ""

    upload_timestamp: str = ""
    extraction_timestamp: str = Field(default_factory=_utcnow)
    normalization_timestamp: str = Field(default_factory=_utcnow)

    content_hash: str = ""
    char_count: int = 0
    page_count: int | None = None

    content: str
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)

    is_email_thread: bool = False
    messages: list[EmailMessage] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Chunk
# --------------------------------------------------------------------------- #

class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    chunk_number: int
    total_chunks: int = 0

    content: str
    token_count: int = 0
    char_start: int = 0
    char_end: int = 0

    # Chunk provenance
    chunk_strategy: Literal["token", "email_message"] = "token"
    message_index: int | None = None   # order within thread; None for non-email
    message_total: int | None = None

    source_file_name: str
    source_path: str
    document_type: str

    # Full metadata travels with the chunk.
    metadata: DocumentMetadata

    citation: str = ""
    content_hash: str = ""
    chunk_timestamp: str = Field(default_factory=_utcnow)
    content_vector: list[float] | None = None

    def to_search_record(self) -> dict[str, Any]:
        """Flatten to the Azure AI Search document shape."""
        m = self.metadata
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "chunk_number": self.chunk_number,
            "total_chunks": self.total_chunks,
            "content": self.content,
            "content_vector": self.content_vector,

            "scenario_id": m.scenario_id,
            "scenario_ids": m.scenario_ids or [m.scenario_id],
            "source_id": m.source_id,
            "source_file": m.source_file,
            "source_type": m.source_type,
            "source_date": m.source_date,
            "author": m.author,
            "page_number": m.page_number,

            "tax_topic": m.tax_topic,
            "jurisdiction": m.jurisdiction,
            "tax_year_or_effective_period": m.tax_year_or_effective_period,
            "authority_level": m.authority_level,

            "legal_entity": m.legal_entity,
            "business_unit": m.business_unit,

            "thread_subject": m.thread_subject,
            "message_date": m.message_date,
            "message_index": self.message_index,

            "validity_status": m.validity_status,
            "current_or_superseded": m.current_or_superseded,
            "access_classification": m.access_classification,

            "citation": self.citation,
            "token_count": self.token_count,
            "chunk_strategy": self.chunk_strategy,
            "chunk_timestamp": self.chunk_timestamp,
        }
