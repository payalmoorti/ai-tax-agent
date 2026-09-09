"""Feedback and response contracts.

Feedback records capture enough context to diagnose *which workflow step* went
wrong, and pin the prompt and model versions in force at the time. Feedback is
never used to auto-tune prompts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import Field, model_validator

from runtime.models.base import RuntimeModel
from runtime.models.enums import (
    FeedbackReason,
    FeedbackType,
    PrecedentStatus,
    ResponseAction,
    ValidityStatus,
)
from runtime.models.synthesis import ClaimCitation


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceReference(RuntimeModel):
    """Slim citation shape returned to the UI."""

    source_id: str
    source_file: str
    source_uri: str | None = None
    citation_label: str
    page_number: int | None = None
    is_external: bool = False


class ChatResponse(RuntimeModel):
    """The ``POST /chat`` response body."""

    request_id: str
    response_action: ResponseAction
    precedent_status: PrecedentStatus
    validity_status: ValidityStatus | None = None

    answer: str | None = Field(
        default=None,
        description="None when response_action is ABSTAIN.",
    )
    material_differences: list[str] = Field(default_factory=list)
    review_triggers: list[str] = Field(default_factory=list)

    amsted_sources: list[SourceReference] = Field(default_factory=list)
    external_sources: list[SourceReference] = Field(default_factory=list)
    claim_citations: list[ClaimCitation] = Field(default_factory=list)

    tax_review_required: bool = False
    abstain_reason: str | None = None

    @model_validator(mode="after")
    def _abstain_has_no_answer(self) -> "ChatResponse":
        if self.response_action is ResponseAction.ABSTAIN:
            if self.answer:
                raise ValueError("ABSTAIN must not return an answer body.")
            if not self.abstain_reason:
                raise ValueError("ABSTAIN requires an abstain_reason.")
        elif not self.answer:
            raise ValueError(
                f"response_action={self.response_action.value} requires an answer."
            )
        return self


class FeedbackRecord(RuntimeModel):
    """Persisted to Table Storage. Keyed by request_id."""

    request_id: str
    response_id: str | None = None
    session_id: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    feedback_type: FeedbackType
    reasons: list[FeedbackReason] = Field(default_factory=list)
    comment: str | None = Field(default=None, max_length=4000)

    # Context snapshot -- what the system decided at the time.
    retrieved_source_ids: list[str] = Field(default_factory=list)
    precedent_status: PrecedentStatus | None = None
    validity_status: ValidityStatus | None = None
    response_action: ResponseAction | None = None

    # Version pinning -- makes regressions attributable.
    prompt_version: str | None = None
    model_version: str | None = None
    index_schema_version: str | None = None

    @model_validator(mode="after")
    def _needs_review_has_reason(self) -> "FeedbackRecord":
        if self.feedback_type is FeedbackType.NEEDS_REVIEW and not self.reasons:
            raise ValueError(
                "NEEDS_REVIEW feedback requires at least one structured reason."
            )
        return self
