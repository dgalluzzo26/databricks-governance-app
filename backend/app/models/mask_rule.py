"""Presentation mask rules: named tag values mapped to builtin or custom SQL handlers.

Authorization (unmask vs mask) stays on sensitivity tags. ``mask_rule`` only chooses
how a denied value looks.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from backend.app.models.base import ApiModel

RULE_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,62}$"

BUILTIN_HANDLERS = frozenset({"full", "joinable", "email", "phone", "keep_last_n"})


class MaskRuleKind(StrEnum):
    BUILTIN = "builtin"
    CUSTOM = "custom"


class MaskHandler(StrEnum):
    """Builtin algorithm ids. Custom handlers use their rule_key as the CASE key."""

    FULL = "full"
    JOINABLE = "joinable"
    EMAIL = "email"
    PHONE = "phone"
    KEEP_LAST_N = "keep_last_n"


class MaskRuleDefinition(ApiModel):
    rule_key: str
    description: str | None = None
    kind: MaskRuleKind = MaskRuleKind.BUILTIN
    handler: str
    expression: str | None = None
    params: dict[str, Any] = {}
    validated_at: datetime | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None


class MaskRuleList(ApiModel):
    rules: list[MaskRuleDefinition]
    total: int


class UpsertMaskRuleRequest(ApiModel):
    rule_key: str = Field(pattern=RULE_KEY_PATTERN)
    description: str | None = None
    kind: MaskRuleKind = MaskRuleKind.BUILTIN
    handler: str | None = None
    expression: str | None = None
    params: dict[str, Any] = {}
    validation_token: str | None = None

    @field_validator("rule_key", mode="before")
    @classmethod
    def _lower_key(cls, value: str) -> str:
        return str(value or "").strip().lower()

    @field_validator("handler", mode="before")
    @classmethod
    def _lower_handler(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip().lower()

    @model_validator(mode="after")
    def _validate_body(self) -> UpsertMaskRuleRequest:
        if self.kind == MaskRuleKind.CUSTOM:
            if not (self.expression or "").strip():
                raise ValueError("expression is required for custom handlers")
            if not (self.validation_token or "").strip():
                raise ValueError("validation_token is required for custom handlers")
            self.handler = self.rule_key
            self.params = {}
            self.expression = (self.expression or "").strip()
            return self

        handler = self.handler or self.rule_key
        if handler not in BUILTIN_HANDLERS:
            raise ValueError(
                f"Unknown builtin handler '{handler}'. Use kind=custom for SQL expressions."
            )
        self.handler = handler
        self.expression = None
        if handler == MaskHandler.KEEP_LAST_N.value:
            n = self.params.get("n", 4)
            try:
                n_int = int(n)
            except (TypeError, ValueError) as exc:
                raise ValueError("params.n must be an integer for keep_last_n") from exc
            if n_int < 1 or n_int > 64:
                raise ValueError("params.n must be between 1 and 64")
            self.params = {"n": n_int}
        else:
            self.params = {}
        return self


class MaskHandlerDraftRequest(ApiModel):
    prompt: str = Field(min_length=3, max_length=4000)
    expression: str | None = None
    errors: list[str] = []


class MaskHandlerDraft(ApiModel):
    expression: str
    explanation: str
    sample_inputs: list[str] = []


class MaskHandlerValidateRequest(ApiModel):
    expression: str = Field(min_length=1)
    sample_inputs: list[str] = []


class MaskHandlerSampleResult(ApiModel):
    input: str
    output: str | None = None
    error: str | None = None


class MaskHandlerValidateResult(ApiModel):
    ok: bool
    expression: str
    samples: list[MaskHandlerSampleResult]
    validation_token: str | None = None


class MaskingConfigStatus(ApiModel):
    joinable_salt_configured: bool
    tag_key: str
    tag_values: list[str] = []
    missing_tag_values: list[str] = []
    policy_name: str
    policy_exists: bool = False
    policy_passes_mask_rule: bool = False
    function_name: str
    udf_in_sync: bool = False
    in_sync: bool = False


class MaskRulePackStatus(ApiModel):
    config: MaskingConfigStatus
    rules: list[MaskRuleDefinition]
