"""Citation validation contract.

Validation is deterministic: it checks that cited sources exist, were actually
retrieved, are typed correctly, and that superseded guidance is not presented as
current. LLM claim-entailment checking is a later enhancement layered on top of
this, not a replacement for it.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from runtime.models.base import RuntimeModel
from runtime.models.enums import ValidationStatus


class ValidationResult(RuntimeModel):
    """Outcome of validating a ``SynthesisResult`` against retrieved evidence."""

    request_id: str
    validation_status: ValidationStatus

    unsupported_claim_ids: list[str] = Field(
        default_factory=list,
        description="Claims with no resolvable supporting evidence.",
    )
    invalid_citation_ids: list[str] = Field(
        default_factory=list,
        description="Cited source IDs that were never retrieved -- i.e. invented.",
    )
    superseded_source_misuse: bool = Field(
        default=False,
        description="True when guidance flagged SUPERSEDED is presented as if "
                    "it still applied.",
    )
    origin_confusion_claim_ids: list[str] = Field(
        default_factory=list,
        description="Claims citing external authority as Amsted precedent, or "
                    "vice versa.",
    )
    detail: str | None = None

    @model_validator(mode="after")
    def _status_matches_findings(self) -> "ValidationResult":
        has_findings = bool(
            self.unsupported_claim_ids
            or self.invalid_citation_ids
            or self.superseded_source_misuse
            or self.origin_confusion_claim_ids
        )
        if self.validation_status is ValidationStatus.PASSED and has_findings:
            raise ValueError(
                "validation_status=PASSED is inconsistent with recorded "
                "findings."
            )
        if self.validation_status is not ValidationStatus.PASSED and not has_findings:
            raise ValueError(
                f"validation_status={self.validation_status.value} requires at "
                "least one recorded finding."
            )
        return self

    @property
    def passed(self) -> bool:
        return self.validation_status is ValidationStatus.PASSED

    def failure_summary(self) -> str:
        """One-line summary for traces and ABSTAIN reasons."""
        if self.passed:
            return "citation validation passed"
        parts: list[str] = []
        if self.invalid_citation_ids:
            parts.append(f"invented sources: {sorted(self.invalid_citation_ids)}")
        if self.unsupported_claim_ids:
            parts.append(f"unsupported claims: {sorted(self.unsupported_claim_ids)}")
        if self.superseded_source_misuse:
            parts.append("superseded guidance presented as current")
        if self.origin_confusion_claim_ids:
            parts.append(
                f"internal/external confusion: {sorted(self.origin_confusion_claim_ids)}"
            )
        return "; ".join(parts)
