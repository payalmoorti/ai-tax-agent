"""External tax research contracts.

External authority is a structurally different type from internal Amsted
evidence and must remain so end-to-end. Bloomberg saying something is true is
not the same as Amsted having concluded it -- collapsing the two is the most
consequential correctness failure this system can make.

The provider Protocol lives here so the workflow can be built and tested against
``MockExternalTaxResearchProvider`` long before any real subscription exists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import Field

from runtime.models.base import RuntimeModel
from runtime.models.enums import EvidenceOrigin


class ExternalResearchRequest(RuntimeModel):
    """Typed request handed to an approved external provider."""

    request_id: str
    research_question: str = Field(min_length=1)
    jurisdictions: list[str] = Field(default_factory=list)
    tax_topic: str | None = None
    entities: list[str] = Field(default_factory=list)
    as_of_date: datetime | None = None
    max_results: int = Field(default=5, ge=1, le=25)


class ExternalEvidence(RuntimeModel):
    """One item of external tax authority."""

    external_source_id: str
    provider: str
    title: str | None = None
    source_date: datetime | None = None
    content_summary: str
    citation: str = Field(
        min_length=1,
        description="Provider-supplied citation string. Never synthesised.",
    )
    source_uri: str | None = None

    #: Constant discriminator. Present so that synthesis and the UI cannot
    #: accidentally treat an external item as Amsted precedent.
    origin: EvidenceOrigin = EvidenceOrigin.EXTERNAL_AUTHORITY


class ExternalEvidencePackage(RuntimeModel):
    """Result of one external research call."""

    provider: str
    research_question: str
    evidence: list[ExternalEvidence] = Field(default_factory=list)
    retrieved_at: datetime | None = None
    provider_error: str | None = Field(
        default=None,
        description="Set when the provider failed. Policy should route to "
                    "ABSTAIN rather than answering without required authority.",
    )

    @property
    def is_empty(self) -> bool:
        return not self.evidence

    @property
    def succeeded(self) -> bool:
        return self.provider_error is None

    @property
    def external_source_ids(self) -> set[str]:
        return {e.external_source_id for e in self.evidence}


@runtime_checkable
class ExternalTaxResearchProvider(Protocol):
    """Interface every external provider adapter implements.

    Implement ``MockExternalTaxResearchProvider`` against this first. Do not
    block the core runtime build on Bloomberg availability -- confirm
    subscription, licensing, auth and permitted use in parallel.
    """

    name: str

    async def research(
        self, request: ExternalResearchRequest
    ) -> ExternalEvidencePackage:
        ...
