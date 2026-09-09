# Step 4 — Query Understanding Agent (CORE)

**Goal:** Turn a raw question into structured intent + extraction, using a
small/fast model.
**Exit criteria:** Historical / current / mixed / ambiguous examples classify
correctly · output validates against `QueryUnderstandingResult` · intent
accuracy measured against the gold set.

This is the first LLM-backed component. Steps 1–3 (environment, contracts,
retrieval) were all deterministic code — this is where prompt iteration time
starts.

---

## 1. Files in this step

| File | Role | Needs Azure? |
|------|------|-------------|
| `runtime/agents/prompts/query_understanding_system.md` | The system prompt | no |
| `runtime/agents/query_understanding.py` | `QueryUnderstandingDraft`, `ChatModel` protocol, `AzureOpenAIChatModel`, `QueryUnderstandingAgent` | lazy |
| `runtime/tests/fakes.py` (additions) | `ScriptedChatModel`, `SequencedChatModel` | no |
| `runtime/tests/unit/test_query_understanding.py` | 20 unit tests | no |
| `scripts/smoke_query_understanding.py` | Live deployment smoke test | yes |
| `eval/runners/query_understanding_eval.py` | Intent-accuracy runner against the gold set | yes |

**Note on `fakes.py`:** this file already existed from Step 3. Two classes
were *appended* to it — `ScriptedChatModel` and `SequencedChatModel` — nothing
existing was changed. If you're merging by hand, just add the new classes at
the end of your current `fakes.py`.

---

## 2. Architecture

```
QueryUnderstandingAgent
  └── ChatModel (Protocol)  →  AzureOpenAIChatModel
                                   └── openai .beta.chat.completions.parse()

UserQuery ──► agent.understand() ──► QueryUnderstandingDraft ──► QueryUnderstandingResult
                                           (model output,            (typed contract,
                                            no request_id)            request_id injected)
```

**Why the draft/result split:** the LLM should never decide `request_id` —
that's assigned by the caller from `UserQuery`. Keeping `QueryUnderstandingDraft`
as a separate type from `QueryUnderstandingResult` means "what the model said"
and "the typed contract the rest of the workflow consumes" never get tangled,
and no placeholder ID can leak into a real result.

**Why `ChatModel` is a Protocol:** identical reasoning to `SearchBackend` and
`EmbeddingProvider` in Step 3 — the entire agent is unit-testable with
`ScriptedChatModel` and no Azure account, and the SDK is imported lazily
inside `AzureOpenAIChatModel._client()`.

---

## 3. The two enforced rules

Both are validators on `QueryUnderstandingDraft`, not just prompt instructions:

1. **`intent = CLARIFICATION` ⟹ `clarification_needed` is set.** You can't
   ask for clarification without saying what's missing.
2. **`clarification_needed` set ⟹ `intent = CLARIFICATION`.** The reverse
   also can't happen — you can't populate the reason field on a
   non-clarification response.

`test_prompt_states_the_hard_constraints` and
`test_prompt_documents_jurisdiction_is_boost_only` additionally guard the
*prompt file itself* — if someone edits the prompt and accidentally drops the
"do not classify precedent" instruction, that test fails immediately, before
you'd notice it from a subtly wrong live response.

---

## 4. Run order

### 4.1 Unit tests (no Azure)

```powershell
pytest runtime/tests/unit/test_query_understanding.py -v
```
pytest runtime/tests/unit -v

Expect **44 passed** (24 from Step 3 + 20 new). If you see fewer, check that
`ScriptedChatModel` and `SequencedChatModel` were added to `fakes.py`.

### 4.2 Auth check

```powershell
python scripts/smoke_query_understanding.py --check-auth-only
```

Confirms `DefaultAzureCredential` can reach Azure OpenAI. Requires
`QUERY_MODEL_DEPLOYMENT` set in `.env` — this should be your **small/fast**
deployment (e.g. `gpt-4o-mini`), not the reasoning-tier model used later for
precedent analysis.

### 4.3 First live question

```powershell
python scripts/smoke_query_understanding.py -q "What did we conclude about ADS?"
python scripts/smoke_query_understanding.py --suite
```

`--suite` runs 5 built-in questions spanning all five intents, mirroring the
worked examples in the prompt file. Eyeball the extraction — does
`internal_search_query` look like something that would retrieve well in Step 3?

### 4.4 Full gold set intent accuracy

```powershell
python eval/runners/query_understanding_eval.py --json eval/reports/qu_baseline.json
```

Scores every case in `precedent_gold_set.yaml` against its `expected_intent`
(already populated on all 25 cases). **Record the intent accuracy number** —
that's the Step 4 exit metric, same role Hit@3 played for Step 3.

The runner also calls out five **critical cases** by ID: `GS-P-001`
(Philadelphia — must be `CURRENT_TAX_QUESTION`, not `HISTORICAL_PRECEDENT`,
or the jurisdiction-difference reasoning downstream never triggers),
`GS-N-002` / `GS-N-004` (cross-universe traps — intent classification alone
won't catch these, but a wrong intent here compounds the risk at Step 5),
`GS-K-001` (the false-CURRENT guard question), and `GS-Q-001`
(`CLARIFICATION`).

---

## 5. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Model returned no parsed structured output` | Response didn't validate against `QueryUnderstandingDraft` — check for a `beta.chat.completions.parse` vs `chat.completions.parse` SDK version mismatch |
| Every question classifies as the same intent | Prompt drift, or wrong deployment (verify it's not accidentally pointed at the precedent/synthesis deployment) |
| `CLARIFICATION` used too often | Model is being conservative; tighten the "reserve this for genuinely ambiguous questions" instruction in the prompt |
| Jurisdiction extracted but downstream retrieval hard-filters anyway | That would be a **Step 3 regression**, not Step 4 — re-run `test_philadelphia_question_retrieves_nyc_evidence` |
| GS-P-001 classifies as `HISTORICAL_PRECEDENT` instead of `CURRENT_TAX_QUESTION` | Model is keying on "ADS" appearing in a known historical thread rather than the forward-looking framing ("can ADS perform..."). Reinforce the tense/framing signal in the prompt. |

---

## 6. What Step 4 does NOT do

- Does not call retrieval. `search_for_understanding()` (Step 3) is the next
  wire-up, not part of this agent.
- Does not classify precedent, validity, or evidence sufficiency. Those are
  Step 5, after retrieval has actually run.
- Does not decide external research is required — that's the Evidence Policy
  in Step 6, driven by `EXTERNAL_RESEARCH_ELIGIBLE_INTENTS` on the *result* of
  this step, not decided here.

---

## 7. Definition of done

- [ ] `pytest runtime/tests/unit` → 44 passed
- [ ] `smoke_query_understanding.py --check-auth-only` → token acquired
- [ ] `--suite` output looks reasonable across all 5 intents
- [ ] `query_understanding_eval.py` run, intent accuracy recorded
- [ ] GS-P-001 and GS-Q-001 classify correctly (the two hardest cases)

Then proceed to Step 5 — Precedent Analysis Agent, which consumes this
result together with the `InternalEvidencePackage` from Step 3.
