"""Unit tests for the Step 4 Query Understanding agent.

No Azure account or live model required -- ``QueryUnderstandingAgent``
depends on the ``ChatModel`` Protocol, exercised here via ``ScriptedChatModel``
and ``SequencedChatModel``.

These tests validate the agent's PLUMBING: request_id injection, prompt
loading, draft validation, error propagation, and the handoff back into
Step 3's jurisdiction guard. They do NOT validate the model's actual
reasoning quality on real questions -- that is what
``scripts/smoke_query_understanding.py`` and
``eval/runners/query_understanding_eval.py`` are for, against a live
deployment.

Run:  pytest runtime/tests/unit/test_query_understanding.py -v
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from runtime.agents.query_understanding import (
    DEFAULT_PROMPT_VERSION,
    PROMPT_PATH,
    QueryUnderstandingAgent,
    QueryUnderstandingDraft,
    _load_default_prompt,
)
from runtime.models.enums import QueryIntent
from runtime.models.query import UserQuery
from runtime.providers.odata import build_filter
from runtime.tests.fakes import ScriptedChatModel, SequencedChatModel


def run(coro):
    return asyncio.run(coro)


def draft(**overrides):
    base = dict(
        intent=QueryIntent.HISTORICAL_PRECEDENT,
        internal_search_query="ADS Transquip income tax nexus",
    )
    base.update(overrides)
    return QueryUnderstandingDraft(**base)


# ===========================================================================
# QueryUnderstandingDraft -- validation rules
# ===========================================================================

def test_clarification_requires_reason():
    with pytest.raises(ValidationError, match="clarification_needed"):
        QueryUnderstandingDraft(
            intent=QueryIntent.CLARIFICATION,
            internal_search_query="q",
        )


def test_non_clarification_forbids_reason():
    with pytest.raises(ValidationError, match="may only be set"):
        QueryUnderstandingDraft(
            intent=QueryIntent.HISTORICAL_PRECEDENT,
            internal_search_query="q",
            clarification_needed="which entity?",
        )


def test_valid_clarification_draft():
    d = QueryUnderstandingDraft(
        intent=QueryIntent.CLARIFICATION,
        internal_search_query="service technicians tax exposure",
        clarification_needed="Which entity and which jurisdiction?",
    )
    assert d.clarification_needed is not None


def test_draft_rejects_blank_search_query():
    with pytest.raises(ValidationError):
        QueryUnderstandingDraft(intent=QueryIntent.HISTORICAL_PRECEDENT,
                                 internal_search_query="")


def test_draft_extra_fields_forbidden():
    """An LLM returning an unexpected field (e.g. inventing a
    precedent_status) is a prompt regression, not a silently ignorable
    detail."""
    with pytest.raises(ValidationError):
        QueryUnderstandingDraft(
            intent=QueryIntent.HISTORICAL_PRECEDENT,
            internal_search_query="q",
            precedent_status="MATCH",
        )


# ===========================================================================
# to_result() -- request_id injection
# ===========================================================================

def test_to_result_injects_request_id():
    d = draft(entities=["ADS"], jurisdictions=["Philadelphia"])
    result = d.to_result(request_id="REQ-ABC123")
    assert result.request_id == "REQ-ABC123"
    assert result.entities == ["ADS"]
    assert result.jurisdictions == ["Philadelphia"]


def test_to_result_preserves_all_fields():
    d = draft(
        topic="State Income Tax",
        subtopic="Nexus",
        entities=["ADS", "Transquip"],
        jurisdictions=["New York City"],
        material_facts=["single service job"],
        external_search_query="NYC nexus single visit",
    )
    result = d.to_result(request_id="REQ-1")
    assert result.topic == "State Income Tax"
    assert result.subtopic == "Nexus"
    assert result.material_facts == ["single service job"]
    assert result.external_search_query == "NYC nexus single visit"


# ===========================================================================
# QueryUnderstandingAgent -- plumbing
# ===========================================================================

def test_agent_returns_result_with_callers_request_id():
    model = ScriptedChatModel(draft(entities=["ADS"]))
    agent = QueryUnderstandingAgent(model, system_prompt="test system prompt")
    query = UserQuery(query="What did we conclude about ADS?")

    result = run(agent.understand(query))

    assert result.request_id == query.request_id
    assert result.entities == ["ADS"]


def test_agent_passes_query_text_as_user_message():
    model = ScriptedChatModel(draft())
    agent = QueryUnderstandingAgent(model, system_prompt="sys")
    query = UserQuery(query="What is our position on Brazilian VAT?")

    run(agent.understand(query))

    assert model.calls == [("sys", "What is our position on Brazilian VAT?")]


def test_agent_loads_default_prompt_when_none_given():
    model = ScriptedChatModel(draft())
    agent = QueryUnderstandingAgent(model)  # no system_prompt override
    run(agent.understand(UserQuery(query="test")))

    system_used = model.calls[0][0]
    assert system_used == _load_default_prompt()
    assert len(system_used) > 500  # sanity: not an empty/stub file


def test_agent_records_prompt_version():
    model = ScriptedChatModel(draft())
    agent = QueryUnderstandingAgent(model, system_prompt="sys", prompt_version="v2-experimental")
    assert agent.prompt_version == "v2-experimental"


def test_agent_default_prompt_version_constant():
    model = ScriptedChatModel(draft())
    agent = QueryUnderstandingAgent(model, system_prompt="sys")
    assert agent.prompt_version == DEFAULT_PROMPT_VERSION


def test_agent_propagates_model_refusal():
    """A refusal or malformed response must surface as an error, not be
    silently swallowed into a default result."""
    model = ScriptedChatModel(ValueError("Model refused to answer: policy"))
    agent = QueryUnderstandingAgent(model, system_prompt="sys")

    with pytest.raises(ValueError, match="refused"):
        run(agent.understand(UserQuery(query="anything")))


def test_agent_propagates_validation_error_from_bad_draft():
    """If the fake (standing in for a real model) is scripted with a broken
    draft, the agent must not mask it."""
    with pytest.raises(ValidationError):
        # constructing the bad draft itself raises -- this documents that
        # QueryUnderstandingDraft's own validators are the enforcement point,
        # not something the agent has to re-check.
        draft(intent=QueryIntent.CLARIFICATION)  # missing clarification_needed


def test_sequenced_chat_model_serves_successive_questions():
    model = SequencedChatModel([
        draft(intent=QueryIntent.HISTORICAL_PRECEDENT),
        draft(intent=QueryIntent.CURRENT_TAX_QUESTION),
    ])
    agent = QueryUnderstandingAgent(model, system_prompt="sys")

    r1 = run(agent.understand(UserQuery(query="q1")))
    r2 = run(agent.understand(UserQuery(query="q2")))

    assert r1.intent == QueryIntent.HISTORICAL_PRECEDENT
    assert r2.intent == QueryIntent.CURRENT_TAX_QUESTION
    assert len(model.calls) == 2


# ===========================================================================
# Prompt file sanity checks
# ===========================================================================

def test_prompt_file_exists():
    assert PROMPT_PATH.exists(), (
        f"Expected prompt file at {PROMPT_PATH}. If you moved prompts, "
        "update PROMPT_PATH in query_understanding.py to match."
    )


def test_prompt_states_the_hard_constraints():
    """Guards against someone editing the prompt and accidentally dropping
    the boundary instructions."""
    text = _load_default_prompt().lower()
    assert "do not answer the tax question" in text
    assert "do not classify precedent" in text
    assert "do not invent facts" in text


def test_prompt_documents_jurisdiction_is_boost_only():
    text = _load_default_prompt().lower()
    assert "boost" in text
    assert "never a hard filter" in text or "never used to hard-filter" in text


# ===========================================================================
# Full loop back into Step 3's jurisdiction guard
# ===========================================================================

def test_philadelphia_understanding_still_routes_to_boost_only():
    """Closes the loop from Step 4 back into the Step 3 guard: whatever the
    agent extracts for jurisdictions must still be structurally incapable of
    becoming a hard filter once passed to SearchFilters."""
    model = ScriptedChatModel(draft(
        intent=QueryIntent.CURRENT_TAX_QUESTION,
        entities=["ADS"],
        jurisdictions=["Philadelphia"],
        internal_search_query="ADS service job Philadelphia nexus exposure",
    ))
    agent = QueryUnderstandingAgent(model, system_prompt="sys")

    result = run(agent.understand(UserQuery(query="Can ADS work in Philadelphia?")))
    filters = result.to_filters()
    odata = build_filter(filters)

    assert filters.boost_jurisdictions == ["Philadelphia"]
    assert odata is not None
    assert "Philadelphia" not in odata  # never leaks into the hard filter


def test_clarification_case_end_to_end():
    model = ScriptedChatModel(draft(
        intent=QueryIntent.CLARIFICATION,
        internal_search_query="service technicians tax exposure",
        clarification_needed="Which entity and which jurisdiction?",
    ))
    agent = QueryUnderstandingAgent(model, system_prompt="sys")

    result = run(agent.understand(UserQuery(query="Do we have a problem with the service techs?")))

    assert result.intent == QueryIntent.CLARIFICATION
    assert result.clarification_needed is not None
    assert "entity" in result.clarification_needed.lower()
