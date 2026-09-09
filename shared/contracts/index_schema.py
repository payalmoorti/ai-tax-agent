"""
Single source of truth for the Azure AI Search index contract.

Both sides of the repo depend on this module:

  * ``ingestion``  WRITES documents shaped by these field names.
  * ``runtime``    READS documents and maps them onto ``InternalEvidence``.

If a field name changes here, both sides fail their tests immediately instead of
failing silently at query time in Azure. That is the entire reason this module
exists -- do not inline these strings anywhere else.

Rule: never type an index field name as a bare string literal in application
code. Import the constant.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

# ---------------------------------------------------------------------------
# Index identity
# ---------------------------------------------------------------------------

INDEX_SCHEMA_VERSION: Final[str] = "1.0.0"
"""Bump on any breaking field change. Recorded in traces and eval reports."""

VECTOR_FIELD_DIMENSIONS: Final[int] = 3072
"""Must match the embedding model deployment used by ingestion."""

VECTOR_PROFILE_NAME: Final[str] = "amsted-hnsw-profile"
SEMANTIC_CONFIG_NAME: Final[str] = "amsted-semantic-config"


# ---------------------------------------------------------------------------
# Field names
# ---------------------------------------------------------------------------

class F:
    """Azure AI Search field names.

    Accessed as ``F.CHUNK_ID`` rather than ``"chunk_id"`` so that renames are a
    single edit and every consumer breaks loudly at import time.
    """

    # --- Identity / provenance -------------------------------------------
    CHUNK_ID: Final[str] = "chunk_id"
    SOURCE_ID: Final[str] = "source_id"
    SOURCE_FILE: Final[str] = "source_file"
    SOURCE_URI: Final[str] = "source_uri"

    # --- Content ----------------------------------------------------------
    CONTENT: Final[str] = "content"
    CONTENT_VECTOR: Final[str] = "content_vector"

    # --- Email / thread structure ----------------------------------------
    THREAD_ID: Final[str] = "thread_id"
    MESSAGE_ID: Final[str] = "message_id"
    SENDER: Final[str] = "sender"
    SENT_DATE: Final[str] = "sent_date"

    # --- Document structure ----------------------------------------------
    PAGE_NUMBER: Final[str] = "page_number"

    # --- Tax enrichment ---------------------------------------------------
    TAX_TOPIC: Final[str] = "tax_topic"
    SUBTOPIC: Final[str] = "subtopic"
    ENTITIES: Final[str] = "entities"
    BUSINESS_UNITS: Final[str] = "business_units"
    JURISDICTIONS: Final[str] = "jurisdictions"
    TAX_CONCEPTS: Final[str] = "tax_concepts"
    EVIDENCE_TYPE: Final[str] = "evidence_type"
    SEMANTIC_STATE: Final[str] = "semantic_state"
    MATERIAL_FACTS: Final[str] = "material_facts"


#: Fields the runtime requests on every search call.
#: ``content_vector`` is deliberately excluded -- never pull embeddings back.
RETRIEVAL_SELECT_FIELDS: Final[tuple[str, ...]] = (
    F.CHUNK_ID,
    F.SOURCE_ID,
    F.SOURCE_FILE,
    F.SOURCE_URI,
    F.CONTENT,
    F.THREAD_ID,
    F.MESSAGE_ID,
    F.SENDER,
    F.SENT_DATE,
    F.PAGE_NUMBER,
    F.TAX_TOPIC,
    F.SUBTOPIC,
    F.ENTITIES,
    F.BUSINESS_UNITS,
    F.JURISDICTIONS,
    F.TAX_CONCEPTS,
    F.EVIDENCE_TYPE,
    F.SEMANTIC_STATE,
    F.MATERIAL_FACTS,
)

#: Fields ingestion must populate on every document. Enforced by an ingestion
#: pre-upload check and asserted by runtime integration tests.
REQUIRED_ON_WRITE: Final[tuple[str, ...]] = (
    F.CHUNK_ID,
    F.SOURCE_ID,
    F.SOURCE_FILE,
    F.CONTENT,
    F.CONTENT_VECTOR,
)

#: Filterable fields. Anything not listed here cannot appear in an OData filter.
FILTERABLE_FIELDS: Final[frozenset[str]] = frozenset({
    F.SOURCE_ID,
    F.SOURCE_FILE,
    F.THREAD_ID,
    F.MESSAGE_ID,
    F.SENT_DATE,
    F.TAX_TOPIC,
    F.SUBTOPIC,
    F.ENTITIES,
    F.BUSINESS_UNITS,
    F.JURISDICTIONS,
    F.TAX_CONCEPTS,
    F.EVIDENCE_TYPE,
    F.SEMANTIC_STATE,
})

#: Fields that may be used to BOOST relevance but must never be used to
#: hard-filter a query.
#:
#: Jurisdiction is the critical one. A Philadelphia question must still be able
#: to retrieve New York City evidence, otherwise PARTIAL_MATCH is unreachable
#: and the system silently collapses to NO_PRECEDENT.
BOOST_ONLY_FIELDS: Final[frozenset[str]] = frozenset({
    F.JURISDICTIONS,
})


# ---------------------------------------------------------------------------
# Collection field helpers
# ---------------------------------------------------------------------------

# Fields that are collections (arrays) in the index and therefore must be
# queried with the OData `/any(...)` syntax rather than scalar equality.
COLLECTION_FIELDS: Final[frozenset[str]] = frozenset({
    F.ENTITIES,
    F.BUSINESS_UNITS,
    F.JURISDICTIONS,
    F.TAX_CONCEPTS,
    F.MATERIAL_FACTS
})


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

class EvidenceType(str, Enum):
    """What kind of artifact a chunk came from. Written by ingestion."""

    EMAIL_MESSAGE = "EMAIL_MESSAGE"
    EMAIL_THREAD = "EMAIL_THREAD"
    MEMO = "MEMO"
    ANALYSIS = "ANALYSIS"
    RETURN_WORKPAPER = "RETURN_WORKPAPER"
    CORRESPONDENCE = "CORRESPONDENCE"
    EXTERNAL_ADVISOR_OPINION = "EXTERNAL_ADVISOR_OPINION"
    OTHER = "OTHER"


class SemanticState(str, Enum):
    """Ingestion's read of whether a chunk states a settled position.

    This is an INPUT SIGNAL to the Precedent Analysis Agent, not a conclusion.
    The runtime must not map ``SETTLED`` directly onto ``ValidityStatus.CURRENT``.
    """

    SETTLED = "SETTLED"
    OPEN_QUESTION = "OPEN_QUESTION"
    SUPERSEDED = "SUPERSEDED"
    BACKGROUND = "BACKGROUND"
    UNKNOWN = "UNKNOWN"


def assert_filterable(field: str) -> None:
    """Guard used by the search provider when building OData filters.

    Raises
    ------
    ValueError
        If the field is not filterable, or is boost-only (jurisdictions).
    """
    if field in BOOST_ONLY_FIELDS:
        raise ValueError(
            f"{field!r} is boost-only and must never be hard-filtered. "
            "Hard-filtering jurisdiction makes PARTIAL_MATCH unreachable."
        )
    if field not in FILTERABLE_FIELDS:
        raise ValueError(
            f"{field!r} is not a filterable index field. "
            f"Filterable: {sorted(FILTERABLE_FIELDS)}"
        )
