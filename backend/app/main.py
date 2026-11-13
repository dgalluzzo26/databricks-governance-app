"""FastAPI application factory and local process entrypoint.

Builds the app with config hydration, service container lifecycle, API routes,
exception handlers, and optional SPA static serving from the frontend dist.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.routing import Match
from starlette.types import Scope

from backend.app import __version__
from backend.app.api.routes import api_router
from backend.app.clients.secrets import hydrate_admin_secrets
from backend.app.core.config import AppConfig, get_config
from backend.app.core.container import ServiceContainer
from backend.app.core.errors import NotFoundError, register_exception_handlers
from backend.app.core.logging import configure_logging
from backend.app.core.perf import RequestTimingMiddleware

logger = logging.getLogger(__name__)


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Mount built frontend assets and a SPA catch-all when ``dist`` exists.

    Args:
        app: FastAPI application to attach static mounts and routes to.
        dist: Path to the frontend build output directory.
    """
    index = dist / "index.html"
    if not index.is_file():
        logger.warning("Frontend build not found at %s; serving API only", dist)
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        """Serve a static file or the SPA index for non-API paths.

        Args:
            path: Request path relative to the frontend dist root.

        Returns:
            The matching static file, or ``index.html`` for client-side routes.

        Raises:
            NotFoundError: If the path looks like a missing API route (``api/...``).
        """
        if path.startswith("api/"):
            raise NotFoundError(f"No API route /{path}")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


def _route_template(app: FastAPI, scope: Scope) -> str | None:
    """Return the template of the first route that fully matches ``scope`` (e.g. ``/api/x/{id}``)."""
    for route in app.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "path", None)
    return None


def create_app(config: AppConfig | None = None, container: ServiceContainer | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Prefer secrets injected by app resources; otherwise hydrate from the secret
    scope when ``config`` is not supplied.

    Args:
        config: Optional pre-built config. When omitted, secrets are hydrated and
            config is loaded via ``get_config``.
        container: Optional service container. When omitted, one is built from
            ``config``.

    Returns:
        A fully wired FastAPI app with lifespan, routes, and frontend mount.
    """
    # Prefer secrets injected by app resources; otherwise hydrate from the secret scope.
    if config is None:
        hydrate_admin_secrets()
        get_config.cache_clear()
        config = get_config()
    configure_logging(config.log_level)
    container = container or ServiceContainer.build(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Start and shut down the service container around request handling.

        Args:
            app: FastAPI application instance (unused beyond container access).

        Yields:
            Control to the running application between startup and shutdown.
        """
        await run_in_threadpool(container.startup)
        logger.info(
            "%s %s started (storage=%s, auth=%s, admin_sp=%s)",
            config.app_name,
            __version__,
            container.storage.kind,
            config.auth_mode.value,
            "yes" if container.workspace.admin_configured else "no",
        )
        yield
        await run_in_threadpool(container.shutdown)

    app = FastAPI(title="Data Governance App", version=__version__, lifespan=lifespan)
    app.state.container = container
    register_exception_handlers(app)
    app.include_router(api_router)
    app.add_middleware(
        RequestTimingMiddleware,
        recorder=lambda: container.metrics,
        route_template=lambda scope: _route_template(app, scope),
    )
    _mount_frontend(app, config.frontend_dist)
    return app


if __name__ == "__main__":
    if not os.environ.get("ADMIN_CLIENT_ID"):
        hydrate_admin_secrets()
        get_config.cache_clear()
    cfg = get_config()
    uvicorn.run(create_app(cfg), host="0.0.0.0", port=cfg.port, proxy_headers=True)
