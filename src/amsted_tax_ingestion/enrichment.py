"""LLM metadata enrichment for the Seed Corpus v0.1 schema.

Populates the semantic fields the source cannot supply deterministically:
    tax_topic, jurisdiction, legal_entity, business_unit,
    tax_year_or_effective_period, authority_level, scenario_id

Deterministic fields set during normalization (source_file, source_type,
source_date, author, message_date, page_number, thread_subject, source_id) are
NOT overwritten.

Governance fields (validity_status, current_or_superseded, access_classification)
are deliberately left at "n/a" — Amsted has not supplied the classification
scheme yet. The prompt does not ask for them, and the enricher will not invent them.

NOTE: a previous revision of this file had a SECOND, older MetadataEnricher class
appended below this one. Python keeps the last definition, so the old class won.
It imported EnrichmentMetadata (which no longer exists in models.py), so the module
raised ImportError and took the whole pipeline down. Keep exactly one class here.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from openai import AzureOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from .models import (
    AUTHORITY_LEVELS,
    JURISDICTIONS,
    NA,
    TAX_TOPICS,
    NormalizedDocument,
)

log = logging.getLogger(__name__)

SYSTEM = f"""You are a tax knowledge classifier for an enterprise tax research corpus.

You are given a tax source document — a memo, an email thread, research notes,
agency guidance, or a statute excerpt. Classify it and return metadata ONLY as JSON.

Rules:
- Use ONLY the allowed values for jurisdiction, tax_topic, and authority_level.
- If the document does not support a confident classification, return "n/a".
  Do NOT guess. Do NOT invent facts that are not in the document.
- legal_entity: the specific legal entity the document concerns (e.g.
  "Amsted Rail Company, Inc."). Return "n/a" if no entity is named.
- business_unit: the operating segment or department (e.g. "Corporate Tax",
  "Amsted Rail"). Return "n/a" if not stated.
- tax_year_or_effective_period: the tax year or effective period the guidance
  applies to (e.g. "2024", "2023-2025", "effective 2024-01-01"). Return "n/a"
  if not stated.
- scenario_id: a stable, reusable slug describing the business tax scenario,
  UPPER_SNAKE_CASE, max 6 words (e.g. STATE_NEXUS_REMOTE_EMPLOYEES).
- confidence: float 0.0-1.0 for your overall certainty.
- rationale: ONE short sentence citing the specific evidence you relied on.

JURISDICTION — return the jurisdiction whose tax is AT ISSUE, not every
jurisdiction the document mentions.

Apply these in order and stop at the first that fits:

1. Exposure analyzed across two or more US states, cities, or localities
   -> "Multistate", even if one is named more often than the others.
2. Exposure analyzed across two or more countries -> "Multinational".
3. A single US state or locality at issue -> that state. A named city maps to
   its state (New York City -> New York; Chicago -> Illinois).
4. A single foreign country at issue -> that country.
5. "Federal" ONLY when US federal tax is itself the subject of the analysis.
   Federal status mentioned as background — S corporation, ESOP, consolidated
   return, check-the-box, entity classification — does NOT make a document
   Federal when the tax being analyzed is state, local, or foreign.
6. Otherwise "n/a".

Deciding test: which taxing authority could actually assess tax here, or whose
law answers the question? In `rationale`, quote the specific phrase from the
document that drove your choice.

Allowed jurisdiction: {json.dumps(JURISDICTIONS)}
Allowed tax_topic: {json.dumps(TAX_TOPICS)}
Allowed authority_level: {json.dumps(AUTHORITY_LEVELS)}

Return exactly:
{{"jurisdiction": str, "tax_topic": str, "legal_entity": str, "business_unit": str,
  "tax_year_or_effective_period": str, "authority_level": str, "scenario_id": str,
  "confidence": number, "rationale": str}}"""


def _coerce(value, allowed: list[str], fallback: str = NA) -> str:
    """Snap the model's answer onto the controlled vocabulary."""
    if not value:
        return fallback
    text = str(value).strip()
    if text.lower() in {"n/a", "na", "none", "unknown", "null", ""}:
        return fallback
    lookup = {a.lower(): a for a in allowed}
    if text.lower() in lookup:
        return lookup[text.lower()]
    for allowed_value in allowed:
        if allowed_value.lower() in text.lower() or text.lower() in allowed_value.lower():
            return allowed_value
    return fallback


