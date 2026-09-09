"""Internal evidence contracts.

``InternalEvidence`` is the runtime-side mirror of the Azure AI Search document
written by ingestion. Field names are imported from
``shared.contracts.index_schema`` so a rename on the ingestion side breaks this
module at test time rather than at query time in production.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from runtime.models.base import RuntimeModel
from shared.contracts.index_schema import (
    F,
    EvidenceType,
    SemanticState,
)


class InternalEvidence(RuntimeModel):
    """One retrieved chunk of Amsted historical evidence.

    Provenance fields are mandatory in spirit: ``source_id`` and ``chunk_id``
    are what the Citation Validator checks claims against, and what the UI turns
    into a link back to the original file in Blob Storage.
    """

    # --- Identity / provenance -------------------------------------------
    source_id: str
    chunk_id: str
    source_file: str
    source_uri: str | None = None

    # --- Content ----------------------------------------------------------
    content: str

    # --- Email / thread structure ----------------------------------------
    thread_id: str | None = None
    message_id: str | None = None
    sender: str | None = None
    sent_date: datetime | None = None

    # --- Document structure ----------------------------------------------
    page_number: int | None = Field(default=None, ge=1)

    # --- Tax enrichment ---------------------------------------------------
    tax_topic: str | None = None
    subtopic: str | None = None
    entities: list[str] = Field(default_factory=list)
    business_units: list[str] = Field(default_factory=list)
    jurisdictions: list[str] = Field(default_factory=list)
    tax_concepts: list[str] = Field(default_factory=list)
    material_facts: list[str] = Field(default_factory=list)
    evidence_type: EvidenceType = EvidenceType.OTHER
    semantic_state: SemanticState = SemanticState.UNKNOWN

    # --- Retrieval scoring ------------------------------------------------
    search_score: float | None = None
    reranker_score: float | None = None

    @field_validator("entities", "jurisdictions", "business_units",
                     "tax_concepts", "material_facts", mode="before")
    @classmethod
    def _none_to_empty(cls, v: Any) -> Any:
        """Azure AI Search returns ``null`` for unpopulated collections."""
        return [] if v is None else v

    @classmethod
    def from_search_document(cls, doc: dict[str, Any]) -> "InternalEvidence":
        """Map a raw Azure AI Search result onto the typed contract.

        This is the ONLY place index field names are read. Everything downstream
        works with attributes.
        """
        return cls(
            source_id=doc[F.SOURCE_ID],
            chunk_id=doc[F.CHUNK_ID],
            source_file=doc[F.SOURCE_FILE],
            source_uri=doc.get(F.SOURCE_URI),
            content=doc[F.CONTENT],
            thread_id=doc.get(F.THREAD_ID),
            message_id=doc.get(F.MESSAGE_ID),
            sender=doc.get(F.SENDER),
            sent_date=doc.get(F.SENT_DATE),
            page_number=doc.get(F.PAGE_NUMBER),
            tax_topic=doc.get(F.TAX_TOPIC),
            subtopic=doc.get(F.SUBTOPIC),
            entities=doc.get(F.ENTITIES),
            business_units=doc.get(F.BUSINESS_UNITS),
            jurisdictions=doc.get(F.JURISDICTIONS),
            tax_concepts=doc.get(F.TAX_CONCEPTS),
            material_facts=doc.get(F.MATERIAL_FACTS),
            evidence_type=doc.get(F.EVIDENCE_TYPE) or EvidenceType.OTHER,
            semantic_state=doc.get(F.SEMANTIC_STATE) or SemanticState.UNKNOWN,
            search_score=doc.get("@search.score"),
            reranker_score=doc.get("@search.reranker_score"),
        )

    def citation_label(self) -> str:
        """Human-readable provenance string shown in the UI.

        Prefers the most specific locator available: message > page > file.
        """
        if self.message_id and self.sender:
            when = self.sent_date.date().isoformat() if self.sent_date else "undated"
            return f"{self.source_file} — message from {self.sender} ({when})"
        if self.page_number:
            return f"{self.source_file} — p. {self.page_number}"
        return self.source_file


class InternalEvidencePackage(RuntimeModel):
    """The full retrieval result for one question."""

    query: str
    evidence: list[InternalEvidence] = Field(default_factory=list)
    total_hits: int | None = None
    used_semantic_reranker: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.evidence

    @property
    def source_ids(self) -> set[str]:
        """Authoritative set the Citation Validator checks claims against."""
        return {e.source_id for e in self.evidence}

    @property
    def chunk_ids(self) -> set[str]:
        return {e.chunk_id for e in self.evidence}

    def by_source_id(self, source_id: str) -> list[InternalEvidence]:
        return [e for e in self.evidence if e.source_id == source_id]

    def superseded_source_ids(self) -> set[str]:
        """Sources ingestion flagged as superseded.

        The validator uses this to catch synthesis presenting stale guidance as
        if it were current.
        """
        return {
            e.source_id
            for e in self.evidence
            if e.semantic_state is SemanticState.SUPERSEDED
        }

    def distinct_jurisdictions(self) -> set[str]:
        """Feeds material-difference detection in precedent analysis."""
        return {j for e in self.evidence for j in e.jurisdictions}
    
    def ranked_source_ids(self) -> list[str]:
        """Distinct source IDs in rank order. Used for Hit@K metrics."""
        seen: list[str] = []
        for e in self.evidence:
            if e.source_id not in seen:
                seen.append(e.source_id)
        return seen
