"""Liveness endpoint at ``GET /api/health``.

Unauthenticated; used by load balancers and deploy checks.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Return process liveness and the package version.

    Returns:
        Mapping with ``status`` (``ok``) and ``version``.
    """
    return {"status": "ok", "version": __version__}
