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
    """Whether a mask rule uses a builtin algorithm or a custom SQL expression.

    Attributes:
        BUILTIN: Uses one of the ``MaskHandler`` algorithms.
        CUSTOM: Uses an admin-supplied SQL expression over the input value ``v``.
    """

    BUILTIN = "builtin"
    CUSTOM = "custom"


class MaskHandler(StrEnum):
    """Builtin algorithm ids. Custom handlers use their rule_key as the CASE key.

    Attributes:
        FULL: Replace the value entirely with ``***``.
        JOINABLE: Salted SHA-256 of the trimmed, lower-cased value, so masked columns still
            join consistently.
        EMAIL: Keep only the domain (``***@domain``).
        PHONE: Keep only the last four digits (``***-***-1234``).
        KEEP_LAST_N: Replace all but the last ``params.n`` characters with ``*``.
    """

    FULL = "full"
    JOINABLE = "joinable"
    EMAIL = "email"
    PHONE = "phone"
    KEEP_LAST_N = "keep_last_n"


class MaskRuleDefinition(ApiModel):
    """Stored mask rule, selected on a column via the ``mask_rule`` tag.

    Attributes:
        rule_key: Rule identifier; also the ``mask_rule`` governed tag value.
        description: Optional human-readable description.
        kind: Builtin or custom.
        handler: Builtin handler id, or ``rule_key`` for custom rules.
        expression: SQL expression for custom rules; None for builtin rules.
        params: Handler parameters, e.g. ``{"n": 4}`` for ``keep_last_n``; empty otherwise.
        validated_at: When a custom expression last passed validation; None for builtins.
        updated_by: User who last saved the rule.
        updated_at: Last save time.
    """

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
    """All mask rules.

    Attributes:
        rules: Mask rule definitions.
        total: Number of rules.
    """

    rules: list[MaskRuleDefinition]
    total: int


class UpsertMaskRuleRequest(ApiModel):
    """Request body to create or update a mask rule.

    Validation normalizes the body per ``kind``. Custom rules require ``expression`` and a
    ``validation_token``; ``handler`` is forced to ``rule_key`` and ``params`` cleared.
    Builtin rules default ``handler`` to ``rule_key``, must name a builtin handler, clear
    ``expression``, and keep only ``n`` (1-64, default 4) for ``keep_last_n``.

    Attributes:
        rule_key: Rule identifier; lower-cased, must match ``RULE_KEY_PATTERN``.
        description: Optional human-readable description.
        kind: Builtin or custom.
        handler: Builtin handler id; lower-cased. Ignored for custom rules.
        expression: SQL expression over ``v`` for custom rules.
        params: Handler parameters (only ``n`` for ``keep_last_n`` is kept).
        validation_token: Token from a successful validation of exactly this expression by
            the same user; required for custom rules.
    """

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
        """Normalize the rule key to stripped lower case before pattern validation.

        Args:
            value: Raw rule key; None is treated as an empty string.

        Returns:
            The normalized rule key.
        """
        return str(value or "").strip().lower()

    @field_validator("handler", mode="before")
    @classmethod
    def _lower_handler(cls, value: str | None) -> str | None:
        """Normalize the handler id to stripped lower case.

        Args:
            value: Raw handler id, or None.

        Returns:
            The normalized handler id, or None when not provided.
        """
        if value is None:
            return None
        return str(value).strip().lower()

    @model_validator(mode="after")
    def _validate_body(self) -> UpsertMaskRuleRequest:
        """Enforce and normalize the fields required by ``kind``.

        Returns:
            This request with ``handler``, ``expression`` and ``params`` normalized.

        Raises:
            ValueError: If a custom rule lacks ``expression`` or ``validation_token``, a
                builtin rule names an unknown handler, or ``params.n`` is not an integer in
                1-64 for ``keep_last_n``.
        """
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
    """Request body to have an LLM draft a custom mask expression.

    Attributes:
        prompt: Natural-language description of the desired masking (3-4000 chars).
        expression: Current draft to refine, or None to start fresh.
        errors: Validation errors from the previous attempt, fed back to the LLM.
    """

    prompt: str = Field(min_length=3, max_length=4000)
    expression: str | None = None
    errors: list[str] = []


class MaskHandlerDraft(ApiModel):
    """LLM-drafted custom mask expression (not yet validated).

    Attributes:
        expression: Sanitized SQL expression over ``v``.
        explanation: Short explanation of what the expression does.
        sample_inputs: Up to 10 suggested inputs for validation.
    """

    expression: str
    explanation: str
    sample_inputs: list[str] = []


class MaskHandlerValidateRequest(ApiModel):
    """Request body to test a custom mask expression on the SQL warehouse.

    Attributes:
        expression: SQL expression over ``v`` to test.
        sample_inputs: Inputs to run it against; built-in defaults are used when empty.
    """

    expression: str = Field(min_length=1)
    sample_inputs: list[str] = []


class MaskHandlerSampleResult(ApiModel):
    """Result of applying a mask expression to one sample input.

    Attributes:
        input: Sample input value.
        output: Masked output; None when the expression returned NULL or failed.
        error: Error message when evaluating this input failed; None on success.
    """

    input: str
    output: str | None = None
    error: str | None = None


class MaskHandlerValidateResult(ApiModel):
    """Outcome of validating a custom mask expression.

    Attributes:
        ok: True when every sample evaluated without error.
        expression: The sanitized expression that was tested.
        samples: Per-sample results.
        validation_token: Short-lived signed token binding this expression to the validating
            user, required to save a custom rule; None when ``ok`` is False.
    """

    ok: bool
    expression: str
    samples: list[MaskHandlerSampleResult]
    validation_token: str | None = None


class MaskingConfigStatus(ApiModel):
    """Drift report for the mask-rule tag, salt, UDFs and metastore ABAC policy.

    Attributes:
        joinable_salt_configured: True when the salt for the ``joinable`` handler exists.
        tag_key: Governed tag key used to select mask rules on columns.
        tag_values: Values currently allowed by that governed tag.
        missing_tag_values: Rule keys not yet allowed as values of the governed tag.
        policy_name: Name of the metastore-level mask policy.
        policy_exists: True when the policy exists on the metastore.
        policy_passes_mask_rule: True when the policy exists and its masking UDF reads the
            ``mask_rule`` tag.
        function_name: Fully qualified name of the masking UDF.
        udf_in_sync: True when the generated ``apply_mask_rule`` UDF contains every custom
            rule.
        in_sync: True when all of the above checks pass.
    """

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
    """Mask-rule configuration status together with the stored rules.

    Attributes:
        config: Drift report for the masking infrastructure.
        rules: All stored mask rules.
    """

    config: MaskingConfigStatus
    rules: list[MaskRuleDefinition]
