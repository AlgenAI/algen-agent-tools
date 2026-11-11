"""Schema annotations connectors put on their fields for the Runtime's guardrails."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

IDENTIFIER_SCHEMA: dict[str, Any] = {"format": "identifier"}
"""Marks a string field as an id: a chat, message, thread, attachment or page id that may be a
digit string. The Runtime's ``pii`` guardrail (0.2.0b2 and later) leaves a one-token value in
such a field alone, where a ten-digit Telegram chat id would otherwise read as a phone number
and a redacted argument would refuse the call. ``format`` is an annotation: JSON Schema
validators, model providers and earlier Runtimes ignore a format they do not know."""

Identifier = Annotated[str, Field(json_schema_extra=dict(IDENTIFIER_SCHEMA))]
"""A string field the ``pii`` guardrail treats as an id; combine it with ``Field(...)`` for
length limits."""
