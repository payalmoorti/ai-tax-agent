"""Synthesis contract.

Every substantive claim in a synthesised answer must carry at least one
citation, and every citation must name a source that was actually retrieved.
``ClaimCitation`` is the unit the Citation Validator checks.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from runtime.models.base import RuntimeModel
from runtime.models.enums import (
    EvidenceOrigin,
    PrecedentStatus,
    ValidityStatus,
)


class ClaimCitation(RuntimeModel):
    """A single claim in the answer bound to its supporting evidence."""

    claim_id: str = Field(description="Stable within one response, e.g. 'C1'.")
    claim_text: str = Field(min_length=1)
    origin: EvidenceOrigin
    source_ids: list[str] = Field(
        min_length=1,
        description="Internal source_ids, or external_source_ids when origin is "
                    "EXTERNAL_AUTHORITY. Never mixed within one claim.",
    )
    chunk_ids: list[str] = Field(default_factory=list)
    page_number: int | None = Field(default=None, ge=1)
    message_id: str | None = None


class SynthesisResult(RuntimeModel):
    """Output of the Evidence Synthesis Agent.

    Note the deliberate structural separation of ``historical_guidance`` from
    ``external_research_summary``. The prompt must populate them independently
    and the UI renders them under separate headings.
    """

    request_id: str

    # Carried forward unchanged from precedent analysis. Synthesis may not
    # revise these -- it reports them.
    precedent_status: PrecedentStatus
    validity_status: ValidityStatus | None = None

    historical_guidance: str | None = Field(
        default=None,
        description="What Amsted concluded historically. Internal evidence only.",
    )
    material_differences: list[str] = Field(default_factory=list)
    external_research_summary: str | None = Field(
        default=None,
        description="Current external authority. Never presented as Amsted "
                    "precedent.",
    )
    conclusion: str = Field(min_length=1)
    caveats: list[str] = Field(default_factory=list)

    tax_review_required: bool = False
    claim_citations: list[ClaimCitation] = Field(default_factory=list)

    prompt_version: str | None = None
    model_deployment: str | None = None

    @model_validator(mode="after")
    def _validity_mirrors_precedent(self) -> "SynthesisResult":
        if self.precedent_status is PrecedentStatus.NO_PRECEDENT:
            if self.validity_status is not None:
                raise ValueError(
                    "NO_PRECEDENT requires validity_status=None in synthesis."
                )
            if self.historical_guidance:
                raise ValueError(
                    "NO_PRECEDENT must not present historical_guidance. If "
                    "guidance exists, precedent_status is wrong upstream."
                )
        return self

    @model_validator(mode="after")
    def _conclusion_is_cited(self) -> "SynthesisResult":
        if not self.claim_citations:
            raise ValueError(
                "SynthesisResult requires at least one ClaimCitation. An "
                "uncited tax conclusion is not permitted."
            )
        return self

    @model_validator(mode="after")
    def _unique_claim_ids(self) -> "SynthesisResult":
        ids = [c.claim_id for c in self.claim_citations]
        if len(ids) != len(set(ids)):
            raise ValueError("claim_id values must be unique within a response.")
        return self

    @property
    def internal_source_ids(self) -> set[str]:
        return {
            sid
            for c in self.claim_citations
            if c.origin is EvidenceOrigin.AMSTED_INTERNAL
            for sid in c.source_ids
        }

    @property
    def external_source_ids(self) -> set[str]:
        return {
            sid
            for c in self.claim_citations
            if c.origin is EvidenceOrigin.EXTERNAL_AUTHORITY
            for sid in c.source_ids
        }
