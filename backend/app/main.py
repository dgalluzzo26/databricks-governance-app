from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from backend.app import __version__
from backend.app.api.routes import api_router
from backend.app.core.config import AppConfig, get_config
from backend.app.core.container import ServiceContainer
from backend.app.core.errors import NotFoundError, register_exception_handlers
from backend.app.core.logging import configure_logging

logger = logging.getLogger(__name__)


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():
        logger.warning("Frontend build not found at %s; serving API only", dist)
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise NotFoundError(f"No API route /{path}")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


def create_app(config: AppConfig | None = None, container: ServiceContainer | None = None) -> FastAPI:
    config = config or get_config()
    configure_logging(config.log_level)
    container = container or ServiceContainer.build(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await run_in_threadpool(container.startup)
        logger.info(
            "%s %s started (storage=%s, auth=%s)",
            config.app_name,
            __version__,
            container.storage.kind,
            config.auth_mode.value,
        )
        yield
        await run_in_threadpool(container.shutdown)

    app = FastAPI(title="Data Governance App", version=__version__, lifespan=lifespan)
    app.state.container = container
    register_exception_handlers(app)
    app.include_router(api_router)
    _mount_frontend(app, config.frontend_dist)
    return app


if __name__ == "__main__":
    cfg = get_config()
    uvicorn.run(create_app(cfg), host="0.0.0.0", port=cfg.port, proxy_headers=True)
