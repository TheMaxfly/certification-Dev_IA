"""API HTTP en lecture seule sur le corpus manga stocké dans PostgreSQL."""

from __future__ import annotations

import base64
import binascii
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Path,
    Query,
    Security,
    status,
)
from fastapi.responses import JSONResponse
from psycopg import Error as PsycopgError
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout
from pydantic import BaseModel, Field

from . import API_VERSION
from .database import create_pool, get_pool
from .security import api_key_header, require_api_key
from .settings import Settings

LOGGER = logging.getLogger(__name__)
DATABASE_ERRORS = (PsycopgError, PoolClosed, PoolTimeout)
PoolDependency = Annotated[ConnectionPool, Depends(get_pool)]

# Borne de `doc_key` telle qu'elle est déjà appliquée par `/rag/doc/{doc_key}`.
# Elle sert aussi à refuser un curseur dont le contenu décodé ne peut pas être
# une clé de document.
DOC_KEY_MAX_LENGTH = 255

TAGS_METADATA = [
    {
        "name": "probes",
        "description": ("Liveness and readiness probes. Open: no API key required."),
    },
    {"name": "kitsu", "description": "Cleaned Kitsu series metadata."},
    {
        "name": "rag",
        "description": (
            "The RAG-ready corpus. `/rag/preview` is a truncated, "
            "relevance-ordered sample; `/rag/export` is the exhaustive, "
            "cursor-paginated feed."
        ),
    },
    {"name": "search", "description": "PostgreSQL full-text search."},
]

APP_DESCRIPTION = """
Read-only HTTP API over the manga corpus stored in PostgreSQL (schema `manga`).

**Authorisation.** Data endpoints require an `X-API-Key` header. The
documentation (`/docs`, `/redoc`, `/openapi.json`) and the probes (`/live`,
`/health`) are open by design: *the documentation is open, the data is closed.*
"""


class ErrorResponse(BaseModel):
    detail: str


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
    preview: str = Field(description="First 500 characters of `doc_text`.")


class RagPreviewResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[RagPreview]


class RagExportDocument(BaseModel):
    doc_key: str
    source: str
    boost_score: float
    doc_text: str = Field(description="Full document text, never truncated.")
    metadata: dict[str, Any]


class RagExportPage(BaseModel):
    limit: int
    next_cursor: str | None = Field(
        description=(
            "Opaque cursor for the next page, or `null` once the corpus is "
            "exhausted. Pass it back as the `cursor` query parameter."
        )
    )
    items: list[RagExportDocument]


class RagSourceCount(BaseModel):
    source: str
    documents: int


class RagCompositionResponse(BaseModel):
    total: int
    by_source: list[RagSourceCount]
    measured_at: datetime = Field(
        description="UTC timestamp at which the counts were measured."
    )


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


# Réponses déclarées dans OpenAPI. Elles font partie du contrat : un client qui
# lit le schéma doit savoir qu'un 401 ou un 503 est possible sans avoir à le
# découvrir en production.
RESPONSE_401 = {
    "model": ErrorResponse,
    "description": "Missing or invalid API key.",
}
RESPONSE_503 = {
    "model": ErrorResponse,
    "description": "PostgreSQL is unavailable.",
}
RESPONSE_404_KITSU = {
    "model": ErrorResponse,
    "description": "No Kitsu series with this identifier.",
}
RESPONSE_404_DOC = {
    "model": ErrorResponse,
    "description": "No RAG document with this key.",
}

# Dépendance d'autorisation, posée route par route plutôt que sur le routeur :
# la liste des routes protégées doit se lire à l'endroit où les routes sont
# définies, pas se déduire d'une exception globale.
PROTECTED = [Security(require_api_key)]

router = APIRouter()


def _database_unavailable(exc: Exception) -> None:
    LOGGER.exception("PostgreSQL query failed", exc_info=exc)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="database unavailable",
    ) from exc


def _score(value: Any) -> float:
    return float(value) if value is not None else 0.0


def encode_cursor(doc_key: str) -> str:
    """Encode une position de parcours en curseur opaque."""
    return base64.urlsafe_b64encode(doc_key.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> str:
    """Décode un curseur, ou lève 422.

    Le curseur est opaque, pas signé : un curseur BIEN FORMÉ mais fabriqué à la
    main est honoré comme une position de parcours quelconque, ce qui est sans
    danger — il n'est jamais interpolé, seulement lié comme paramètre. Ce que
    cette fonction garantit, c'est qu'un curseur ILLISIBLE produit un 422 et
    jamais une 500.
    """
    invalid = HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="cursor is not a valid pagination cursor",
    )
    try:
        # `validate=True` : sans lui, base64 ignore silencieusement les
        # caractères hors alphabet et accepte n'importe quelle chaîne.
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        doc_key = raw.decode("utf-8")
    except (binascii.Error, ValueError) as exc:
        raise invalid from exc

    if not doc_key or len(doc_key) > DOC_KEY_MAX_LENGTH:
        raise invalid
    return doc_key


