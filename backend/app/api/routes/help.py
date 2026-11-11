"""In-app developer help under ``/api/help``.

Serves the Markdown guides in ``docs/`` and an API reference generated from the live FastAPI
route table, so endpoint docs never drift from the code.
"""

from __future__ import annotations

import inspect
import re
import types
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.dependencies.models import Dependant
from fastapi.responses import PlainTextResponse
from fastapi.routing import APIRoute

from backend.app.api.deps import User, get_current_user, get_request_context, require_admin
from backend.app.core.errors import NotFoundError
from backend.app.models.help import (
    ApiEndpoint,
    ApiParameter,
    ApiReference,
    EndpointAccess,
    HelpDoc,
    HelpDocList,
)

router = APIRouter(tags=["help"])

_REPO_ROOT = Path(__file__).resolve().parents[4]

_DOCS: tuple[HelpDoc, ...] = (
    HelpDoc(
        slug="masking-row-filters",
        title="Masking & row filters",
        description="Decision flows for column masks, row filters, access requests, and Access Explorer.",
        source_path="docs/mask-and-row-filter-flows.md",
    ),
    HelpDoc(
        slug="grants-sync",
        title="Grants sync",
        description="How tag-driven dac_* groups, membership, and GRANT/REVOKE are planned and applied.",
        source_path="docs/grants-sync.md",
    ),
    HelpDoc(
        slug="backend",
        title="Backend & APIs",
        description="Backend layers, identities, storage, and the Databricks APIs each service calls.",
        source_path="docs/backend-architecture.md",
    ),
)
_DOCS_BY_SLUG = {doc.slug: doc for doc in _DOCS}

_ACCESS_DEPENDENCIES: tuple[tuple[Callable[..., Any], EndpointAccess], ...] = (
    (require_admin, EndpointAccess.ADMIN),
    (get_current_user, EndpointAccess.USER),
    (get_request_context, EndpointAccess.USER),
)
_ACCESS_RANK = {EndpointAccess.PUBLIC: 0, EndpointAccess.USER: 1, EndpointAccess.ADMIN: 2}
_MODULE_PREFIX = re.compile(r"\b(?:[a-z_][a-z0-9_]*\.)+(?=[A-Za-z_])")


def _read_doc(doc: HelpDoc) -> str:
    """Return the Markdown body of ``doc``.

    Raises:
        NotFoundError: If the file is missing on disk (HTTP 404).
    """
    path = _REPO_ROOT / doc.source_path
    if not path.is_file():
        raise NotFoundError(f"Help document not found at {doc.source_path}")
    return path.read_text(encoding="utf-8")


@router.get("/help/docs", response_model=HelpDocList)
def list_docs() -> HelpDocList:
    """List the developer guides available in the Help page.

    Returns:
        Guides in display order.
    """
    return HelpDocList(docs=list(_DOCS))


@router.get("/help/docs/{slug}", response_class=PlainTextResponse)
def get_doc(slug: str) -> str:
    """Return one developer guide as Markdown.

    Args:
        slug: Guide key from ``/help/docs``.

    Returns:
        Full Markdown contents of the guide.

    Raises:
        NotFoundError: If the slug is unknown or the file is missing (HTTP 404).
    """
    doc = _DOCS_BY_SLUG.get(slug)
    if doc is None:
        raise NotFoundError(f"Unknown help document: {slug}")
    return _read_doc(doc)


@router.get("/help/flows", response_class=PlainTextResponse)
def mask_and_row_filter_flows() -> str:
    """Return the mask / row-filter developer guide as Markdown.

    Kept for older clients; equivalent to ``/help/docs/masking-row-filters``.

    Returns:
        Full Markdown contents of the flows help document.

    Raises:
        NotFoundError: If the help file is missing on disk (HTTP 404).
    """
    return _read_doc(_DOCS_BY_SLUG["masking-row-filters"])


@router.get("/help/api-reference", response_model=ApiReference)
def api_reference(_: User) -> ApiReference:
    """Describe every ``/api`` endpoint from the mounted feature routers.

    Access level comes from the route's auth dependencies; summary and description come
    from the handler docstring.

    Returns:
        Endpoints in registration order.
    """
    # Imported here: the routes package imports this module while building ROUTERS.
    from backend.app.api.routes import ROUTERS, api_router

    endpoints: list[ApiEndpoint] = []
    routes = [r for feature in ROUTERS for r in feature.routes if isinstance(r, APIRoute)]
    for route in routes:
        doc = inspect.getdoc(route.endpoint) or ""
        summary, _, description = doc.partition("\n\n")
        for method in sorted(route.methods or ()):
            endpoints.append(
                ApiEndpoint(
                    method=method,
                    path=f"{api_router.prefix}{route.path}",
                    tag=str(route.tags[0]) if route.tags else "other",
                    summary=" ".join(summary.split()),
                    description=description.strip(),
                    access=_access_for(route.dependant),
                    request_body=_body_name(route.dependant),
                    response_model=_type_name(route.response_model) if route.response_model else None,
                    parameters=_parameters(route.dependant),
                    handler=f"{route.endpoint.__module__}.{route.endpoint.__name__}",
                )
            )
    return ApiReference(endpoints=endpoints)


def _access_for(dependant: Dependant) -> EndpointAccess:
    """Return the strictest access level among ``dependant``'s dependency tree."""
    best = EndpointAccess.PUBLIC
    stack = list(dependant.dependencies)
    while stack:
        dep = stack.pop()
        for call, level in _ACCESS_DEPENDENCIES:
            if dep.call is call and _ACCESS_RANK[level] > _ACCESS_RANK[best]:
                best = level
        stack.extend(dep.dependencies)
    return best


def _body_name(dependant: Dependant) -> str | None:
    """Return the request body model name, if the route has one."""
    if not dependant.body_params:
        return None
    return " | ".join(_type_name(field.field_info.annotation) for field in dependant.body_params)


def _parameters(dependant: Dependant) -> list[ApiParameter]:
    """Return path then query parameters declared by the route."""
    params: list[ApiParameter] = []
    for location, fields in (("path", dependant.path_params), ("query", dependant.query_params)):
        for field in fields:
            params.append(
                ApiParameter(
                    name=field.alias,
                    location=location,
                    required=field.field_info.is_required(),
                    type=_type_name(field.field_info.annotation),
                    description=field.field_info.description,
                )
            )
    return params


def _type_name(annotation: object) -> str:
    """Render a type annotation compactly (``DataAccessRun | None``, ``list[str]``)."""
    if annotation is None or annotation is type(None):
        return "None"
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return " | ".join(_type_name(arg) for arg in typing.get_args(annotation))
    if origin is not None:
        args = ", ".join(_type_name(arg) for arg in typing.get_args(annotation))
        return f"{getattr(origin, '__name__', str(origin))}[{args}]"
    name = getattr(annotation, "__name__", None)
    return name if isinstance(name, str) else _MODULE_PREFIX.sub("", str(annotation))
