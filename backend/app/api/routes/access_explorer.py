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
    _ = user
    return container.access_explorer.search_tables(q, limit=limit)


@router.get("/tree/catalogs", response_model=TreeCatalogList)
def list_catalogs(user: User, container: Container) -> TreeCatalogList:
    _ = user
    return container.access_explorer.list_catalogs()


@router.get("/tree/schemas", response_model=TreeSchemaList)
def list_schemas(
    user: User,
    container: Container,
    catalog: str = Query(..., min_length=1, max_length=255),
) -> TreeSchemaList:
    _ = user
    return container.access_explorer.list_schemas(catalog)


@router.get("/tree/tables", response_model=TreeTableList)
def list_tables(
    user: User,
    container: Container,
    catalog: str = Query(..., min_length=1, max_length=255),
    schema: str = Query(..., min_length=1, max_length=255),
) -> TreeTableList:
    _ = user
    return container.access_explorer.list_tables(catalog, schema)


@router.post("/analyze", response_model=TableAccessInsight)
def analyze_table(
    body: AnalyzeTableRequest, user: User, container: Container
) -> TableAccessInsight:
    return container.access_explorer.analyze_table(body, user)