@router.get(
    "/live",
    response_model=HealthResponse,
    tags=["probes"],
    summary="Liveness probe",
    description="Checks the FastAPI process answers. Never touches PostgreSQL.",
)
def live() -> HealthResponse:
    """Sonde de vie du processus, indépendante de PostgreSQL."""
    return HealthResponse(status="ok", db="not_checked")


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
    tags=["probes"],
    summary="Readiness probe",
    description="Checks the API answers and PostgreSQL accepts a query.",
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


@router.get(
    "/kitsu/{kitsu_id}",
    response_model=KitsuCoreResponse,
    dependencies=PROTECTED,
    responses={401: RESPONSE_401, 404: RESPONSE_404_KITSU, 503: RESPONSE_503},
    tags=["kitsu"],
    summary="Kitsu series metadata",
    description="Returns the cleaned Kitsu metadata for one series.",
)
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


@router.get(
    "/rag/preview",
    response_model=RagPreviewResponse,
    dependencies=PROTECTED,
    responses={401: RESPONSE_401, 503: RESPONSE_503},
    tags=["rag"],
    summary="Truncated sample of the RAG corpus",
    description=(
        "Ranked sample of the RAG corpus, ordered by business relevance. "
        "**This endpoint is deliberately neither exhaustive nor complete:** "
        "`doc_text` is cut at 500 characters and `offset` is capped, so the "
        "deep end of the corpus is unreachable here. That is what a preview "
        "is. To read the whole corpus, use `/rag/export`."
    ),
)
def rag_preview(
    pool: PoolDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0, le=20000)] = 0,
) -> RagPreviewResponse:
    """Renvoie un aperçu paginé, tronqué et trié par pertinence.

    Contrat d'origine de l'ancien `/rag/export`, conservé tel quel : mêmes
    plafonds, même troncature à 500 caractères, même tri par `boost_score`.
    Ces trois propriétés sont justes pour un aperçu et rédhibitoires pour un
    export — d'où la séparation en deux endpoints.
    """
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
    return RagPreviewResponse(total=int(total), limit=limit, offset=offset, items=items)


@router.get(
    "/rag/export",
    response_model=RagExportPage,
    dependencies=PROTECTED,
    responses={401: RESPONSE_401, 503: RESPONSE_503},
    tags=["rag"],
    summary="Exhaustive cursor-paginated export of the RAG corpus",
    description=(
        "Streams the entire RAG corpus, in full text, ordered by `doc_key`. "
        "Pagination is by opaque cursor and has no depth limit: follow "
        "`next_cursor` until it is `null` and every document has been seen "
        "exactly once. A page returning fewer than `limit` items is the last "
        "one; when the corpus size is an exact multiple of `limit`, a final "
        "empty page closes the walk."
    ),
)
def rag_export(
    pool: PoolDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    cursor: Annotated[
        str | None,
        Query(description="Opaque cursor returned as `next_cursor`."),
    ] = None,
) -> RagExportPage:
    """Export exhaustif du corpus RAG, en texte intégral, par curseur.

    `offset` a été RETIRÉ : un `OFFSET` plafonné rend le fond du corpus
    inatteignable, et un `OFFSET` profond coûte un parcours complet à chaque
    page. Le curseur borne la lecture par `doc_key` et se paie en temps constant.

    `COLLATE "C"` : l'ordre de parcours n'a aucune signification métier — il
    sert seulement à garantir qu'un document est vu une fois et une seule. Une
    comparaison octet par octet est stable quelle que soit la collation de la
    base ou de la locale système ; un tri linguistique, lui, peut changer entre
    deux versions d'ICU et faire silencieusement sauter des lignes en cours de
    parcours. Le prédicat et le tri portent la MÊME collation, sans quoi
    l'ordre et la borne divergeraient.

    `doc_key` est unique (51 880 lignes, 51 880 valeurs distinctes) : l'ordre
    est total et n'appelle aucune colonne de départage.
    """
    columns = "doc_key, source, boost_score, doc_text, metadata_json"
    if cursor is None:
        sql = f"""
        SELECT {columns}
        FROM manga.rag_export_docs
        ORDER BY doc_key COLLATE "C"
        LIMIT %s
        """
        params: tuple[Any, ...] = (limit,)
    else:
        sql = f"""
        SELECT {columns}
        FROM manga.rag_export_docs
        WHERE doc_key COLLATE "C" > %s
        ORDER BY doc_key COLLATE "C"
        LIMIT %s
        """
        params = (decode_cursor(cursor), limit)

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
    except DATABASE_ERRORS as exc:
        _database_unavailable(exc)

    items = [
        RagExportDocument(
            doc_key=row[0],
            source=row[1],
            boost_score=_score(row[2]),
            doc_text=row[3],
            metadata=row[4] or {},
        )
        for row in rows
    ]
    next_cursor = encode_cursor(items[-1].doc_key) if len(items) == limit else None
    return RagExportPage(limit=limit, next_cursor=next_cursor, items=items)


