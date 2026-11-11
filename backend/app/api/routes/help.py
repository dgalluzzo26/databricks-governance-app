"""In-app developer help documents under ``/api/help``.

Unauthenticated plain-text Markdown guides for operators and developers.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from backend.app.core.errors import NotFoundError

router = APIRouter(tags=["help"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FLOWS_DOC = _REPO_ROOT / "docs" / "mask-and-row-filter-flows.md"


@router.get("/help/flows", response_class=PlainTextResponse)
def mask_and_row_filter_flows() -> str:
    """Return the mask / row-filter developer guide as Markdown.

    Returns:
        Full Markdown contents of the flows help document.

    Raises:
        NotFoundError: If the help file is missing on disk (HTTP 404).
    """
    if not _FLOWS_DOC.is_file():
        raise NotFoundError(f"Help document not found at {_FLOWS_DOC}")
    return _FLOWS_DOC.read_text(encoding="utf-8")
