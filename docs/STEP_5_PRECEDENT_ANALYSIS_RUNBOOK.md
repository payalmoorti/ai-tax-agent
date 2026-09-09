# Step 5 — Precedent Analysis Agent (CORE) — Milestone 2

**Goal:** Given retrieved evidence, classify how it relates to the current
question and whether it's still valid.
**Exit criteria:** Schema-valid output · every classification names
supporting source IDs · every `PARTIAL_MATCH` lists material differences ·
every validity call carries a rationale · **False CURRENT rate = 0%.**

This is the core reasoning component and where most of your prompt-iteration
time goes. Uses the **strong reasoning model** deployment
(`PRECEDENT_MODEL_DEPLOYMENT`) — not the small/fast one from Step 4.

---

## Is the model "creating" MATCH / PARTIAL_MATCH / NO_PRECEDENT?

Yes, directly. Unlike Steps 1–3 (deterministic code) and even unlike some of
Step 4's structural extraction, this step's entire output — `precedent_status`
and `validity_status` — **is the LLM's judgment call**, made by reading the
retrieved evidence text and reasoning about whether it matches the question.

What is *not* left to the model's judgment is the **shape** of that judgment:

- It must pick from exactly five `precedent_status`/`validity_status`
  combinations that are structurally valid (enforced by Pydantic validators
  on `PrecedentAnalysisDraft`, not just prompt instructions).
- It must justify `CURRENT` with a reason that names affirmative evidence —
  a reason like "nothing newer was found" is mechanically rejected before it
  ever becomes a result.
- It must only cite `source_id`s that were actually in the evidence it was
  given — a fabricated citation is caught and raises before reaching
  downstream steps.

So: the *classification* is genuinely the model's reasoning. The *guardrails
around* that reasoning — what's structurally possible to output, what
citations are legitimate — are deterministic code, same philosophy as
Steps 1–3.

---

## Files in this step (see MANIFEST.md for exact destination paths)

| File | Role | Needs Azure? |
|---|---|---|
| `precedent_analysis_system.md` | The system prompt — six worked examples grounded in real corpus content | no |
| `precedent_analysis.py` | `PrecedentAnalysisDraft`, `format_evidence_for_prompt`, `PrecedentAnalysisAgent` | lazy (reuses `ChatModel`/`AzureOpenAIChatModel` from Step 4) |
| `test_precedent_analysis.py` | 35 unit tests | no |
| `smoke_precedent_analysis.py` | Offline (canned real-content fixtures) + live smoke test | offline: chat model only · live: everything |
| `precedent_analysis_eval.py` | Full-pipeline gold-set eval, with the False CURRENT rate metric | yes |

**Note:** this step reuses `ChatModel` and `AzureOpenAIChatModel` from
`runtime/agents/query_understanding.py` rather than duplicating them — the
SDK plumbing (structured output, lazy import, Managed Identity vs. API key)
is identical regardless of which prompt/deployment is running. Only the
prompt, deployment name, and response schema differ per agent.

---

## Architecture

```
PrecedentAnalysisAgent
  └── ChatModel (Protocol, from Step 4)  →  AzureOpenAIChatModel
                                                (pointed at PRECEDENT_MODEL_DEPLOYMENT,
                                                 NOT the same deployment as Step 4)

question + QueryUnderstandingResult + InternalEvidencePackage
        │
        ▼ format_evidence_for_prompt()  -- builds the user message
        ▼ agent.analyze()
        │
        ▼ PrecedentAnalysisDraft         (model output, no request_id)
        │
        ▼ .to_result(request_id)         (from QueryUnderstandingResult.request_id)
        │
        ▼ assert_sources_were_retrieved() -- fabricated-citation guard
        │
        ▼ PrecedentAnalysisResult        (typed contract, consumed by Step 6+)
```

Same draft/result split as Step 4, for the same reason: the model never
assigns `request_id` — that's threaded through from the `QueryUnderstandingResult`
that's already carrying it.

---

## The two safety rules, and why they're enforced twice

Both rules are validated **on the draft** (in `precedent_analysis.py`) AND
**on the final result** (in `runtime/models/precedent.py`, from Step 2). This
isn't redundant — it's defense in depth:

