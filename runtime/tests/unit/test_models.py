"""Contract tests for the runtime domain models.

These tests are the enforcement mechanism for the two safety rules. If someone
later relaxes a validator to make a prompt "work", these fail.

Run:  pytest runtime/tests/unit/test_models.py -v
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from runtime.models import (
    ChatResponse,
    ClaimCitation,
    EvidenceOrigin,
    EvidencePolicyDecision,
    EvidenceSufficiency,
    FeedbackRecord,
    FeedbackReason,
    FeedbackType,
    InternalEvidence,
    InternalEvidencePackage,
    PrecedentAnalysisResult,
    PrecedentStatus,
    QueryIntent,
    QueryUnderstandingResult,
    ResponseAction,
    ResponseDecision,
    SynthesisResult,
    UserQuery,
    ValidationResult,
    ValidationStatus,
    ValidityStatus,
)
from shared.contracts.index_schema import (
    F,
    SemanticState,
    assert_filterable,
)


# ---------------------------------------------------------------------------
# Index schema contract
# ---------------------------------------------------------------------------

def test_jurisdiction_cannot_be_hard_filtered():
    """The PARTIAL_MATCH guard. Hard-filtering jurisdiction makes a
    Philadelphia question unable to retrieve NYC evidence."""
    with pytest.raises(ValueError, match="boost-only"):
        assert_filterable(F.JURISDICTIONS)


def test_filterable_fields_accepted():
    assert_filterable(F.TAX_TOPIC)
    assert_filterable(F.ENTITIES)


def test_unknown_field_rejected():
    with pytest.raises(ValueError, match="not a filterable"):
        assert_filterable("not_a_real_field")


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def test_user_query_generates_request_id():
    q = UserQuery(query="What did Amsted conclude about ADS work in NYC?")
    assert q.request_id.startswith("REQ-")


def test_blank_query_rejected():
    with pytest.raises(ValidationError):
        UserQuery(query="   ")


def test_clarification_requires_explanation():
    with pytest.raises(ValidationError, match="clarification_needed"):
        QueryUnderstandingResult(
            request_id="REQ-1",
            intent=QueryIntent.CLARIFICATION,
            internal_search_query="x",
        )


def test_jurisdiction_routed_to_boost_not_filter():
    qu = QueryUnderstandingResult(
        request_id="REQ-1",
        intent=QueryIntent.CURRENT_TAX_QUESTION,
        jurisdictions=["Philadelphia"],
        entities=["ADS"],
        internal_search_query="ADS service activity nexus",
    )
    filters = qu.to_filters()
    assert filters.boost_jurisdictions == ["Philadelphia"]
    assert not hasattr(filters, "jurisdictions")


# ---------------------------------------------------------------------------
# Evidence mapping
# ---------------------------------------------------------------------------

def _search_doc(**overrides):
    doc = {
        F.SOURCE_ID: "SRC-0009",
        F.CHUNK_ID: "SRC-0009::c3",
        F.SOURCE_FILE: "ADS_NYC_Tax_Analysis.pdf",
        F.CONTENT: "Historical analysis of ADS service activity in New York City.",
        F.JURISDICTIONS: ["New York City"],
        F.ENTITIES: ["ADS"],
        F.TAX_CONCEPTS: None,          # Search returns null for empty collections
        F.PAGE_NUMBER: 4,
        F.SEMANTIC_STATE: "SETTLED",
        "@search.score": 0.87,
    }
    doc.update(overrides)
    return doc


def test_from_search_document_maps_provenance():
    ev = InternalEvidence.from_search_document(_search_doc())
    assert ev.source_id == "SRC-0009"
    assert ev.page_number == 4
    assert ev.tax_concepts == []       # null coerced, not crashed
    assert ev.semantic_state is SemanticState.SETTLED
    assert ev.search_score == 0.87


def test_citation_label_prefers_most_specific_locator():
    ev = InternalEvidence.from_search_document(_search_doc())
    assert "p. 4" in ev.citation_label()


def test_package_exposes_superseded_sources():
    pkg = InternalEvidencePackage(
        query="q",
        evidence=[
            InternalEvidence.from_search_document(_search_doc()),
            InternalEvidence.from_search_document(
                _search_doc(**{
                    F.SOURCE_ID: "SRC-0010",
                    F.CHUNK_ID: "SRC-0010::c1",
                    F.SEMANTIC_STATE: "SUPERSEDED",
                })
            ),
        ],
    )
    assert pkg.superseded_source_ids() == {"SRC-0010"}
    assert pkg.source_ids == {"SRC-0009", "SRC-0010"}


# ---------------------------------------------------------------------------
# RULE 1 -- NO_PRECEDENT implies null validity
# ---------------------------------------------------------------------------

def test_no_precedent_forbids_validity_status():
    with pytest.raises(ValidationError, match="requires validity_status=None"):
        PrecedentAnalysisResult(
            request_id="REQ-1",
            precedent_status=PrecedentStatus.NO_PRECEDENT,
            validity_status=ValidityStatus.UNKNOWN,
            validity_reason="n/a",
            evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
        )


def test_no_precedent_forbids_supporting_sources():
    with pytest.raises(ValidationError, match="must not cite supporting"):
        PrecedentAnalysisResult(
            request_id="REQ-1",
            precedent_status=PrecedentStatus.NO_PRECEDENT,
            supporting_source_ids=["SRC-0009"],
            evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
        )


def test_match_requires_validity_status():
    with pytest.raises(ValidationError, match="requires a validity_status"):
        PrecedentAnalysisResult(
            request_id="REQ-1",
            precedent_status=PrecedentStatus.MATCH,
            supporting_source_ids=["SRC-0009"],
            evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
        )


def test_valid_no_precedent_result():
    r = PrecedentAnalysisResult(
        request_id="REQ-1",
        precedent_status=PrecedentStatus.NO_PRECEDENT,
        evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
    )
    assert r.validity_status is None
    assert r.requires_review is True


# ---------------------------------------------------------------------------
# RULE 2 -- CURRENT requires affirmative evidence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_reason",
    [
        "No newer guidance was retrieved, so the position still stands.",
        "Nothing suggests the treatment changed.",
        "The source was not superseded by anything in the corpus.",
        "Absence of contradicting evidence supports continued applicability.",
        "No subsequent analysis was located.",
    ],
)
def test_current_rejected_when_justified_by_absence(bad_reason):
    """The highest-risk failure mode in the system, blocked at the type layer."""
    with pytest.raises(ValidationError, match="absence of contradicting evidence"):
        PrecedentAnalysisResult(
            request_id="REQ-1",
            precedent_status=PrecedentStatus.MATCH,
            validity_status=ValidityStatus.CURRENT,
            supporting_source_ids=["SRC-0009"],
            validity_reason=bad_reason,
            evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
        )


def test_current_accepted_with_affirmative_evidence():
    r = PrecedentAnalysisResult(
        request_id="REQ-1",
        precedent_status=PrecedentStatus.MATCH,
        validity_status=ValidityStatus.CURRENT,
        supporting_source_ids=["SRC-0009"],
        material_similarities=["ADS service activity", "New York City"],
        validity_reason=(
            "SRC-0009 was reconfirmed in the FY2025 review memo, which restates "
            "the NYC treatment as continuing to apply."
        ),
        evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
    )
    assert r.validity_status is ValidityStatus.CURRENT
    assert r.requires_review is False


def test_current_requires_sources():
    with pytest.raises(ValidationError, match="requires supporting_source_ids"):
        PrecedentAnalysisResult(
            request_id="REQ-1",
            precedent_status=PrecedentStatus.MATCH,
            validity_status=ValidityStatus.CURRENT,
            validity_reason="Reconfirmed in the FY2025 review memo.",
            evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
        )


def test_partial_match_requires_material_differences():
    with pytest.raises(ValidationError, match="material_differences"):
        PrecedentAnalysisResult(
            request_id="REQ-1",
            precedent_status=PrecedentStatus.PARTIAL_MATCH,
            validity_status=ValidityStatus.UNKNOWN,
            supporting_source_ids=["SRC-0009"],
            validity_reason="Corpus does not establish present validity.",
            evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT_FOR_CURRENT_CONCLUSION,
        )


def test_invented_source_ids_detected():
    r = PrecedentAnalysisResult(
        request_id="REQ-1",
        precedent_status=PrecedentStatus.MATCH,
        validity_status=ValidityStatus.UNKNOWN,
        supporting_source_ids=["SRC-0009", "SRC-9999"],
        validity_reason="Corpus does not establish present validity.",
        evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
    )
    with pytest.raises(ValueError, match="never retrieved"):
        r.assert_sources_were_retrieved({"SRC-0009"})


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def test_external_required_needs_allowed_provider():
    with pytest.raises(ValidationError, match="allowed_providers"):
        EvidencePolicyDecision(
            external_research_required=True,
            reason="Current tax question with jurisdiction difference.",
        )


def test_providers_forbidden_when_not_required():
    with pytest.raises(ValidationError, match="must be empty"):
        EvidencePolicyDecision(
            external_research_required=False,
            reason="Historical question.",
            allowed_providers=["bloomberg"],
        )


def test_review_action_requires_triggers():
    with pytest.raises(ValidationError, match="review_triggers"):
        ResponseDecision(
            response_action=ResponseAction.ANSWER_WITH_TAX_REVIEW,
            reason="Partial match.",
        )


# ---------------------------------------------------------------------------
# Synthesis + validation
# ---------------------------------------------------------------------------

def _citation(claim_id="C1", origin=EvidenceOrigin.AMSTED_INTERNAL):
    return ClaimCitation(
        claim_id=claim_id,
        claim_text="Amsted previously treated ADS service work in NYC as ...",
        origin=origin,
        source_ids=["SRC-0009"],
    )


def test_synthesis_requires_at_least_one_citation():
    with pytest.raises(ValidationError, match="at least one ClaimCitation"):
        SynthesisResult(
            request_id="REQ-1",
            precedent_status=PrecedentStatus.MATCH,
            validity_status=ValidityStatus.UNKNOWN,
            conclusion="Prior treatment applies.",
        )


def test_no_precedent_cannot_present_historical_guidance():
    with pytest.raises(ValidationError, match="must not present historical_guidance"):
        SynthesisResult(
            request_id="REQ-1",
            precedent_status=PrecedentStatus.NO_PRECEDENT,
            historical_guidance="Amsted concluded ...",
            conclusion="No precedent found.",
            claim_citations=[_citation()],
        )


def test_duplicate_claim_ids_rejected():
    with pytest.raises(ValidationError, match="unique"):
        SynthesisResult(
            request_id="REQ-1",
            precedent_status=PrecedentStatus.MATCH,
            validity_status=ValidityStatus.UNKNOWN,
            conclusion="...",
            claim_citations=[_citation("C1"), _citation("C1")],
        )


def test_internal_and_external_source_ids_stay_separate():
    s = SynthesisResult(
        request_id="REQ-1",
        precedent_status=PrecedentStatus.PARTIAL_MATCH,
        validity_status=ValidityStatus.UNKNOWN,
        material_differences=["Historical source addresses NYC, question is Philadelphia"],
        conclusion="Prior NYC analysis is instructive but not controlling.",
        claim_citations=[
            _citation("C1", EvidenceOrigin.AMSTED_INTERNAL),
            ClaimCitation(
                claim_id="C2",
                claim_text="Pennsylvania local rules provide ...",
                origin=EvidenceOrigin.EXTERNAL_AUTHORITY,
                source_ids=["EXT-PA-001"],
            ),
        ],
    )
    assert s.internal_source_ids == {"SRC-0009"}
    assert s.external_source_ids == {"EXT-PA-001"}


def test_validation_status_must_match_findings():
    with pytest.raises(ValidationError, match="inconsistent"):
        ValidationResult(
            request_id="REQ-1",
            validation_status=ValidationStatus.PASSED,
            invalid_citation_ids=["SRC-9999"],
        )


def test_validation_failure_summary():
    v = ValidationResult(
        request_id="REQ-1",
        validation_status=ValidationStatus.FAILED_INVALID_CITATION,
        invalid_citation_ids=["SRC-9999"],
    )
    assert "SRC-9999" in v.failure_summary()


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------

def test_abstain_must_not_carry_an_answer():
    with pytest.raises(ValidationError, match="must not return an answer"):
        ChatResponse(
            request_id="REQ-1",
            response_action=ResponseAction.ABSTAIN,
            precedent_status=PrecedentStatus.NO_PRECEDENT,
            answer="Here is my best guess ...",
            abstain_reason="No precedent.",
        )


def test_abstain_requires_reason():
    with pytest.raises(ValidationError, match="requires an abstain_reason"):
        ChatResponse(
            request_id="REQ-1",
            response_action=ResponseAction.ABSTAIN,
            precedent_status=PrecedentStatus.NO_PRECEDENT,
        )


def test_needs_review_feedback_requires_structured_reason():
    with pytest.raises(ValidationError, match="structured reason"):
        FeedbackRecord(request_id="REQ-1", feedback_type=FeedbackType.NEEDS_REVIEW)

    ok = FeedbackRecord(
        request_id="REQ-1",
        feedback_type=FeedbackType.NEEDS_REVIEW,
        reasons=[FeedbackReason.WRONG_VALIDITY_STATUS],
    )
    assert ok.created_at is not None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_models_round_trip_json():
    r = PrecedentAnalysisResult(
        request_id="REQ-1",
        precedent_status=PrecedentStatus.PARTIAL_MATCH,
        validity_status=ValidityStatus.UNKNOWN,
        supporting_source_ids=["SRC-0009"],
        material_differences=["Jurisdiction differs: NYC vs Philadelphia"],
        validity_reason="Corpus does not establish present validity.",
        evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT_FOR_CURRENT_CONCLUSION,
    )
    assert PrecedentAnalysisResult.model_validate_json(r.model_dump_json()) == r


def test_extra_fields_forbidden():
    """An LLM returning an unexpected field is a prompt regression, not a
    silently ignorable detail."""
    with pytest.raises(ValidationError):
        PrecedentAnalysisResult(
            request_id="REQ-1",
            precedent_status=PrecedentStatus.NO_PRECEDENT,
            evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
            confidence=0.93,
        )
