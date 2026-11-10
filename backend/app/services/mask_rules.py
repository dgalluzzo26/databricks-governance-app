from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from datetime import UTC, datetime

from backend.app.clients.cache import TtlCache
from backend.app.clients.sql import SqlExecutor, quote_identifier, string_literal
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.core.errors import InvalidRequestError, NotFoundError, UpstreamError
from backend.app.models.audit import AuditEvent
from backend.app.models.mask_rule import (
    MaskHandler,
    MaskHandlerDraft,
    MaskHandlerDraftRequest,
    MaskHandlerSampleResult,
    MaskHandlerValidateRequest,
    MaskHandlerValidateResult,
    MaskingConfigStatus,
    MaskRuleDefinition,
    MaskRuleKind,
    MaskRuleList,
    MaskRulePackStatus,
    UpsertMaskRuleRequest,
)
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.services.governed_tags import GovernedTagService
from backend.app.services.mask_handler_sql import (
    build_apply_mask_rule_sql,
    build_resolve_handler_sql,
    expected_custom_keys,
    sanitize_expression,
    substitute_v,
)

logger = logging.getLogger(__name__)

JOINABLE_SALT_KEY = "joinable_salt"
VALIDATION_TTL_SECONDS = 15 * 60
DEFAULT_SAMPLES = ["alice@gmail.com", "555-123-4567", "plaintext", "ABCDEFGH"]

DEFAULT_RULES: list[tuple[str, str, MaskHandler, dict]] = [
    ("full", "Full redact", MaskHandler.FULL, {}),
    ("email", "Keep email domain", MaskHandler.EMAIL, {}),
    ("phone", "Keep last 4 phone digits", MaskHandler.PHONE, {}),
    ("joinable", "Deterministic hash for joins", MaskHandler.JOINABLE, {}),
    ("keep_last_4", "Keep last 4 characters", MaskHandler.KEEP_LAST_N, {"n": 4}),
]

PROTECTED_RULE_KEYS = {"full"}

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


