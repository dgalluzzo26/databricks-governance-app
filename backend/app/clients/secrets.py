"""Load Account Admin SP credentials from a Databricks secret scope into the environment.

Metastore-level ABAC policies require an Account Admin service principal. The Databricks Apps
runtime SP is not enough. Secrets live in scope ``databricks-governance-app`` (configurable)
and are also injected via app resources when deployed.
"""

from __future__ import annotations

import base64
import logging
import os
import threading

logger = logging.getLogger(__name__)

DEFAULT_SCOPE = "databricks-governance-app"

# Secret keys → env vars consumed by AppConfig / WorkspaceClientFactory.
SECRET_ENV_MAP = {
    "client_id": "ADMIN_CLIENT_ID",
    "client_secret": "ADMIN_CLIENT_SECRET",
}

_lock = threading.Lock()
_hydrated_once = False


def admin_secret_scope() -> str:
    return os.environ.get("ADMIN_SECRET_SCOPE", "").strip() or DEFAULT_SCOPE


def decode_secret_value(raw: object) -> str:
    if raw is None:
        return ""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    text = text.strip()
    if not text:
        return ""
    try:
        decoded = base64.b64decode(text, validate=True).decode("utf-8")
        if decoded and decoded.isprintable():
            text = decoded.strip()
    except Exception:  # noqa: BLE001 - fall back to the raw text
        pass
    return "" if text == "-" else text


def hydrate_admin_secrets(*, force: bool = False) -> list[str]:
    """Pull Account Admin SP secrets into os.environ. Returns loaded secret keys.

    Skips keys that are already set (e.g. injected by app resources) unless ``force`` is True.
    Uses the Apps / job identity to read the scope.
    """
    global _hydrated_once
    with _lock:
        if _hydrated_once and not force:
            return []

        already = all(os.environ.get(env, "").strip() for env in SECRET_ENV_MAP.values())
        if already and not force:
            _hydrated_once = True
            logger.info("Account Admin SP already present in environment; skipping secret hydrate")
            return []

        scope = admin_secret_scope()
        try:
            from databricks.sdk import WorkspaceClient

            client = WorkspaceClient()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Admin secrets hydrate: WorkspaceClient unavailable: %s", exc)
            return []

        loaded: list[str] = []
        for secret_key, env_name in SECRET_ENV_MAP.items():
            if not force and os.environ.get(env_name, "").strip():
                continue
            try:
                resp = client.secrets.get_secret(scope=scope, key=secret_key)
                value = decode_secret_value(getattr(resp, "value", None))
                if value:
                    os.environ[env_name] = value
                    loaded.append(secret_key)
            except Exception as exc:  # noqa: BLE001
                logger.debug("secret %s/%s not loaded: %s", scope, secret_key, exc)

        if force or os.environ.get("ADMIN_CLIENT_ID", "").strip():
            _hydrated_once = True
        if loaded:
            logger.info("Hydrated Account Admin SP from secrets scope=%s keys=%s", scope, ",".join(loaded))
        else:
            logger.info(
                "Admin secrets hydrate: no new keys from scope=%s "
                "(already set, missing keys, or no Can Read)",
                scope,
            )
        return loaded


def admin_credentials_configured() -> bool:
    client_id = os.environ.get("ADMIN_CLIENT_ID", "").strip()
    client_secret = os.environ.get("ADMIN_CLIENT_SECRET", "").strip()
    return bool(client_id and client_secret)
