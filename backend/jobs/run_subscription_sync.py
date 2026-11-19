"""Databricks Job entrypoint for the subscription engine (second task of the sync job).

Runs after the data-access sync task on the same schedule and Run now. Subscriptions have no
dry run: each pass starts subscriptions whose window opened and strips ones that ended, were
revoked, or were denied. Takes the same parameters as ``run_data_access_sync``; ``--mode`` is
ignored.

Exit codes: 0 on success or paused no-op; 1 when any subscription failed or on error.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_here = Path(globals().get("__file__") or sys.argv[0]).resolve()
_REPO_ROOT = _here.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.clients.secrets import hydrate_admin_secrets  # noqa: E402
from backend.app.core.config import get_config  # noqa: E402
from backend.app.core.container import ServiceContainer  # noqa: E402
from backend.app.models.data_access import SyncTrigger  # noqa: E402
from backend.jobs.run_data_access_sync import (  # noqa: E402
    _apply_env_from_args,
    _parse_args,
    ensure_lakebase_env,
)

logger = logging.getLogger("subscription_sync_job")


def main(argv: list[str] | None = None) -> int:
    """Reconcile every subscription and return a process exit code."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    try:
        _apply_env_from_args(args)
        ensure_lakebase_env()
        hydrate_admin_secrets()
        get_config.cache_clear()
        container = ServiceContainer.build(get_config())
        container.startup(migrate_storage=False, sync_schedule=False, mirror_uc=False)
        try:
            settings = container.data_access.get_settings().settings
            trigger = (args.trigger or "schedule").strip().lower()
            if trigger == SyncTrigger.SCHEDULE.value and not settings.schedule_enabled:
                logger.info("Schedule paused (schedule_enabled=false); exiting without subscription sync")
                return 0
            result = container.subscriptions.sync_all()
            logger.info(
                "Subscriptions: processed=%d active=%d grants+=%d grants-=%d "
                "members+=%d members-=%d failed=%d",
                result.processed,
                result.active,
                result.grants_added,
                result.grants_revoked,
                result.members_added,
                result.members_removed,
                result.failed,
            )
            for error in result.errors:
                logger.error("%s", error)
            return 1 if result.failed else 0
        finally:
            container.shutdown()
    except Exception:
        logger.exception("Subscription sync job failed")
        return 1


if __name__ == "__main__":
    rc = main()
    if rc != 0:
        raise RuntimeError(f"Subscription sync job failed with exit code {rc}")
