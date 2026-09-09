"""Precedent analysis contract.

This module encodes the two safety rules of the entire system as validators
rather than as prompt instructions:

1. ``NO_PRECEDENT`` implies ``validity_status is None``.
2. ``CURRENT`` requires an affirmative, evidence-backed rationale -- it is never
   inferable from the absence of a newer source.

A prompt regression that violates either rule raises a ``ValidationError`` at the
workflow boundary instead of quietly producing an overconfident tax answer.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from runtime.models.base import RuntimeModel
from runtime.models.enums import (
    EvidenceSufficiency,
    PrecedentStatus,
    REVIEW_FORCING_VALIDITY,
    ValidityStatus,
)

#: Phrases that indicate the model justified CURRENT by absence of contradiction
#: rather than by affirmative evidence. Cheap deterministic guard against the
#: highest-risk failure mode in the system.
_ABSENCE_REASONING_MARKERS: tuple[str, ...] = (
    "no newer",
    "no more recent",
    "no later",
    "nothing newer",
    "not superseded",
    "no evidence to the contrary",
    "no contradicting",
    "no indication that it changed",
    "absence of",
    "nothing suggests",
    "no updates were found",
    "no subsequent",
)


class PrecedentAnalysisResult(RuntimeModel):
    """Output of the Precedent Analysis Agent -- the core reasoning component.

    The agent does two jobs: classify how closely prior guidance applies, and
    assess whether that guidance still holds. Both must name their supporting
    sources.
    """

    request_id: str

    precedent_status: PrecedentStatus
    validity_status: ValidityStatus | None = Field(
        default=None,
        description="None if and only if precedent_status is NO_PRECEDENT.",
    )

    supporting_source_ids: list[str] = Field(default_factory=list)
    material_similarities: list[str] = Field(default_factory=list)
    material_differences: list[str] = Field(default_factory=list)

    precedent_reason: str | None = None
    validity_reason: str | None = Field(
        default=None,
        description="Required whenever validity_status is not None.",
    )

    evidence_sufficiency: EvidenceSufficiency
    tax_review_required: bool = False

    # -- Rule 1 ------------------------------------------------------------
    @model_validator(mode="after")
    def _no_precedent_has_null_validity(self) -> "PrecedentAnalysisResult":
        if self.precedent_status is PrecedentStatus.NO_PRECEDENT:
            if self.validity_status is not None:
                raise ValueError(
                    "precedent_status=NO_PRECEDENT requires validity_status=None; "
                    f"got {self.validity_status}. There is no precedent whose "
                    "validity could be assessed."
                )
            if self.supporting_source_ids:
                raise ValueError(
                    "precedent_status=NO_PRECEDENT must not cite supporting "
                    "source IDs."
                )
        elif self.validity_status is None:
            raise ValueError(
                f"precedent_status={self.precedent_status.value} requires a "
                "validity_status. Use UNKNOWN if the corpus does not establish "
                "whether the guidance still holds."
            )
        return self

    # -- Rule 2 ------------------------------------------------------------
    @model_validator(mode="after")
    def _current_requires_affirmative_evidence(self) -> "PrecedentAnalysisResult":
        if self.validity_status is not ValidityStatus.CURRENT:
            return self

        if not self.supporting_source_ids:
            raise ValueError(
                "validity_status=CURRENT requires supporting_source_ids naming "
                "the evidence that affirmatively supports continued "
                "applicability."
            )
        reason = (self.validity_reason or "").strip()
        if not reason:
            raise ValueError(
                "validity_status=CURRENT requires a validity_reason citing "
                "affirmative evidence."
            )
        lowered = reason.lower()
        for marker in _ABSENCE_REASONING_MARKERS:
            if marker in lowered:
                raise ValueError(
                    f"validity_status=CURRENT was justified by absence of "
                    f"contradicting evidence ({marker!r} in validity_reason). "
                    "Absence of a newer source is not evidence of currency -- "
                    "use UNKNOWN instead."
                )
        return self

    # -- Supporting consistency -------------------------------------------
    @model_validator(mode="after")
    def _validity_reason_present(self) -> "PrecedentAnalysisResult":
        if self.validity_status is not None and not (self.validity_reason or "").strip():
            raise ValueError(
                f"validity_status={self.validity_status.value} requires a "
                "validity_reason."
            )
        return self

    @model_validator(mode="after")
    def _partial_match_names_differences(self) -> "PrecedentAnalysisResult":
        if (
            self.precedent_status is PrecedentStatus.PARTIAL_MATCH
            and not self.material_differences
        ):
            raise ValueError(
                "precedent_status=PARTIAL_MATCH requires at least one entry in "
                "material_differences explaining what differs."
            )
        return self

    @model_validator(mode="after")
    def _match_cites_sources(self) -> "PrecedentAnalysisResult":
        if (
            self.precedent_status
            in {PrecedentStatus.MATCH, PrecedentStatus.PARTIAL_MATCH}
            and not self.supporting_source_ids
        ):
            raise ValueError(
                f"precedent_status={self.precedent_status.value} requires "
                "supporting_source_ids."
            )
        return self

    # -- Derived helpers ---------------------------------------------------
    @property
    def requires_review(self) -> bool:
        """Whether this result independently forces human tax review.

        The Response Policy owns the final decision; this is a convenience for
        policy code and traces, not a substitute for it.
        """
        if self.tax_review_required:
            return True
        if self.precedent_status is PrecedentStatus.PARTIAL_MATCH:
            return True
        if self.validity_status in REVIEW_FORCING_VALIDITY:
            return True
        return self.evidence_sufficiency is not EvidenceSufficiency.SUFFICIENT

    def assert_sources_were_retrieved(self, retrieved: set[str]) -> None:
        """Guard against the agent inventing source IDs.

        Called by the workflow immediately after precedent analysis, before the
        result is allowed to reach synthesis.
        """
        invented = set(self.supporting_source_ids) - retrieved
        if invented:
            raise ValueError(
                "Precedent agent cited source IDs that were never retrieved: "
                f"{sorted(invented)}"
            )