@router.get(
    "/rag/export/composition",
    response_model=RagCompositionResponse,
    dependencies=PROTECTED,
    responses={401: RESPONSE_401, 503: RESPONSE_503},
    tags=["rag"],
    summary="Size and composition of the RAG corpus",
    description=(
        "Total document count and breakdown by source, with the timestamp of "
        "the measurement. Cursor pagination returns no total, so this is where "
        "a consumer learns how much there is to read before walking "
        "`/rag/export`."
    ),
)
def rag_export_composition(pool: PoolDependency) -> RagCompositionResponse:
    """Taille et composition du corpus, mesurées à la demande.

    Cet endpoint existe par conception, non par confort : une pagination par
    curseur ne peut renvoyer aucun total, et le `COUNT(*)` — mesuré à ~330 ms —
    disparaît ainsi du coût de CHAQUE page. Effet second assumé : la
    composition du corpus (`kitsu_synopsis` / `ms_hybrid` / `ms_review`)
    devient explicite au lieu de rester tacite.

    Un seul `GROUP BY` sert le total et le détail : deux requêtes séparées
    pourraient être mesurées à des instants différents et ne plus s'additionner.
    """
    sql = """
    SELECT source, COUNT(*)
    FROM manga.rag_export_docs
    GROUP BY source
    ORDER BY source
    """
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
    except DATABASE_ERRORS as exc:
        _database_unavailable(exc)

    by_source = [RagSourceCount(source=row[0], documents=int(row[1])) for row in rows]
    return RagCompositionResponse(
        total=sum(entry.documents for entry in by_source),
        by_source=by_source,
        measured_at=datetime.now(UTC),
    )


@router.get(
    "/rag/doc/{doc_key}",
    response_model=RagDocumentResponse,
    dependencies=PROTECTED,
    responses={401: RESPONSE_401, 404: RESPONSE_404_DOC, 503: RESPONSE_503},
    tags=["rag"],
    summary="One RAG document, in full",
    description="Returns the full text and metadata of a single RAG document.",
)
def rag_doc(
    pool: PoolDependency,
    doc_key: Annotated[str, Path(min_length=1, max_length=DOC_KEY_MAX_LENGTH)],
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


@router.get(
    "/search",
    response_model=SearchResponse,
    dependencies=PROTECTED,
    responses={401: RESPONSE_401, 503: RESPONSE_503},
    tags=["search"],
    summary="Full-text search over the RAG corpus",
    description=(
        "PostgreSQL full-text search, ranked by text score combined with the "
        "business boost. Previews are cut at 300 characters."
    ),
)
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


def configure_logging() -> None:
    """Donne un handler au journal applicatif, faute de quoi il est muet.

    Sans configuration, Python n'installe que le `lastResort` handler, qui
    n'émet qu'à partir de WARNING : les refus d'autorisation apparaissaient,
    mais pas le libellé du consommateur d'un appel autorisé. Un journal
    d'autorisation qui ne consigne que les échecs ne permet pas de répondre à
    « qui a lu quoi » — c'est la question à laquelle il doit servir.

    `basicConfig` sans `force` ne fait rien si l'hôte a déjà configuré le
    journal : on n'écrase pas la configuration d'un déploiement.
    """
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Fabrique l'application et permet d'injecter une configuration en test.

    Sans `settings` explicite, `Settings.from_env()` valide le trousseau
    `API_KEYS` et LÈVE si celui-ci est absent, vide ou malformé : l'application
    n'est alors jamais construite. C'est la défaillance fermée — il n'existe
    pas de démarrage en accès ouvert.
    """
    configure_logging()
    resolved_settings = settings or Settings.from_env()
    api = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        description=APP_DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
    )
    api.state.settings = resolved_settings
    api.include_router(router)
    return api


__all__ = ["API_VERSION", "api_key_header", "app", "create_app", "router"]

app = create_app()
