#!/usr/bin/env python
"""Query Understanding evaluation -- Step 4 exit metric.

Runs every question in the precedent gold set through the live
QueryUnderstandingAgent and scores the returned ``intent`` against
``expected_intent``, which is already populated on all 25 cases from the
precedent gold set built earlier. This is the Step 4 analogue of Step 3's
Hit@3: a single accuracy number to record as the baseline and re-check after
any prompt change.

Requires a live Azure OpenAI deployment (QUERY_MODEL_DEPLOYMENT). There is no
offline mode for this runner by design -- scoring intent classification
against fakes would only tell you the plumbing works (already covered by
runtime/tests/unit/test_query_understanding.py), not whether the prompt
reasons correctly on real questions.

Usage
-----
    python eval/runners/query_understanding_eval.py
    python eval/runners/query_understanding_eval.py --case GS-P-001
    python eval/runners/query_understanding_eval.py --json eval/reports/qu_baseline.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.agents.query_understanding import (  # noqa: E402
    AzureOpenAIChatModel,
    QueryUnderstandingAgent,
)
from runtime.config.settings import get_settings  # noqa: E402
from runtime.models.query import UserQuery  # noqa: E402

GOLD_SET_PATH = Path(__file__).resolve().parents[1] / "gold_set" / "precedent_gold_set.yaml"

OK = "\033[92mPASS\033[0m"
BAD = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"


@dataclass
class CaseResult:
    test_id: str
    question: str
    expected_intent: str | None
    actual_intent: str | None = None
    error: str | None = None
    extraction: dict[str, Any] = field(default_factory=dict)

    @property
    def scored(self) -> bool:
        return self.expected_intent is not None

    @property
    def correct(self) -> bool:
        return self.scored and self.actual_intent == self.expected_intent

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "expected_intent": self.expected_intent,
            "actual_intent": self.actual_intent,
            "correct": self.correct if self.scored else None,
            "error": self.error,
            "extraction": self.extraction,
        }


def load_cases(path: Path, only: str | None) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = data["cases"]
    if only:
        cases = [c for c in cases if c["test_id"] == only]
    return cases


async def run_case(agent: QueryUnderstandingAgent, case: dict[str, Any]) -> CaseResult:
    question = " ".join(case["question"].split())
    result = CaseResult(
        test_id=case["test_id"],
        question=question,
        expected_intent=case.get("expected_intent"),
    )
    try:
        qu = await agent.understand(UserQuery(query=question))
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
        return result

    result.actual_intent = qu.intent.value if hasattr(qu.intent, "value") else qu.intent
    result.extraction = {
        "topic": qu.topic,
        "entities": qu.entities,
        "jurisdictions": qu.jurisdictions,
        "internal_search_query": qu.internal_search_query,
        "clarification_needed": qu.clarification_needed,
    }
    return result


def print_report(results: list[CaseResult]) -> dict[str, Any]:
    scored = [r for r in results if r.scored and not r.error]
    errored = [r for r in results if r.error]

    print(f"\n{'=' * 78}")
    print("QUERY UNDERSTANDING EVALUATION")
    print("=" * 78)

    for r in results:
        if r.error:
            print(f"  [{BAD}] {r.test_id}  ERROR: {r.error[:60]}")
        elif not r.scored:
            print(f"  [{SKIP}] {r.test_id}  no expected_intent; got {r.actual_intent}")
        elif r.correct:
            print(f"  [{OK}] {r.test_id}  {r.actual_intent}")
        else:
            print(f"  [{BAD}] {r.test_id}  expected {r.expected_intent}, got {r.actual_intent}")
        print(f"         {r.question[:74]}")

    accuracy = sum(r.correct for r in scored) / len(scored) if scored else 0.0

    confusion: Counter = Counter()
    for r in scored:
        if not r.correct:
            confusion[(r.expected_intent, r.actual_intent)] += 1

    print(f"\n{'-' * 78}")
    print(f"  Scored cases : {len(scored)} of {len(results)}")
    print(f"  Intent accuracy: {accuracy:.1%}   <-- Step 4 baseline")
    if errored:
        print(f"  Errors       : {len(errored)}")
    if confusion:
        print("\n  Misclassifications (expected -> actual):")
        for (exp, act), n in confusion.most_common():
            print(f"    {exp:>22} -> {act:<22}  x{n}")
    print("-" * 78)

    critical_ids = {"GS-P-001", "GS-N-002", "GS-N-004", "GS-K-001", "GS-Q-001"}
    critical = [r for r in results if r.test_id in critical_ids]
    if critical:
        print("\n  Critical cases:")
        for r in critical:
            mark = OK if r.correct else (BAD if r.scored else SKIP)
            print(f"    [{mark}] {r.test_id}: expected={r.expected_intent} actual={r.actual_intent}")

    return {
        "scored_cases": len(scored),
        "total_cases": len(results),
        "intent_accuracy": round(accuracy, 4),
        "errors": len(errored),
        "confusion": {f"{e}->{a}": n for (e, a), n in confusion.items()},
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gold-set", type=Path, default=GOLD_SET_PATH)
    ap.add_argument("--case", help="Run a single test_id")
    ap.add_argument("--json", type=Path, help="Write a JSON report")
    args = ap.parse_args()

    settings = get_settings()
    cases = load_cases(args.gold_set, args.case)
    if args.case and not cases:
        print(f"No case {args.case!r}")
        return 1

    chat_model = AzureOpenAIChatModel(
        endpoint=settings.azure_openai_endpoint,
        deployment=settings.query_model_deployment,
        api_version=settings.azure_openai_api_version,
        api_key=settings.azure_openai_api_key,
    )
    agent = QueryUnderstandingAgent(chat_model)

    try:
        results = [await run_case(agent, c) for c in cases]
    finally:
        await chat_model.aclose()

    metrics = print_report(results)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "prompt_version": agent.prompt_version,
            "model_deployment": settings.query_model_deployment,
            "metrics": metrics,
            "cases": [r.to_dict() for r in results],
        }
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\n  report written: {args.json}")

    return 0 if metrics["intent_accuracy"] >= 0.8 or metrics["scored_cases"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