def _free_text(value) -> str:
    if value is None:
        return NA
    text = str(value).strip()
    if not text or text.lower() in {"n/a", "na", "none", "unknown", "null"}:
        return NA
    return text[:200]


def _scenario_id(value) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "")).strip("_").upper()
    parts = [p for p in slug.split("_") if p][:6]
    return "_".join(parts) or NA


def _confidence(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


class MetadataEnricher:
    def __init__(self, settings, max_chars: int = 12000):
        settings.require(
            "azure_openai_endpoint", "azure_openai_api_key", "chat_deployment_name"
        )
        self.deployment = settings.chat_deployment_name
        self.max_chars = max_chars
        self.client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
    def _call(self, content: str) -> str:
        response = self.client.chat.completions.create(
            model=self.deployment,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": content},
            ],
        )
        return response.choices[0].message.content or "{}"

    def _user_prompt(self, doc: NormalizedDocument) -> str:
        header = [
            f"SOURCE FILE: {doc.source_file_name}",
            f"SOURCE TYPE: {doc.metadata.source_type}",
        ]
        if doc.is_email_thread:
            header.append(f"THREAD SUBJECT: {doc.metadata.thread_subject}")
            header.append(f"MESSAGE COUNT: {len(doc.messages)}")
            participants = sorted({m.sender for m in doc.messages if m.sender != NA})
            if participants:
                header.append(f"PARTICIPANTS: {'; '.join(participants[:10])}")
        return "\n".join(header) + f"\n\nCONTENT:\n---\n{doc.content[:self.max_chars]}\n---"

    def enrich(self, doc: NormalizedDocument) -> NormalizedDocument:
        timestamp = datetime.now(timezone.utc).isoformat()

        try:
            data = json.loads(self._call(self._user_prompt(doc)))
            if not isinstance(data, dict):
                raise ValueError(f"Expected a JSON object, got {type(data).__name__}")
        except Exception as exc:  # noqa: BLE001 — enrichment must not kill the run
            log.warning("Enrichment FAILED for %s: %s", doc.source_file_name, exc)
            doc.metadata.enrichment_status = "failed"
            doc.metadata.enrichment_rationale = f"Enrichment failed: {exc}"
            doc.metadata.enrichment_model = self.deployment
            doc.metadata.enrichment_timestamp = timestamp
            return doc

        # Corpus-assigned scenario IDs always win over the model's guess.
        scenario_id = doc.metadata.scenario_id
        if scenario_id == NA:
            scenario_id = _scenario_id(data.get("scenario_id"))

        # Prefer a deterministically parsed tax year over the model's answer.
        tax_year = doc.metadata.tax_year_or_effective_period
        if tax_year == NA:
            tax_year = _free_text(data.get("tax_year_or_effective_period"))

        doc.metadata = doc.metadata.model_copy(update={
            "scenario_id": scenario_id,
            "tax_topic": _coerce(data.get("tax_topic"), TAX_TOPICS),
            "jurisdiction": _coerce(data.get("jurisdiction"), JURISDICTIONS),
            "authority_level": _coerce(data.get("authority_level"), AUTHORITY_LEVELS),
            "legal_entity": _free_text(data.get("legal_entity")),
            "business_unit": _free_text(data.get("business_unit")),
            "tax_year_or_effective_period": tax_year,
            # Governance fields intentionally untouched — they stay "n/a".
            "enrichment_status": "ok",
            "enrichment_confidence": _confidence(data.get("confidence")),
            "enrichment_rationale": str(data.get("rationale", ""))[:500],
            "enrichment_model": self.deployment,
            "enrichment_timestamp": timestamp,
        })

        log.info(
            "Enriched %s -> %s / %s / %s (confidence %.2f)",
            doc.source_file_name,
            doc.metadata.jurisdiction,
            doc.metadata.tax_topic,
            doc.metadata.authority_level,
            doc.metadata.enrichment_confidence,
        )
        return doc
