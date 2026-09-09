"""Typed contracts for the Amsted Tax Agent runtime.

Every object passed between workflow components is defined here. Import from
this package rather than from submodules so that internal reorganisation does
not ripple through agent and workflow code.

Build order note: this package is Step 2 of the runtime build and is BLOCKING.
Prompts are written against these schemas, so nothing above it should be
implemented until these models and their tests are green.
"""

from runtime.models.base import MutableRuntimeModel, RuntimeModel
from runtime.models.enums import (
    EXTERNAL_RESEARCH_ELIGIBLE_INTENTS,
    REVIEW_FORCING_VALIDITY,
    EvidenceOrigin,
    EvidenceSufficiency,
    FeedbackReason,
    FeedbackType,
    PrecedentStatus,
    QueryIntent,
    ResponseAction,
    ValidationStatus,
    ValidityStatus,
)
from runtime.models.evidence import InternalEvidence, InternalEvidencePackage
from runtime.models.external import (
    ExternalEvidence,
    ExternalEvidencePackage,
    ExternalResearchRequest,
    ExternalTaxResearchProvider,
)
from runtime.models.feedback import (
    ChatResponse,
    FeedbackRecord,
    SourceReference,
)
from runtime.models.policy import EvidencePolicyDecision, ResponseDecision
from runtime.models.precedent import PrecedentAnalysisResult
from runtime.models.query import (
    QueryUnderstandingResult,
    SearchFilters,
    UserQuery,
    new_request_id,
)
from runtime.models.synthesis import ClaimCitation, SynthesisResult
from runtime.models.validation import ValidationResult

__all__ = [
    # base
    "RuntimeModel",
    "MutableRuntimeModel",
    # enums
    "QueryIntent",
    "PrecedentStatus",
    "ValidityStatus",
    "EvidenceSufficiency",
    "ResponseAction",
    "ValidationStatus",
    "EvidenceOrigin",
    "FeedbackType",
    "FeedbackReason",
    "REVIEW_FORCING_VALIDITY",
    "EXTERNAL_RESEARCH_ELIGIBLE_INTENTS",
    # query
    "UserQuery",
    "QueryUnderstandingResult",
    "SearchFilters",
    "new_request_id",
    # evidence
    "InternalEvidence",
    "InternalEvidencePackage",
    # precedent
    "PrecedentAnalysisResult",
    # policy
    "EvidencePolicyDecision",
    "ResponseDecision",
    # external
    "ExternalResearchRequest",
    "ExternalEvidence",
    "ExternalEvidencePackage",
    "ExternalTaxResearchProvider",
    # synthesis
    "ClaimCitation",
    "SynthesisResult",
    # validation
    "ValidationResult",
    # api / feedback
    "ChatResponse",
    "SourceReference",
    "FeedbackRecord",
]
