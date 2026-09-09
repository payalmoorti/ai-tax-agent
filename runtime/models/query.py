"""Query-side contracts: the raw user question and its structured reading."""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from runtime.models.base import RuntimeModel
from runtime.models.enums import QueryIntent

NonEmptyStr = Annotated[str, Field(min_length=1)]


def new_request_id() -> str:
    """Correlation ID carried through every span, trace and eval record."""
    return f"REQ-{uuid.uuid4().hex[:12].upper()}"


class UserQuery(RuntimeModel):
    """Raw inbound question. The only untyped text in the system."""

    request_id: str = Field(default_factory=new_request_id)
    session_id: str | None = None
    query: NonEmptyStr = Field(max_length=4000)
    user_id: str | None = Field(
        default=None,
        description="Entra object ID. Never log alongside evidence content.",
    )

    @field_validator("query")
    @classmethod
    def _reject_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query must contain non-whitespace text")
        return v


class SearchFilters(RuntimeModel):
    """Filters applied to internal retrieval.

    Note what is absent: there is no ``jurisdictions`` filter field. That is
    deliberate and load-bearing. Jurisdiction is a boost signal only -- see
    ``shared.contracts.index_schema.BOOST_ONLY_FIELDS``. A Philadelphia question
    must still retrieve NYC evidence so the Precedent Agent can return
    PARTIAL_MATCH.
    """

    entities: list[str] = Field(default_factory=list)
    business_units: list[str] = Field(default_factory=list)
    tax_topic: str | None = None
    evidence_types: list[str] = Field(default_factory=list)
    sent_after: str | None = Field(
        default=None, description="ISO-8601 date, inclusive lower bound."
    )
    sent_before: str | None = None

    #: Jurisdictions used to BOOST relevance, never to exclude documents.
    boost_jurisdictions: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not any([
            self.entities,
            self.business_units,
            self.tax_topic,
            self.evidence_types,
            self.sent_after,
            self.sent_before,
        ])


class QueryUnderstandingResult(RuntimeModel):
    """Structured reading of the user question.

    Produced by the Query Understanding Agent. The agent must NOT answer the tax
    question, must NOT classify precedent, and must NOT invent facts the user did
    not supply.
    """

    request_id: str
    intent: QueryIntent
    topic: str | None = None
    subtopic: str | None = None
    entities: list[str] = Field(default_factory=list)
    jurisdictions: list[str] = Field(default_factory=list)
    material_facts: list[str] = Field(default_factory=list)

    internal_search_query: NonEmptyStr = Field(
        description="Rewritten query sent to Azure AI Search."
    )
    external_search_query: str | None = Field(
        default=None,
        description="Populated only when external research may be required.",
    )

    clarification_needed: str | None = Field(
        default=None,
        description="Set only when intent is CLARIFICATION.",
    )

    @model_validator(mode="after")
    def _clarification_consistency(self) -> "QueryUnderstandingResult":
        if self.intent is QueryIntent.CLARIFICATION and not self.clarification_needed:
            raise ValueError(
                "intent=CLARIFICATION requires clarification_needed to state "
                "what is missing"
            )
        if self.intent is not QueryIntent.CLARIFICATION and self.clarification_needed:
            raise ValueError(
                "clarification_needed may only be set when intent=CLARIFICATION"
            )
        return self

    def to_filters(self) -> SearchFilters:
        """Build retrieval filters, routing jurisdiction to boost-only."""
        return SearchFilters(
            entities=self.entities,
            tax_topic=self.topic,
            boost_jurisdictions=self.jurisdictions,
        )