class MaskRuleService:
    """App-managed mask_rule registry mirrored to UC; regenerates apply_mask_rule for customs."""

    def __init__(
        self,
        config: AppConfig,
        sql: SqlExecutor,
        storage: StorageProvider,
        governed_tags: GovernedTagService,
        metadata_cache: TtlCache | None = None,
        *,
        mirror_sql: SqlExecutor | None = None,
        workspace: WorkspaceClientFactory | None = None,
    ) -> None:
        self._config = config
        self._sql = sql
        self._mirror_sql = mirror_sql or sql
        self._storage = storage
        self._governed_tags = governed_tags
        self._metadata_cache = metadata_cache
        self._workspace = workspace

    @property
    def tag_key(self) -> str:
        return self._config.mask_rule_tag_key

    @property
    def definitions_table(self) -> str:
        return ".".join(
            quote_identifier(p)
            for p in (self._config.app_catalog, self._config.app_schema, "mask_rule_definitions")
        )

    @property
    def config_table(self) -> str:
        return ".".join(
            quote_identifier(p)
            for p in (self._config.app_catalog, self._config.app_schema, "masking_config")
        )

    @property
    def function_name(self) -> str:
        return ".".join(
            quote_identifier(p)
            for p in (self._config.app_catalog, self._config.abac_function_schema, "mask_variant")
        )

    @property
    def apply_function_name(self) -> str:
        return ".".join(
            quote_identifier(p)
            for p in (
                self._config.app_catalog,
                self._config.abac_function_schema,
                "apply_mask_rule",
            )
        )

    @property
    def resolve_function_name(self) -> str:
        return ".".join(
            quote_identifier(p)
            for p in (
                self._config.app_catalog,
                self._config.abac_function_schema,
                "_resolve_mask_handler",
            )
        )

    def list_rules(self) -> MaskRuleList:
        rules = self._storage.mask_rules.list_rules()
        return MaskRuleList(rules=rules, total=len(rules))

    def upsert_rule(self, request: UpsertMaskRuleRequest, actor: CurrentUser) -> MaskRuleDefinition:
        now = datetime.now(UTC)
        validated_at: datetime | None = None
        expression: str | None = None
        if request.kind == MaskRuleKind.CUSTOM:
            expression = sanitize_expression(request.expression or "")
            self._require_validation_token(
                expression, actor.user_name, request.validation_token or ""
            )
            validated_at = now
        rule = MaskRuleDefinition(
            rule_key=request.rule_key,
            description=request.description,
            kind=request.kind,
            handler=request.handler or request.rule_key,
            expression=expression,
            params=request.params,
            validated_at=validated_at,
            updated_by=actor.user_name,
            updated_at=now,
        )
        saved = self._storage.mask_rules.upsert_rule(rule)
        self._governed_tags.ensure_values(self.tag_key, [saved.rule_key])
        self.sync_to_uc()
        self.regenerate_apply_mask_rule()
        self._audit(
            "mask_rule.upserted",
            actor.user_name,
            {
                "rule_key": saved.rule_key,
                "kind": saved.kind.value,
                "handler": saved.handler,
                "params": saved.params,
            },
        )
        return saved

    def delete_rule(self, rule_key: str, actor: CurrentUser) -> None:
        key = rule_key.strip().lower()
        if key in PROTECTED_RULE_KEYS:
            raise InvalidRequestError(f"Cannot delete protected mask rule '{key}'")
        if not self._storage.mask_rules.delete_rule(key):
            raise NotFoundError(f"Mask rule '{rule_key}' was not found")
        self.sync_to_uc()
        self.regenerate_apply_mask_rule()
        self._audit("mask_rule.deleted", actor.user_name, {"rule_key": key})

    def seed_defaults(self, actor: CurrentUser) -> MaskRuleList:
        for key, description, handler, params in DEFAULT_RULES:
            existing = self._storage.mask_rules.get_rule(key)
            if existing is not None:
                continue
            self._storage.mask_rules.upsert_rule(
                MaskRuleDefinition(
                    rule_key=key,
                    description=description,
                    kind=MaskRuleKind.BUILTIN,
                    handler=handler.value,
                    expression=None,
                    params=params,
                    updated_by=actor.user_name,
                    updated_at=datetime.now(UTC),
                )
            )
        if not self._storage.mask_rules.get_config(JOINABLE_SALT_KEY):
            self._storage.mask_rules.put_config(
                JOINABLE_SALT_KEY, secrets.token_hex(32), actor.user_name
            )
        self._governed_tags.ensure_values(self.tag_key, [key for key, *_ in DEFAULT_RULES])
        self.sync_to_uc()
        self.regenerate_apply_mask_rule()
        self._audit("mask_rule.seeded", actor.user_name, {"count": len(DEFAULT_RULES)})
        return self.list_rules()

    def ensure_salt(self, actor: CurrentUser, *, rotate: bool = False) -> MaskingConfigStatus:
        current = self._storage.mask_rules.get_config(JOINABLE_SALT_KEY)
        if current is None or rotate:
            self._storage.mask_rules.put_config(
                JOINABLE_SALT_KEY, secrets.token_hex(32), actor.user_name
            )
            self.sync_to_uc()
            self.regenerate_apply_mask_rule()
            self._audit(
                "mask_rule.salt_rotated" if rotate else "mask_rule.salt_created",
                actor.user_name,
                {},
            )
        return self.pack_status().config

    def validate_handler(
        self, request: MaskHandlerValidateRequest, actor: CurrentUser
    ) -> MaskHandlerValidateResult:
        expression = sanitize_expression(request.expression)
        samples_in = [s for s in (request.sample_inputs or DEFAULT_SAMPLES) if s is not None]
        if not samples_in:
            samples_in = list(DEFAULT_SAMPLES)
        values_sql = ", ".join(f"({string_literal(s)})" for s in samples_in)
        expr_sql = substitute_v(expression, "v")
        sql = (
            f"SELECT v AS input, cast(({expr_sql}) AS STRING) AS output "
            f"FROM VALUES {values_sql} AS t(v)"
        )
        samples: list[MaskHandlerSampleResult] = []
        try:
            rows = self._sql.query(sql)
        except Exception:  # noqa: BLE001
            # Try per-input so the UI can show which samples fail.
            for sample in samples_in:
                try:
                    one = self._sql.query(
                        f"SELECT cast(({substitute_v(expression, string_literal(sample))}) "
                        f"AS STRING) AS output"
                    )
                    out = None if not one else one[0].get("output")
                    samples.append(
                        MaskHandlerSampleResult(
                            input=sample, output=None if out is None else str(out)
                        )
                    )
                except Exception as one_exc:  # noqa: BLE001
                    samples.append(
                        MaskHandlerSampleResult(input=sample, error=str(one_exc))
                    )
            return MaskHandlerValidateResult(
                ok=False, expression=expression, samples=samples, validation_token=None
            )

        for row in rows:
            samples.append(
                MaskHandlerSampleResult(
                    input=str(row.get("input") or ""),
                    output=None if row.get("output") is None else str(row.get("output")),
                )
            )
        if any(s.error for s in samples):
            return MaskHandlerValidateResult(
                ok=False, expression=expression, samples=samples, validation_token=None
            )
        token = self._issue_validation_token(expression, actor.user_name)
        return MaskHandlerValidateResult(
            ok=True, expression=expression, samples=samples, validation_token=token
        )

    def draft_handler(self, request: MaskHandlerDraftRequest, actor: CurrentUser) -> MaskHandlerDraft:
        _ = actor
        if self._workspace is None:
            raise InvalidRequestError("Workspace client is not configured for LLM draft")
        system = (
            "You write Databricks SQL scalar expressions for column masking. "
            "The input string column is the identifier v. Return ONLY JSON with keys "
            "expression (string), explanation (string), sample_inputs (array of strings). "
            "The expression must be a single scalar SQL expression returning STRING. "
            "Do not use DML, semicolons, or table references. Prefer concat, sha2, lower, "
            "trim, regexp_replace, split_part, right, left, length, repeat."
        )
        user_parts = [f"Create a mask expression for: {request.prompt.strip()}"]
        if request.expression:
            user_parts.append(f"Current expression:\n{request.expression}")
        if request.errors:
            user_parts.append("Validation errors:\n" + "\n".join(request.errors))
        try:
            from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

            response = self._workspace.admin().serving_endpoints.query(
                name=self._config.mask_handler_llm_endpoint,
                messages=[
                    ChatMessage(role=ChatMessageRole.SYSTEM, content=system),
                    ChatMessage(role=ChatMessageRole.USER, content="\n\n".join(user_parts)),
                ],
                max_tokens=800,
            )
            content = ""
            if response.choices:
                message = response.choices[0].message
                content = getattr(message, "content", None) or ""
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(f"LLM draft failed: {exc}") from exc

        parsed = _parse_draft_json(content)
        expression = sanitize_expression(str(parsed.get("expression") or ""))
        explanation = str(parsed.get("explanation") or "Drafted mask expression").strip()
        samples = parsed.get("sample_inputs") or DEFAULT_SAMPLES
        if not isinstance(samples, list):
            samples = list(DEFAULT_SAMPLES)
        return MaskHandlerDraft(
            expression=expression,
            explanation=explanation,
            sample_inputs=[str(s) for s in samples][:10],
        )

    def sync_to_uc(self) -> dict[str, int]:
        """Replace the UC registry tables from Lakebase (or memory)."""
        self._ensure_uc_tables()
        rules = self._storage.mask_rules.list_rules()
        salt = self._storage.mask_rules.get_config(JOINABLE_SALT_KEY)
        self._mirror_sql.execute(f"DELETE FROM {self.definitions_table}")
        for rule in rules:
            validated_sql = (
                f"CAST({string_literal(rule.validated_at.isoformat())} AS TIMESTAMP)"
                if rule.validated_at
                else "NULL"
            )
            self._mirror_sql.execute(
                f"""
                INSERT INTO {self.definitions_table}
                  (rule_key, description, kind, handler, expression, params,
                   validated_at, updated_by, updated_at)
                VALUES (
                  {string_literal(rule.rule_key)},
                  {string_literal(rule.description or '')},
                  {string_literal(rule.kind.value)},
                  {string_literal(rule.handler)},
                  {string_literal(rule.expression or '')},
                  {string_literal(json.dumps(rule.params))},
                  {validated_sql},
                  {string_literal(rule.updated_by or '')},
                  current_timestamp()
                )
                """
            )
        if salt is not None:
            self._mirror_sql.execute(
                f"""
                MERGE INTO {self.config_table} t
                USING (
                  SELECT {string_literal(JOINABLE_SALT_KEY)} AS config_key,
                         {string_literal(salt)} AS config_value
                ) s
                ON t.config_key = s.config_key
                WHEN MATCHED THEN UPDATE SET
                  config_value = s.config_value,
                  updated_at = current_timestamp()
                WHEN NOT MATCHED THEN INSERT (config_key, config_value, updated_by, updated_at)
                VALUES (s.config_key, s.config_value, 'app', current_timestamp())
                """
            )
        logger.info("Synced %s mask rules to UC %s", len(rules), self.definitions_table)
        return {"rules": len(rules), "salt_configured": 1 if salt else 0}

    def regenerate_apply_mask_rule(self) -> None:
        rules = self._storage.mask_rules.list_rules()
        resolve_sql = build_resolve_handler_sql(
            function_fqn=self.resolve_function_name,
            definitions_table=self.definitions_table,
        )
        apply_sql = build_apply_mask_rule_sql(
            function_fqn=self.apply_function_name,
            resolve_function_fqn=self.resolve_function_name,
            definitions_table=self.definitions_table,
            config_table=self.config_table,
            rules=rules,
        )
        self._sql.execute(resolve_sql)
        self._sql.execute(apply_sql)
        logger.info("Regenerated %s from %s rules", self.apply_function_name, len(rules))

    def pack_status(self) -> MaskRulePackStatus:
        rules = self._storage.mask_rules.list_rules()
        tag_values = self._governed_tags.get_tag(self.tag_key).values
        missing = [r.rule_key for r in rules if r.rule_key not in tag_values]
        policy_name = self._config.mask_variant_policy_name
        rows = self._sql.query(
            "SELECT match_columns FROM system.information_schema.abac_policy_definitions "
            "WHERE policy_name = :name AND upper(on_securable_type) = 'METASTORE'",
            {"name": policy_name},
        )
        passes = False
        udf_in_sync = False
        try:
            sig = self._sql.query(
                "SELECT routine_definition FROM system.information_schema.routines "
                "WHERE routine_catalog = :c AND routine_schema = :s AND routine_name = 'mask_variant'",
                {"c": self._config.app_catalog, "s": self._config.abac_function_schema},
            )
            body = str(sig[0].get("routine_definition") or "") if sig else ""
            passes = "mask_rule" in body.lower() or "apply_mask_rule" in body.lower()
            apply = self._sql.query(
                "SELECT routine_definition FROM system.information_schema.routines "
                "WHERE routine_catalog = :c AND routine_schema = :s "
                "AND routine_name = 'apply_mask_rule'",
                {"c": self._config.app_catalog, "s": self._config.abac_function_schema},
            )
            apply_body = str(apply[0].get("routine_definition") or "") if apply else ""
            customs = expected_custom_keys(rules)
            udf_in_sync = bool(apply_body) and all(key in apply_body for key in customs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not inspect mask functions: %s", exc)
        salt = self._storage.mask_rules.get_config(JOINABLE_SALT_KEY)
        config = MaskingConfigStatus(
            joinable_salt_configured=bool(salt),
            tag_key=self.tag_key,
            tag_values=tag_values,
            missing_tag_values=missing,
            policy_name=policy_name,
            policy_exists=bool(rows),
            policy_passes_mask_rule=passes and bool(rows),
            function_name=self.function_name,
            udf_in_sync=udf_in_sync,
            in_sync=bool(salt) and not missing and bool(rows) and passes and udf_in_sync,
        )
        return MaskRulePackStatus(config=config, rules=rules)

    def ensure_pack(self, actor: CurrentUser) -> MaskRulePackStatus:
        """Seed defaults if empty, ensure salt/tag values, sync UC, recreate policy + UDF."""
        if not self._storage.mask_rules.list_rules():
            self.seed_defaults(actor)
        else:
            self.ensure_salt(actor, rotate=False)
            self._governed_tags.ensure_values(
                self.tag_key, [r.rule_key for r in self._storage.mask_rules.list_rules()]
            )
            self.sync_to_uc()
            self.regenerate_apply_mask_rule()
        self._recreate_variant_policy()
        if self._metadata_cache is not None:
            self._metadata_cache.invalidate(("abac-policy-definitions",))
        self._audit("mask_rule.pack_ensured", actor.user_name, {})
        return self.pack_status()

    def _recreate_variant_policy(self) -> None:
        name = self._config.mask_variant_policy_name
        self._sql.execute(
            f"""
            CREATE OR REPLACE POLICY {name} ON METASTORE
            COMMENT 'Mask sensitivity columns (non-geo); presentation via mask_rule'
            COLUMN MASK {self.function_name}
            TO `account users`
            FOR TABLES
            MATCH COLUMNS (
              has_tag('sensitivity_category')
              AND has_tag('sensitivity')
              AND has_tag('data_type')
              AND NOT has_tag_value('data_type', 'geometry')
              AND NOT has_tag_value('data_type', 'geography')
            ) AS c
            ON COLUMN c
            USING COLUMNS (
              get_column_tag_value(c, 'sensitivity_category'),
              get_column_tag_value(c, 'sensitivity'),
              get_column_tag_value(c, 'mask_rule')
            )
            """
        )

    def _ensure_uc_tables(self) -> None:
        self._mirror_sql.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.definitions_table} (
              rule_key STRING NOT NULL,
              description STRING,
              kind STRING,
              handler STRING NOT NULL,
              expression STRING,
              params STRING NOT NULL,
              validated_at TIMESTAMP,
              updated_by STRING,
              updated_at TIMESTAMP
            ) USING DELTA
            COMMENT 'UC mirror of mask rule registry for VARIANT column masks'
            """
        )
        # Older mirrors may lack new columns.
        for col, typ in (
            ("kind", "STRING"),
            ("expression", "STRING"),
            ("validated_at", "TIMESTAMP"),
        ):
            with contextlib.suppress(Exception):
                self._mirror_sql.execute(
                    f"ALTER TABLE {self.definitions_table} ADD COLUMNS ({col} {typ})"
                )
        self._mirror_sql.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.config_table} (
              config_key STRING NOT NULL,
              config_value STRING NOT NULL,
              updated_by STRING,
              updated_at TIMESTAMP
            ) USING DELTA
            COMMENT 'Masking config (e.g. joinable_salt) for VARIANT column masks'
            """
        )
        self._ensure_mirror_grants()

    def _ensure_mirror_grants(self) -> None:
        """Admin SQL regenerates UDFs that read the UC mirror; keep both SPs privileged."""
        principals: list[str] = []
        if self._workspace is not None:
            for item in (self._workspace.app_client_id, self._workspace.admin_client_id):
                if item and item not in principals:
                    principals.append(item)
        for principal in principals:
            quoted = f"`{principal}`"
            for table in (self.definitions_table, self.config_table):
                with contextlib.suppress(Exception):
                    self._sql.execute(f"GRANT SELECT, MODIFY ON TABLE {table} TO {quoted}")
            with contextlib.suppress(Exception):
                self._sql.execute(
                    f"GRANT MANAGE ON SCHEMA "
                    f"{quote_identifier(self._config.app_catalog)}."
                    f"{quote_identifier(self._config.abac_function_schema)} "
                    f"TO {quoted}"
                )
            for fn in (self.apply_function_name, self.resolve_function_name, self.function_name):
                with contextlib.suppress(Exception):
                    self._sql.execute(f"GRANT MANAGE ON FUNCTION {fn} TO {quoted}")

    def _validation_secret(self) -> bytes:
        raw = (
            self._config.mask_handler_validation_secret
            or self._config.admin_client_secret
            or "local-dev-mask-handler-secret"
        )
        return raw.encode("utf-8")

    def _issue_validation_token(self, expression: str, actor: str) -> str:
        expires = int(time.time()) + VALIDATION_TTL_SECONDS
        payload = f"{expires}:{actor}:{expression}"
        digest = hmac.new(self._validation_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{expires}.{digest}"

    def _require_validation_token(self, expression: str, actor: str, token: str) -> None:
        try:
            expires_s, digest = token.split(".", 1)
            expires = int(expires_s)
        except ValueError as exc:
            raise InvalidRequestError("Invalid validation_token") from exc
        if expires < int(time.time()):
            raise InvalidRequestError("validation_token expired; validate the expression again")
        payload = f"{expires}:{actor}:{expression}"
        expected = hmac.new(
            self._validation_secret(), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, digest):
            raise InvalidRequestError(
                "validation_token does not match this expression; validate again before saving"
            )

    def _audit(self, event_type: str, actor: str, payload: dict) -> None:
        self._storage.audit.record(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type=event_type,
                actor=actor,
                payload=payload,
                created_at=datetime.now(UTC),
            )
        )


def _parse_draft_json(content: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = _JSON_BLOCK.search(text)
    if not match:
        raise UpstreamError("LLM did not return JSON with an expression")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise UpstreamError("LLM returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise UpstreamError("LLM JSON must be an object")
    return data
