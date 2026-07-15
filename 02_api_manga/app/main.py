"""API HTTP en lecture seule sur le corpus manga stocké dans PostgreSQL."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse
from psycopg import Error as PsycopgError
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout
from pydantic import BaseModel

from .database import create_pool, get_pool
from .settings import Settings

LOGGER = logging.getLogger(__name__)
DATABASE_ERRORS = (PsycopgError, PoolClosed, PoolTimeout)
PoolDependency = Annotated[ConnectionPool, Depends(get_pool)]


class HealthResponse(BaseModel):
    status: str
    db: str


class KitsuCoreResponse(BaseModel):
    kitsu_id: int
    slug: str | None
    title_canonical: str | None
    synopsis_clean: str | None
    rating_average_10: float | None
    rating_rank: int | None
    popularity_rank: int | None


class RagPreview(BaseModel):
    doc_key: str
    source: str
    boost_score: float
    preview: str


class RagExportResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[RagPreview]


class RagDocumentResponse(BaseModel):
    doc_key: str
    source: str
    boost_score: float
    doc_text: str
    metadata: dict[str, Any]


class SearchResult(RagPreview):
    text_score: float


class SearchResponse(BaseModel):
    query: str
    total: int
    limit: int
    offset: int
    items: list[SearchResult]


router = APIRouter()


def _database_unavailable(exc: Exception) -> None:
    LOGGER.exception("PostgreSQL query failed", exc_info=exc)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="database unavailable",
    ) from exc


def _score(value: Any) -> float:
    return float(value) if value is not None else 0.0


@router.get("/live", response_model=HealthResponse)
def live() -> HealthResponse:
    """Sonde de vie du processus, indépendante de PostgreSQL."""
    return HealthResponse(status="ok", db="not_checked")


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
)
def health(pool: PoolDependency) -> HealthResponse | JSONResponse:
    """Vérifie que l'API répond et que PostgreSQL accepte une requête."""
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
    except DATABASE_ERRORS:
        LOGGER.warning("PostgreSQL health check failed", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "db": "error"},
        )
    return HealthResponse(status="ok", db="ok")


@router.get("/kitsu/{kitsu_id}", response_model=KitsuCoreResponse)
def get_kitsu_core(
    pool: PoolDependency,
    kitsu_id: Annotated[int, Path(ge=1)],
) -> KitsuCoreResponse:
    """Expose les métadonnées nettoyées d'un manga Kitsu."""
    sql = """
    SELECT kitsu_id, slug, title_canonical, synopsis_clean,
           rating_average_10, rating_rank, popularity_rank
    FROM manga.kitsu_series_core
    WHERE kitsu_id = %s
    """
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (kitsu_id,))
                row = cur.fetchone()
    except DATABASE_ERRORS as exc:
        _database_unavailable(exc)

    if row is None:
        raise HTTPException(status_code=404, detail="kitsu_id not found")

    return KitsuCoreResponse(
        kitsu_id=row[0],
        slug=row[1],
        title_canonical=row[2],
        synopsis_clean=row[3],
        rating_average_10=row[4],
        rating_rank=row[5],
        popularity_rank=row[6],
    )


@router.get("/rag/export", response_model=RagExportResponse)
def rag_export(
    pool: PoolDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0, le=20000)] = 0,
) -> RagExportResponse:
    """Renvoie un aperçu paginé des documents prêts pour la vectorisation."""
    sql = """
    SELECT doc_key, source, boost_score, left(doc_text, 500) AS preview
    FROM manga.rag_export_docs
    ORDER BY boost_score DESC NULLS LAST, doc_key
    LIMIT %s OFFSET %s
    """
    count_sql = "SELECT COUNT(*) FROM manga.rag_export_docs;"

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(count_sql)
                count_row = cur.fetchone()
                total = count_row[0] if count_row is not None else 0
                cur.execute(sql, (limit, offset))
                rows = cur.fetchall()
    except DATABASE_ERRORS as exc:
        _database_unavailable(exc)

    items = [
        RagPreview(
            doc_key=row[0],
            source=row[1],
            boost_score=_score(row[2]),
            preview=row[3],
        )
        for row in rows
    ]
    return RagExportResponse(total=int(total), limit=limit, offset=offset, items=items)


