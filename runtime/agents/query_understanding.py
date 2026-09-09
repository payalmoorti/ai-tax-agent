"""Query Understanding Agent.

First LLM-backed component in the workflow. Turns a raw user question into a
structured, typed reading: intent classification, topic/entity/jurisdiction
extraction, and a rewritten search query for retrieval.

Hard constraints, enforced by the prompt AND by validators on the output:
  - Must NOT answer the tax question.
  - Must NOT classify precedent (that is Step 5's job, after retrieval).
  - Must NOT invent facts the user did not state or clearly imply.

Jurisdiction extraction here is purely descriptive. Step 3 already guarantees
extracted jurisdictions are never used to hard-filter retrieval -- this
agent's only job is to name them accurately so ``to_filters()`` can route
them to boost.

Uses a small/fast model deployment (``Settings.query_model_deployment``) --
this step does not require the reasoning-tier model.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, Field, field_validator, model_validator, AliasChoices

from runtime.models.base import RuntimeModel
from runtime.models.enums import QueryIntent
from runtime.models.query import QueryUnderstandingResult, UserQuery

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "query_understanding_system.md"
DEFAULT_PROMPT_VERSION = "query_understanding_v1"

T = TypeVar("T", bound=BaseModel)


@lru_cache
def _load_default_prompt() -> str:
    """Cached so the file is read once per process, not once per request."""
    return PROMPT_PATH.read_text(encoding="utf-8")


@runtime_checkable
class ChatModel(Protocol):
    """What the agent depends on. Fakeable in tests via ``ScriptedChatModel``
    / ``SequencedChatModel`` in ``runtime.tests.fakes``."""

    async def complete_structured(
        self, system: str, user: str, response_model: type[T]
    ) -> T:
        ...


class QueryUnderstandingDraft(RuntimeModel):
    """What the LLM actually produces.

    Deliberately excludes ``request_id`` -- that is assigned by the caller
    (from ``UserQuery``), not decided by the model. Keeping this as a
    separate type from ``QueryUnderstandingResult`` means the "what the
    model said" and "the typed contract used by the rest of the workflow"
    concerns never get tangled, and no placeholder request_id can leak into
    a real result.
    """

    intent: QueryIntent
    topic: str | None = None
    subtopic: str | None = None
    entities: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("entities", "entity"),
    )
    jurisdictions: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("jurisdictions", "jurisdiction"),
    )
    material_facts: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("material_facts", "facts"),
    )
    internal_search_query: str = Field(min_length=1)
    external_search_query: str | None = None
    clarification_needed: str | None = None

    @field_validator("entities", "jurisdictions", "material_facts", mode="before")
    @classmethod
    def _coerce_list_field(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            cleaned = value.strip()
            return [cleaned] if cleaned else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return value

    @model_validator(mode="after")
    def _clarification_consistency(self) -> "QueryUnderstandingDraft":
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

    def to_result(self, request_id: str) -> QueryUnderstandingResult:
        """Inject the caller-assigned request_id and produce the typed
        contract the rest of the workflow consumes."""
        return QueryUnderstandingResult(
            request_id=request_id,
            intent=self.intent,
            topic=self.topic,
            subtopic=self.subtopic,
            entities=self.entities,
            jurisdictions=self.jurisdictions,
            material_facts=self.material_facts,
            internal_search_query=self.internal_search_query,
            external_search_query=self.external_search_query,
            clarification_needed=self.clarification_needed,
        )


class QueryUnderstandingAgent:
    """Wraps a ``ChatModel`` to produce ``QueryUnderstandingResult``.

    Parameters
    ----------
    chat_model:
        Anything satisfying ``ChatModel`` -- normally ``AzureOpenAIChatModel``.
    system_prompt:
        Overrides the default prompt loaded from
        ``prompts/query_understanding_system.md``. Mainly useful for
        prompt-version experiments in eval; leave ``None`` in production.
    prompt_version:
        Recorded in logs/telemetry only -- not carried on the result itself.
        Bump this whenever ``system_prompt`` content changes materially, so
        eval runs and traces can be attributed to a specific prompt version.
    """

    def __init__(
        self,
        chat_model: ChatModel,
        system_prompt: str | None = None,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
    ) -> None:
        self._chat_model = chat_model
        self._system_prompt = system_prompt or _load_default_prompt()
        self._prompt_version = prompt_version

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    async def understand(self, query: UserQuery) -> QueryUnderstandingResult:
        """Classify intent and extract structure from a raw question.

        Never silently downgrades a malformed model response -- a
        ``ValidationError`` here means the model returned a structurally
        broken draft (e.g. ``CLARIFICATION`` without a reason) and should be
        treated as a prompt regression to fix, not routed to the user as-is.
        """
        draft = await self._chat_model.complete_structured(
            system=self._system_prompt,
            user=query.query,
            response_model=QueryUnderstandingDraft,
        )
        result = draft.to_result(request_id=query.request_id)
        logger.debug(
            "query understanding: request_id=%s intent=%s prompt_version=%s",
            query.request_id,
            result.intent,
            self._prompt_version,
        )
        return result


class AzureOpenAIChatModel:
    """Structured-output chat completion via a Foundry-hosted Azure OpenAI
    deployment.

    Uses the SDK's Pydantic-native structured output support (``.parse()``)
    so the model's JSON is validated directly into the target type -- no
    manual ``json.loads`` / ``model_validate`` step, and a malformed response
    raises before it ever reaches the agent.

    The SDK is imported lazily inside ``_client`` so this module can be
    imported, and ``ChatModel`` used as a Protocol in tests, without the
    ``openai`` package installed.
    """

    def __init__(
        self,
        endpoint: str,
        deployment: str,
        api_version: str = "2024-10-21",
        api_key: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        self._endpoint = endpoint
        self._deployment = deployment
        self._api_version = api_version
        self._api_key = api_key
        self._temperature = temperature
        self._cached_client: Any = None

    def _client(self) -> Any:
        if self._cached_client is not None:
            return self._cached_client

        from openai import AsyncAzureOpenAI

        if self._api_key:
            client = AsyncAzureOpenAI(
                azure_endpoint=self._endpoint,
                api_key=self._api_key,
                api_version=self._api_version,
            )
        else:
            from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

            token_provider = get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            )
            client = AsyncAzureOpenAI(
                azure_endpoint=self._endpoint,
                azure_ad_token_provider=token_provider,
                api_version=self._api_version,
            )

        self._cached_client = client
        return client

    async def complete_structured(
        self, system: str, user: str, response_model: type[T]
    ) -> T:
        """Call the deployment and parse the response directly into
        ``response_model``.

        Note: uses ``beta.chat.completions.parse``, the documented structured
        -output entry point as of ``openai>=1.40``. If your installed SDK has
        promoted this out of beta, switch to ``chat.completions.parse`` --
        the call shape is otherwise identical.
        """
        completion = await self._client().beta.chat.completions.parse(
            model=self._deployment,
            temperature=self._temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=response_model,
        )
        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise ValueError(f"Model refused to answer: {message.refusal}")
        parsed = message.parsed
        if parsed is None:
            raise ValueError(
                "Model returned no parsed structured output. This usually "
                "means the response didn't validate against the schema -- "
                "check the raw completion for details."
            )
        return parsed

    async def aclose(self) -> None:
        if self._cached_client is not None:
            await self._cached_client.close()
            self._cached_client = None