1. **Draft-level validation** catches a bad model response immediately, with
   an error message specific to this call ("this LLM call produced a
   false-CURRENT justification").
2. **Result-level validation** (on `PrecedentAnalysisResult`) is the backstop
   that fires no matter how the result was constructed — even if some future
   code path builds one without going through this agent.

### Rule 1 — `NO_PRECEDENT` ⟹ `validity_status = null`

There's no precedent whose validity could be assessed. If the model says
`NO_PRECEDENT` but also tries to assign a validity status or cite sources,
that's rejected.

### Rule 2 — `CURRENT` requires affirmative evidence

This is **the highest-risk failure mode in the system**, tracked as the
**False CURRENT rate**. A `validity_reason` containing phrases like "no newer
source," "nothing suggests it changed," or "not superseded by anything
retrieved" is mechanically blocked — regardless of what `validity_status` the
model assigned. Absence of contradiction is not evidence of currency.

---

## Two reasoning traps the prompt explicitly guards against

Both are drawn from real content in your pilot corpus, not hypothetical:

**Entity mismatch.** Retrieval can surface evidence that's topically
identical but belongs to a different corporate entity (e.g., a different
company's 409(p) synthetic equity analysis when the question is about
Amsted's ESOP). The prompt instructs the model to recognize this and return
`NO_PRECEDENT`, not attribute one entity's conclusions to another. Covered by
`test_prompt_addresses_entity_mismatch_trap` and the smoke script's
`no-precedent` case.

**Explicit scope limitations authored into the source itself.** Some
evidence contains the author's own instruction not to extend a conclusion
(e.g., the real Illinois nexus email in your corpus: *"we are NOT extending
this conclusion to Wisconsin... please do not let anyone cite this thread for
a Wisconsin position"*). The model must treat that as binding —
`PARTIAL_MATCH` at most, never `MATCH`. This is the `wisconsin-trap` case in
the smoke script, built from that real email content.

---

## Run order

### 5.1 Unit tests (no Azure)

```powershell
pytest runtime/tests/unit -v
```

Expect **79 passed** (44 from Steps 3–4 + 35 new).

### 5.2 Auth check

```powershell
python scripts\smoke_precedent_analysis.py --check-auth-only
```

### 5.3 Offline smoke test (no Search index needed)

This is the fastest way to validate prompt quality — it uses real content
from your corpus (the ADS/Transquip NYC email and the Meridian Bearing
Illinois FINAL POSITION email) as canned evidence, so you can test reasoning
quality independent of whether retrieval is fully working.

```powershell
python scripts\smoke_precedent_analysis.py --offline
```

Runs all five built-in cases. Eyeball each one, but pay special attention to:

```powershell
python scripts\smoke_precedent_analysis.py --offline --case wisconsin-trap
```

If this returns `MATCH`/`CURRENT` for Wisconsin instead of `PARTIAL_MATCH`,
the model is ignoring the source's own scope limitation — a prompt issue to
fix before doing anything else.

```powershell
python scripts\smoke_precedent_analysis.py --offline --case philadelphia-partial
```

If this returns `NO_PRECEDENT` instead of `PARTIAL_MATCH`, retrieval-adjacent
reasoning is too conservative — the model isn't recognizing that the NYC
example's reasoning transfers to Philadelphia even though the jurisdiction
differs.

### 5.4 Live smoke test (needs full stack)

```powershell
python scripts\smoke_precedent_analysis.py --live -q "Did Meridian Bearing Company have Illinois income tax nexus for 2019-2021?"
```

Chains real Query Understanding → real retrieval → real Precedent Analysis.

### 5.5 Full gold set evaluation — the real exit metric

```powershell
python eval\runners\precedent_analysis_eval.py --json eval\reports\precedent_baseline.json
```

Reports precedent accuracy, validity accuracy, and — the number that
matters most — the **False CURRENT rate**. This must be 0%. The runner's
exit code reflects this: it fails the run if false-CURRENT count is
non-zero, regardless of overall accuracy.

Run a single case to isolate an issue:

```powershell
python eval\runners\precedent_analysis_eval.py --case GS-P-002
```

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ValidationError: ... false-CURRENT failure mode` at the draft stage | The model justified `CURRENT` by absence — this is caught correctly, not a bug. Reclassify via prompt reinforcement. |
| `ValueError: ... never retrieved` | The model cited a `source_id` not present in the evidence it received — a fabrication, correctly caught. If this happens often, the evidence formatting may not be making `source_id` prominent enough in the prompt. |
| `wisconsin-trap` returns `MATCH` | Model isn't respecting the source's own explicit scope limitation. Reinforce with the worked example already in the prompt, or make the "binding" language more forceful. |
| `no-precedent` (entity mismatch case) returns `MATCH` | Model is pattern-matching on topic ("409(p)", "synthetic equity") rather than checking entity identity. Add a stronger explicit reminder near the top of the response. |
| False CURRENT rate > 0% on the gold set | **Do not ship.** Pull the specific `validity_reason` text the model produced for that case from the JSON report and check whether it's using softer absence-language not yet in `_ABSENCE_REASONING_MARKERS` (e.g., "remains the position" without qualification) — if so, add the phrase to the marker list AND reinforce the prompt. |

---

## What Step 5 does NOT do

- Does not decide whether external research is needed — that's the Evidence
  Policy (Step 6), driven off this result plus `QueryIntent`.
- Does not write the user-facing answer — that's Synthesis (Step 8).
- Does not validate its own citations against a stricter standard than "was
  this source actually retrieved" — deeper claim-level entailment checking
  is the Citation Validator (Step 9), layered on top of this, not a
  replacement for it.

---

## Definition of done

- [ ] `pytest runtime/tests/unit` → 79 passed
- [ ] `smoke_precedent_analysis.py --check-auth-only` → auth confirmed
- [ ] All 5 offline cases produce reasonable classifications, especially
      `wisconsin-trap` and `no-precedent`
- [ ] Live smoke test runs the full chain successfully
- [ ] Full gold set eval run, metrics recorded
- [ ] **False CURRENT rate = 0%**

Then proceed to Step 6 — Evidence Policy + Response Policy (deterministic
code, no LLM), which consumes this result together with `QueryIntent` to
decide whether external research is required and what the terminal response
action should be.