@router.get("/rag/doc/{doc_key}", response_model=RagDocumentResponse)
def rag_doc(
    pool: PoolDependency,
    doc_key: Annotated[str, Path(min_length=1, max_length=255)],
) -> RagDocumentResponse:
    """Récupère le texte complet et les métadonnées d'un document RAG."""
    sql = """
    SELECT doc_key, source, boost_score, doc_text, metadata_json
    FROM manga.rag_export_docs
    WHERE doc_key = %s
    """
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (doc_key,))
                row = cur.fetchone()
    except DATABASE_ERRORS as exc:
        _database_unavailable(exc)

    if row is None:
        raise HTTPException(status_code=404, detail="doc_key not found")

    return RagDocumentResponse(
        doc_key=row[0],
        source=row[1],
        boost_score=_score(row[2]),
        doc_text=row[3],
        metadata=row[4] or {},
    )


@router.get("/search", response_model=SearchResponse)
def search(
    pool: PoolDependency,
    q: Annotated[str, Query(min_length=2, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0, le=5000)] = 0,
) -> SearchResponse:
    """Recherche plein texte dans le corpus RAG avec un boost métier."""
    normalized_query = q.strip()
    if len(normalized_query) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="q must contain at least 2 non-whitespace characters",
        )

    sql = """
    WITH query AS (
      SELECT websearch_to_tsquery('simple', %s) AS tsq
    ), ranked AS (
      SELECT
        d.doc_key,
        d.source,
        coalesce(d.boost_score, 0.0) AS boost_score,
        ts_rank_cd(to_tsvector('simple', d.doc_text), query.tsq) AS text_score,
        left(d.doc_text, 300) AS preview
      FROM manga.rag_export_docs d
      CROSS JOIN query
      WHERE to_tsvector('simple', d.doc_text) @@ query.tsq
    )
    SELECT doc_key, source, boost_score, text_score, preview
    FROM ranked
    ORDER BY (text_score * 10.0 + boost_score) DESC, doc_key
    LIMIT %s OFFSET %s;
    """
    count_sql = """
    WITH query AS (
      SELECT websearch_to_tsquery('simple', %s) AS tsq
    )
    SELECT COUNT(*)
    FROM manga.rag_export_docs d
    CROSS JOIN query
    WHERE to_tsvector('simple', d.doc_text) @@ query.tsq;
    """

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(count_sql, (normalized_query,))
                count_row = cur.fetchone()
                total = count_row[0] if count_row is not None else 0
                cur.execute(sql, (normalized_query, limit, offset))
                rows = cur.fetchall()
    except DATABASE_ERRORS as exc:
        _database_unavailable(exc)

    items = [
        SearchResult(
            doc_key=row[0],
            source=row[1],
            boost_score=_score(row[2]),
            text_score=_score(row[3]),
            preview=row[4],
        )
        for row in rows
    ]
    return SearchResponse(
        query=normalized_query,
        total=int(total),
        limit=limit,
        offset=offset,
        items=items,
    )


@asynccontextmanager
async def lifespan(api: FastAPI) -> AsyncIterator[None]:
    """Ouvre le pool sans bloquer le démarrage et le ferme proprement."""
    pool = create_pool(api.state.settings)
    pool.open()
    api.state.db_pool = pool
    try:
        yield
    finally:
        pool.close()
        api.state.db_pool = None


def create_app(settings: Settings | None = None) -> FastAPI:
    """Fabrique l'application et permet d'injecter une configuration en test."""
    resolved_settings = settings or Settings.from_env()
    api = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        lifespan=lifespan,
    )
    api.state.settings = resolved_settings
    api.include_router(router)
    return api


app = create_app()
