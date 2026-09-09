"""Policy contracts.

Both policy decisions are produced by deterministic code, never by an LLM. These
models exist so that routing decisions are typed, traceable and unit-testable
across the full ``intent x precedent x validity`` cross-product.

If you find yourself wanting to move any of this logic into a prompt, don't.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from runtime.models.base import RuntimeModel
from runtime.models.enums import ResponseAction


class EvidencePolicyDecision(RuntimeModel):
    """Whether the workflow may call an external tax research provider."""

    external_research_required: bool
    reason: str = Field(
        min_length=1,
        description="Human-readable rule justification; surfaced in traces.",
    )
    rule_id: str | None = Field(
        default=None,
        description="Key from config/policy.yaml that fired, e.g. "
                    "'current_partial_match'. Makes policy behaviour auditable.",
    )
    allowed_providers: list[str] = Field(default_factory=list)
    response_constraints: list[str] = Field(
        default_factory=list,
        description="Constraints injected into the synthesis prompt, e.g. "
                    "'must state jurisdiction difference explicitly'.",
    )
    tax_review_required: bool = False

    @model_validator(mode="after")
    def _providers_present_when_required(self) -> "EvidencePolicyDecision":
        if self.external_research_required and not self.allowed_providers:
            raise ValueError(
                "external_research_required=True requires at least one entry in "
                "allowed_providers."
            )
        if not self.external_research_required and self.allowed_providers:
            raise ValueError(
                "allowed_providers must be empty when external research is not "
                "required."
            )
        return self


class ResponseDecision(RuntimeModel):
    """Terminal action returned to the user."""

    response_action: ResponseAction
    reason: str = Field(min_length=1)
    rule_id: str | None = None
    review_triggers: list[str] = Field(
        default_factory=list,
        description="Which conditions forced review or abstention. Shown to the "
                    "user when action is ANSWER_WITH_TAX_REVIEW.",
    )

    @model_validator(mode="after")
    def _review_has_triggers(self) -> "ResponseDecision":
        if (
            self.response_action is ResponseAction.ANSWER_WITH_TAX_REVIEW
            and not self.review_triggers
        ):
            raise ValueError(
                "ANSWER_WITH_TAX_REVIEW requires review_triggers stating why "
                "review is needed."
            )
        return self

    @property
    def is_terminal_refusal(self) -> bool:
        return self.response_action is ResponseAction.ABSTAIN
