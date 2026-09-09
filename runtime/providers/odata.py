"""OData filter construction for Azure AI Search.

Deliberately free of any Azure SDK import so it can be unit-tested without
credentials or network. The Azure client wrapper consumes the string this
produces.

Everything here funnels through ``assert_filterable``, which is what makes
hard-filtering on jurisdiction impossible to express.
"""

from __future__ import annotations

from runtime.models.query import SearchFilters
from shared.contracts.index_schema import (
    COLLECTION_FIELDS,
    F,
    assert_filterable,
)


def escape_odata_string(value: str) -> str:
    """Escape a string literal for an OData filter.

    Single quotes are doubled. This is the only injection vector in the filter
    path, since every field name is drawn from a fixed allowlist.
    """
    return value.replace("'", "''")


def _eq_clause(field: str, value: str) -> str:
    assert_filterable(field)
    return f"{field} eq '{escape_odata_string(value)}'"


def _collection_any_clause(field: str, values: list[str]) -> str:
    """Build ``field/any(v: v eq 'a' or v eq 'b')`` for a collection field."""
    assert_filterable(field)
    if field not in COLLECTION_FIELDS:
        raise ValueError(f"{field!r} is not a collection field")
    inner = " or ".join(f"v eq '{escape_odata_string(v)}'" for v in values)
    return f"{field}/any(v: {inner})"


def _date_clause(field: str, operator: str, value: str) -> str:
    assert_filterable(field)
    if operator not in {"ge", "le", "gt", "lt"}:
        raise ValueError(f"unsupported date operator {operator!r}")
    # OData datetime literals are unquoted ISO-8601.
    return f"{field} {operator} {value}"


def build_filter(filters: SearchFilters) -> str | None:
    """Translate ``SearchFilters`` into an OData filter expression.

    Returns ``None`` when no filters apply, which the search client passes
    through as "no filter" rather than an empty string.

    Jurisdiction is structurally absent from ``SearchFilters`` as a filterable
    field -- only ``boost_jurisdictions`` exists, and it is never read here.
    """
    clauses: list[str] = []

    if filters.entities:
        clauses.append(_collection_any_clause(F.ENTITIES, filters.entities))

    if filters.business_units:
        clauses.append(_collection_any_clause(F.BUSINESS_UNITS, filters.business_units))

    if filters.tax_topic:
        clauses.append(_eq_clause(F.TAX_TOPIC, filters.tax_topic))

    if filters.evidence_types:
        inner = " or ".join(
            f"{F.EVIDENCE_TYPE} eq '{escape_odata_string(t)}'"
            for t in filters.evidence_types
        )
        assert_filterable(F.EVIDENCE_TYPE)
        clauses.append(f"({inner})")

    if filters.sent_after:
        clauses.append(_date_clause(F.SENT_DATE, "ge", filters.sent_after))

    if filters.sent_before:
        clauses.append(_date_clause(F.SENT_DATE, "le", filters.sent_before))

    if not clauses:
        return None
    return " and ".join(clauses)


def build_scoring_parameters(filters: SearchFilters) -> list[str] | None:
    """Jurisdiction boost, expressed as a scoring profile parameter.

    This is how jurisdiction influences ranking WITHOUT excluding documents.
    Requires a scoring profile on the index with a tag boost over
    ``jurisdictions``; if none exists, this returns None and relevance falls
    back to pure hybrid scoring, which is still correct -- just less targeted.

    The critical property: a Philadelphia question ranks Philadelphia evidence
    higher but still retrieves New York City evidence, keeping PARTIAL_MATCH
    reachable.
    """
    if not filters.boost_jurisdictions:
        return None
    joined = ",".join(filters.boost_jurisdictions)
    return [f"jurisdictionTag-{joined}"]
