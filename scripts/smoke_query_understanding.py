#!/usr/bin/env python
"""Query Understanding smoke test against a live Azure OpenAI deployment.

Run this once ``QUERY_MODEL_DEPLOYMENT`` is configured, before trusting the
agent against the full gold set. Answers, in order:

  1. Can we authenticate to the query model deployment?
  2. Does the model return a schema-valid structured response at all?
  3. For a handful of hand-picked questions, does the classification and
     extraction look reasonable?

Usage
-----
    python scripts/smoke_query_understanding.py --check-auth-only
    python scripts/smoke_query_understanding.py -q "What did we conclude about ADS?"
    python scripts/smoke_query_understanding.py --suite     # runs 5 built-in questions
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.agents.query_understanding import AzureOpenAIChatModel, QueryUnderstandingAgent  # noqa: E402
from runtime.config.settings import get_settings  # noqa: E402
from runtime.models.query import UserQuery  # noqa: E402

OK = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"

BUILT_IN_SUITE = [
    "What concerns did Amsted identify about ADS sending Transquip service "
    "technicians into a major city with an income tax?",
    "Can ADS perform a service job in Philadelphia without creating broader "
    "tax exposure for Amsted?",
    "What is our position on Brazilian VAT on intercompany software licences?",
    "Do we have a problem with the service techs?",
    "We concluded Illinois nexus in 2021 based on P.L. 86-272. Is that still "
    "consistent with current Illinois guidance?",
]


def banner(text: str) -> None:
    print(f"\n{'=' * 72}\n{text}\n{'=' * 72}")


#this function checks if the user has authenticated to Azure OpenAI using either an API key or DefaultAzureCredential. It prints the authentication status and returns True if successful, False otherwise.
async def check_auth(settings) -> bool:
    banner("1. Authentication")

    if not settings.use_managed_identity:
        print("  mode: API key (local dev only)")
        if not settings.azure_openai_api_key:
            print(f"  {FAIL} AZURE_OPENAI_API_KEY is not set in .env")
            return False
        print(f"  {OK} API key present — will authenticate via key on first request")
        print("  (skipping DefaultAzureCredential check — not used in API-key mode)")
        return True

    print("  mode: DefaultAzureCredential (az login / Managed Identity)")
    try:
        from azure.identity.aio import DefaultAzureCredential

        cred = DefaultAzureCredential()
        await cred.get_token("https://cognitiveservices.azure.com/.default")
        print(f"  {OK} token acquired: Azure OpenAI")
        await cred.close()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  {FAIL} token FAILED: {exc}")
        print("\n  Fix: az login, and confirm 'Cognitive Services OpenAI User' "
              "role is assigned on the Foundry/OpenAI resource.")
        return False


async def run_one(agent: QueryUnderstandingAgent, question: str) -> None:
    query = UserQuery(query=question)
    try:
        result = await agent.understand(query)
    except Exception as exc:  # noqa: BLE001
        print(f"\n  {FAIL} {question[:70]}")
        print(f"      ERROR: {exc}")
        return

    print(f"\n  {OK} {question[:70]}")
    print(f"      intent:              {result.intent.value if hasattr(result.intent, 'value') else result.intent}")
    print(f"      topic / subtopic:    {result.topic} / {result.subtopic}")
    print(f"      entities:            {result.entities}")
    print(f"      jurisdictions:       {result.jurisdictions}")
    print(f"      material_facts:      {result.material_facts}")
    print(f"      internal_search_query: {result.internal_search_query}")
    if result.external_search_query:
        print(f"      external_search_query: {result.external_search_query}")
    if result.clarification_needed:
        print(f"      clarification_needed:  {result.clarification_needed}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", "-q")
    ap.add_argument("--suite", action="store_true", help="Run the 5 built-in test questions")
    ap.add_argument("--check-auth-only", action="store_true")
    args = ap.parse_args()

    settings = get_settings()
    print(f"environment: {settings.environment}")
    print(f"query model deployment: {settings.query_model_deployment or '(not set)'}")

    if not await check_auth(settings):
        return 1
    if args.check_auth_only:
        return 0

    if not args.query and not args.suite:
        ap.error("--query or --suite is required unless --check-auth-only")

    chat_model = AzureOpenAIChatModel(
        endpoint=settings.azure_openai_endpoint,
        deployment=settings.query_model_deployment,
        api_version=settings.azure_openai_api_version,
        api_key=settings.azure_openai_api_key,
    )
    agent = QueryUnderstandingAgent(chat_model)

    banner("2-3. Retrieval understanding")
    questions = BUILT_IN_SUITE if args.suite else [args.query]
    try:
        for q in questions:
            await run_one(agent, q)
    finally:
        await chat_model.aclose()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
