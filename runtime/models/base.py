"""Shared Pydantic base configuration for all runtime contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RuntimeModel(BaseModel):
    """Base for every object passed between workflow components.

    Configuration choices and why they matter here:

    ``extra="forbid"``
        An LLM returning a field we did not ask for is a prompt regression. Fail
        loudly rather than silently dropping it.

    ``frozen=True``
        Workflow steps must not mutate upstream results. If a step needs to
        change something it produces a new object, which keeps traces honest.

    ``validate_assignment=True``
        Belt-and-braces for the rare non-frozen subclass.

    ``str_strip_whitespace=True``
        Models routinely emit trailing whitespace in structured output.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        str_strip_whitespace=True,
        use_enum_values=False,
        populate_by_name=True,
    )


class MutableRuntimeModel(RuntimeModel):
    """For accumulator objects only (trace buffers, feedback drafts)."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        validate_assignment=True,
        str_strip_whitespace=True,
    )
