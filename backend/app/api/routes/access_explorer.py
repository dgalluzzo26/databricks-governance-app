"""Catalog tree, table search, and access insights under ``/api/access-explorer``.

Any signed-in user may browse catalogs/schemas/tables, search tables, and
analyze a table's column masks, row filters, and attribute suggestions.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.app.api.deps import Container, User
from backend.app.models.access_explorer import (
    AnalyzeTableRequest,
    TableAccessInsight,
    TableSearchResult,
    TreeCatalogList,
    TreeSchemaList,
    TreeTableList,
)

router = APIRouter(prefix="/access-explorer", tags=["access-explorer"])


@router.get("/search", response_model=TableSearchResult)
def search_tables(
    user: User,
    container: Container,
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(40, ge=1, le=100),
) -> TableSearchResult:
    """Search Unity Catalog tables by name substring.

    Any signed-in user.

    Args:
        user: Authenticated current user (dependency injection).
        container: Process-scoped service container (dependency injection).
        q: Search string (2-200 characters).
        limit: Maximum results to return (1-100, default 40).

    Returns:
        Matching tables for the search query.
    """
    _ = user
    return container.access_explorer.search_tables(q, limit=limit)


@router.get("/tree/catalogs", response_model=TreeCatalogList)
def list_catalogs(user: User, container: Container) -> TreeCatalogList:
    """List catalogs available in the access explorer tree.

    Any signed-in user.

    Returns:
        Catalog names for tree navigation.
    """
    _ = user
    return container.access_explorer.list_catalogs()


@router.get("/tree/schemas", response_model=TreeSchemaList)
def list_schemas(
    user: User,
    container: Container,
    catalog: str = Query(..., min_length=1, max_length=255),
) -> TreeSchemaList:
    """List schemas under a catalog for the access explorer tree.

    Any signed-in user.

    Args:
        user: Authenticated current user (dependency injection).
        container: Process-scoped service container (dependency injection).
        catalog: Parent catalog name.

    Returns:
        Schema names within the catalog.
    """
    _ = user
    return container.access_explorer.list_schemas(catalog)


@router.get("/tree/tables", response_model=TreeTableList)
def list_tables(
    user: User,
    container: Container,
    catalog: str = Query(..., min_length=1, max_length=255),
    schema: str = Query(..., min_length=1, max_length=255),
) -> TreeTableList:
    """List tables under a catalog.schema for the access explorer tree.

    Any signed-in user.

    Args:
        user: Authenticated current user (dependency injection).
        container: Process-scoped service container (dependency injection).
        catalog: Parent catalog name.
        schema: Parent schema name.

    Returns:
        Table names within the schema.
    """
    _ = user
    return container.access_explorer.list_tables(catalog, schema)


@router.post("/analyze", response_model=TableAccessInsight)
def analyze_table(
    body: AnalyzeTableRequest, user: User, container: Container
) -> TableAccessInsight:
    """Analyze column masks, row filters, and attribute gaps for a table.

    Any signed-in user. Insights are personalized using the caller's attributes.

    Args:
        body: Fully qualified table identity to analyze.
        user: Authenticated current user (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        Access insight including masks, filters, and suggestions.

    Raises:
        NotFoundError: If the table does not exist (HTTP 404).
    """
    return container.access_explorer.analyze_table(body, user)
