"""Shared Pydantic base model for API payloads."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """Base for API payloads: snake_case in Python, camelCase on the wire.

    Fields are serialized with camelCase aliases, can be populated by either their Python
    name or alias, and models can be built from arbitrary objects via attribute access
    (``from_attributes``).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)
