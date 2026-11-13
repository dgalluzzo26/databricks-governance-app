"""Models for the in-app developer help (guide index and generated API reference)."""

from __future__ import annotations

from enum import StrEnum

from backend.app.models.base import ApiModel


class EndpointAccess(StrEnum):
    """Minimum caller role an endpoint requires."""

    PUBLIC = "public"
    USER = "user"
    ADMIN = "admin"


class HelpAudience(StrEnum):
    """Who should see a help guide in the in-app Help page.

    The Help UI filters by the caller's effective role (including admin demo View as).
    """

    USER = "user"
    APPROVER = "approver"
    ADMIN = "admin"


class HelpDoc(ApiModel):
    """One Markdown guide served under ``/api/help/docs/{slug}``.

    Attributes:
        slug: URL key for the guide.
        title: Display title.
        description: One-line summary shown in the Help tab.
        source_path: Repo-relative path of the Markdown file.
        audiences: Roles that may see this guide in Help (filter client-side).
    """

    slug: str
    title: str
    description: str
    source_path: str
    audiences: list[HelpAudience]


class HelpDocList(ApiModel):
    """Available help guides in display order."""

    docs: list[HelpDoc]


class ApiParameter(ApiModel):
    """A path or query parameter of an endpoint.

    Attributes:
        name: Wire name.
        location: ``path`` or ``query``.
        required: Whether the parameter must be supplied.
        type: Python annotation rendered as text.
        description: Parameter description, when declared.
    """

    name: str
    location: str
    required: bool
    type: str
    description: str | None = None


class ApiEndpoint(ApiModel):
    """One HTTP route of the backend, derived from the FastAPI route table.

    Attributes:
        method: HTTP verb.
        path: Full path including the ``/api`` prefix.
        tag: Router tag (feature area).
        summary: First line of the handler docstring.
        description: Remaining docstring text (Args/Returns sections included).
        access: Minimum role required (from the route's auth dependencies).
        request_body: Request body model name, when the route accepts one.
        response_model: Response model name, when declared.
        parameters: Path and query parameters.
        handler: ``module.function`` implementing the route.
    """

    method: str
    path: str
    tag: str
    summary: str
    description: str
    access: EndpointAccess
    request_body: str | None = None
    response_model: str | None = None
    parameters: list[ApiParameter]
    handler: str


class ApiReference(ApiModel):
    """Every backend endpoint grouped by tag order of appearance."""

    endpoints: list[ApiEndpoint]
