"""
Controlled vocabularies for the runtime workflow.

Every routing decision in the system is a function of these enums. They are
defined in one place so that the Evidence Policy and Response Policy can be
exhaustively unit-tested across the full ``intent x precedent x validity``
cross-product.

Do not add a value here without adding the corresponding policy rows.
"""

from __future__ import annotations

from enum import Enum


class QueryIntent(str, Enum):
    """What the user is actually asking for.

    Produced by the Query Understanding Agent; consumed by the Evidence Policy.
    """

    HISTORICAL_PRECEDENT = "HISTORICAL_PRECEDENT"
    """What did Amsted previously conclude? Internal corpus only."""

    CURRENT_TAX_QUESTION = "CURRENT_TAX_QUESTION"
    """What should we do now? External authority may be required."""

    MIXED_RESEARCH = "MIXED_RESEARCH"
    """What did we do, and is it still consistent with current guidance?"""

    SOURCE_LOOKUP = "SOURCE_LOOKUP"
    """Find me the document. Retrieval only, no precedent reasoning."""

    CLARIFICATION = "CLARIFICATION"
    """Question is under-specified; ask before researching."""


class PrecedentStatus(str, Enum):
    """How closely historical Amsted guidance applies to the current question."""

    MATCH = "MATCH"
    """Same tax issue, materially similar facts."""

    PARTIAL_MATCH = "PARTIAL_MATCH"
    """Relevant guidance exists but one or more material facts differ."""

    NO_PRECEDENT = "NO_PRECEDENT"
    """Corpus does not support an Amsted precedent for this question."""


class ValidityStatus(str, Enum):
    """What the corpus indicates about whether retrieved precedent still holds.

    ``CURRENT`` is the highest-risk value in the system. It must be supported by
    affirmative evidence -- never inferred from the absence of a newer source.
    That failure mode is tracked as the *False CURRENT rate* in evaluation.

    Where precedent_status is NO_PRECEDENT this field is ``None``, not a member
    of this enum -- there is no precedent whose validity could be assessed.
    """

    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"
    UNDER_REVIEW = "UNDER_REVIEW"
    UNKNOWN = "UNKNOWN"


class EvidenceSufficiency(str, Enum):
    """Whether retrieved evidence can support a conclusion."""

    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT_FOR_CURRENT_CONCLUSION = "INSUFFICIENT_FOR_CURRENT_CONCLUSION"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICTING = "CONFLICTING"


class ResponseAction(str, Enum):
    """Terminal decision returned to the user."""

    ANSWER = "ANSWER"
    ANSWER_WITH_TAX_REVIEW = "ANSWER_WITH_TAX_REVIEW"
    ABSTAIN = "ABSTAIN"


class ValidationStatus(str, Enum):
    """Outcome of deterministic citation validation."""

    PASSED = "PASSED"
    FAILED_UNSUPPORTED_CLAIM = "FAILED_UNSUPPORTED_CLAIM"
    FAILED_INVALID_CITATION = "FAILED_INVALID_CITATION"
    FAILED_SUPERSEDED_MISUSE = "FAILED_SUPERSEDED_MISUSE"


class EvidenceOrigin(str, Enum):
    """Keeps internal Amsted precedent structurally separate from external
    authority. Carried through synthesis and rendered separately in the UI."""

    AMSTED_INTERNAL = "AMSTED_INTERNAL"
    EXTERNAL_AUTHORITY = "EXTERNAL_AUTHORITY"


class FeedbackType(str, Enum):
    HELPFUL = "HELPFUL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class FeedbackReason(str, Enum):
    """Structured reasons attached to NEEDS_REVIEW feedback.

    These map onto specific workflow steps so that feedback is diagnosable
    rather than merely negative.
    """

    WRONG_SOURCE = "WRONG_SOURCE"
    MISSING_SOURCE = "MISSING_SOURCE"
    WRONG_PRECEDENT_STATUS = "WRONG_PRECEDENT_STATUS"
    WRONG_VALIDITY_STATUS = "WRONG_VALIDITY_STATUS"
    OUTDATED_SOURCE = "OUTDATED_SOURCE"
    CITATION_ERROR = "CITATION_ERROR"
    MATERIAL_FACT_MISSED = "MATERIAL_FACT_MISSED"
    EXTERNAL_RESEARCH_ERROR = "EXTERNAL_RESEARCH_ERROR"
    INCOMPLETE_ANSWER = "INCOMPLETE_ANSWER"
    OTHER = "OTHER"


#: Validity values that always force human tax review regardless of intent.
REVIEW_FORCING_VALIDITY: frozenset[ValidityStatus] = frozenset({
    ValidityStatus.UNDER_REVIEW,
    ValidityStatus.UNKNOWN,
    ValidityStatus.SUPERSEDED,
})

#: Intents that permit calling an external tax research provider.
EXTERNAL_RESEARCH_ELIGIBLE_INTENTS: frozenset[QueryIntent] = frozenset({
    QueryIntent.CURRENT_TAX_QUESTION,
    QueryIntent.MIXED_RESEARCH,
})
