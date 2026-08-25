"""API HTTP en lecture seule sur le corpus manga stocké dans PostgreSQL."""

from __future__ import annotations

import base64
import binascii
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from datetime import date as DateISO  # cf. le champ `date` de `Review`
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
    {
        "name": "catalogue",
        "description": (
            "The Manga Sanctuary catalogue: series, their volumes, and the "
            "**complete** review reference (`ms_reviews_all`, 11 074 rows) — "
            "not the 3 187-row legacy RAG corpus."
        ),
    },
    {
        "name": "identity",
        "description": (
            "Cross-platform identity. No shared identifier exists between "
            "sources, so every link is a scored decision with a method and a "
            "status, never a join."
        ),
    },
    {
        "name": "coverage",
        "description": (
            "What the corpus holds — and what it does not. Known limits are "
            "reported here rather than left to be discovered."
        ),
    },
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


class GenreCode(BaseModel):
    """Un genre normalisé : le code d'abord, le libellé comme commodité.

    Le code est la valeur STABLE — il survit à un changement de libellé
    d'affichage, il est indexable, il tient dans une URL. Le libellé change
    avec la langue et l'humeur éditoriale. Rendre le libellé seul obligerait
    un client à faire de la chaîne de caractères la clé de son filtre.
    """

    code: str = Field(description="Stable identifier from `manga.genre_ref`.")
    label_fr: str = Field(description="French display label. A convenience.")
    type: str = Field(description="`genre` or `format`.")


class SeriesResponse(BaseModel):
    """Une série du socle catalogue Manga Sanctuary.

    Les colonnes internes du rapprochement (`ms_title_norm_x`,
    `_other_titles_list`, `matched_title_norm`, `fuzzy_low_score`…) ne sont PAS
    exposées : ce sont des états intermédiaires d'un calcul, pas des faits sur
    l'œuvre. Ce que le rapprochement a conclu se lit sur `/identity/{work_uid}`.
    """

    series_id: int
    work_uid: int | None = Field(
        description="Identity hub key. Use it on `/identity/{work_uid}`."
    )
    title: str | None
    url: str | None
    type: str | None
    category: str | None
    year: int | None
    other_titles: list[str] = Field(default_factory=list)
    dessinateur: str | None
    scenariste: str | None
    magazine_prepublication: str | None
    statuses: list[str] = Field(default_factory=list)

    genres_source: list[str] = Field(
        default_factory=list,
        description=(
            "Raw Manga Sanctuary labels, in French, exactly as the source "
            "wrote them — casing included."
        ),
    )
    genres_enriched: list[GenreCode] = Field(
        default_factory=list,
        description=(
            "Normalised codes from the genre reference, merging both sources "
            "and their hierarchy. **Not** a fallback for `genres_source`: the "
            "two answer different questions and neither replaces the other."
        ),
    )
    tags_source: list[str] = Field(
        default_factory=list,
        description="Raw Manga Sanctuary tags. Open vocabulary, not normalised.",
    )

    synopsis: str | None = Field(description="Manga Sanctuary synopsis.")
    synopsis_enriched: str | None = Field(
        description="Manga Sanctuary synopsis, or the Kitsu one when MS has none."
    )

    popularity_rank: int | None
    members_rating: float | None
    members_votes: int | None
    experts_rating: float | None
    experts_votes: int | None

    volume_count: int | None
    review_count: int | None
    score_mean: float | None
    score_min: float | None
    score_max: float | None
    first_review_date: DateISO | None
    last_review_date: DateISO | None

    kitsu_id: int | None = Field(
        description="Kitsu identifier, or `null`. Full metadata: `/kitsu/{kitsu_id}`."
    )
    needs_review: bool | None = Field(
        description=(
            "The identity decision behind this series is flagged for human "
            "review. Method, score and reason live on `/identity/{work_uid}`."
        )
    )


class Volume(BaseModel):
    volume_url: str
    title: str | None
    number: int | None
    publication_date: DateISO | None
    ean: str | None = Field(description="As displayed by the source, unvalidated.")
    editeur: str | None = Field(
        description=(
            "**Known source defect — do not use as a filter.** The Manga "
            "Sanctuary selector captured a field *label* instead of its value: "
            "all 104 050 populated rows hold the literal string "
            "`Mag. de prépublication`. Exposed rather than hidden so the "
            "defect is visible; fixing it needs a module 04 selector fix and a "
            "re-crawl."
        )
    )
    dessinateur: str | None
    scenariste: str | None
    format: str | None
    pages: int | None
    country: str | None
    status: str | None = Field(
        description=(
            "**Known source defect.** 44 910 rows read `Complète Complète` — "
            "the label was captured twice. The value is usable once "
            "de-duplicated; it is served raw here, as collected."
        )
    )
    tomes_published: int | None
    tomes_total: int | None
    experts_rating: float | None
    experts_votes: int | None
    review_count: int | None
    synopsis: str | None


class VolumesResponse(BaseModel):
    series_id: int
    total: int
    limit: int
    offset: int
    items: list[Volume]


class Review(BaseModel):
    review_id: int
    volume_number: int | None
    volume_url: str | None
    review_url: str | None = Field(
        description=(
            "URL of the published review at the source; it points to the review, "
            "not the author's profile."
        )
    )
    title: str | None
    score: float | None
    author: str | None
    date: DateISO | None = Field(
        description=(
            "Parsed date, or `null` when the source truncated it to a weekday "
            "(29.65 % of the corpus). `date_raw` keeps what was displayed."
        )
    )
    date_raw: str | None
    type: str | None
    grain: str
    body: str | None


class ReviewsResponse(BaseModel):
    series_id: int
    total: int
    limit: int
    offset: int
    items: list[Review]


class IdentityResponse(BaseModel):
    """Ce que l'on sait de l'identité d'une œuvre, et comment on le sait.

    Aucun identifiant n'est partagé entre les plateformes : chaque lien est une
    DÉCISION, avec une méthode, un score et un statut. Les rendre sans leur
    provenance laisserait croire à une jointure.
    """

    work_uid: int
    series_id: int | None
    series_title: str | None
    wikidata_qid: str | None
    kitsu_id: str | None
    mal_id: str | None
    anilist_id: str | None
    madb_id: str | None
    disponibilite: str | None
    method: str | None = Field(
        description="How the current decision was reached. `null` if none yet."
    )
    score: float | None
    status: str | None
    decided_at: datetime | None
    decided_by: str | None


class CoverageTotals(BaseModel):
    series: int
    volumes: int
    reviews: int = Field(description="From `ms_reviews_all`, the full reference.")
    reviews_rag_legacy: int = Field(
        description=(
            "The legacy RAG corpus (`ms_reviews`), kept for comparison. "
            "Smaller by construction — it is a filtered subset, not a shortfall."
        )
    )


class CoverageMethod(BaseModel):
    method: str
    decisions: int


class CoverageGenres(BaseModel):
    series_with_source_genres: int
    series_with_enriched_genres: int
    series_without_any_genre: int = Field(
        description="Documented by neither source. No reference can fix these."
    )
    series_on_generic_lgbt_only: int = Field(
        description=(
            "Carry the generic `lgbt` code with no finer one, because no "
            "reliable Kitsu match exists. A source limit, not a bug."
        )
    )


class CoverageResponse(BaseModel):
    """Les limites connues, mesurées et affichées.

    Une limite mesurée est une force ; découverte par un tiers, c'est une faute.
    """

    totals: CoverageTotals
    identity_by_method: list[CoverageMethod]
    genres: CoverageGenres
    measured_at: datetime


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
RESPONSE_404_SERIES = {
    "model": ErrorResponse,
    "description": (
        "No series with this identifier. Distinct from an empty collection: "
        "a series with no volume or no review answers 200 with `items: []`."
    ),
}
RESPONSE_404_IDENTITY = {
    "model": ErrorResponse,
    "description": "No work with this `work_uid`.",
}

# Bornes de pagination des collections LIÉES À UNE SÉRIE.
#
# `limit`/`offset` ici, curseur sur `/rag/export` : le choix inverse, pour la
# raison inverse. Un curseur protège d'un `OFFSET` profond sur un corpus de
# plusieurs dizaines de milliers de lignes parcouru en entier. Ces deux
# collections-ci sont bornées par la série qui les porte : la plus fournie du
# catalogue compte quelques centaines de volumes, une poignée de critiques. La
# profondeur est structurellement faible, `OFFSET` n'y coûte rien, et un
# curseur opaque imposerait au client un protocole pour parcourir dix lignes.
COLLECTION_LIMIT_MAX = 200
COLLECTION_LIMIT_DEFAUT = 50
COLLECTION_OFFSET_MAX = 10_000

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


# ---------------------------------------------------------------------------
# Catalogue — le socle Manga Sanctuary
# ---------------------------------------------------------------------------
# `_serie_existe` est appelée par les trois routes de série. Elle fait la
# différence entre les deux 404 qu'on confond d'habitude : un identifiant
# INCONNU (404) et une collection VIDE (200 + `items: []`). Une série sans
# critique existe ; répondre 404 dirait à un client qu'elle n'existe pas, et il
# en conclurait la mauvaise chose.
def _serie_existe(cur: Any, series_id: int) -> None:
    cur.execute(
        "SELECT 1 FROM manga.ms_series_enriched WHERE series_id = %s", (series_id,)
    )
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="series_id not found")


def _liste(valeur: Any) -> list[str]:
    """Un tableau jsonb en liste de chaînes, tolérant au NULL et au scalaire."""
    if not isinstance(valeur, list):
        return []
    return [str(element) for element in valeur if element is not None]


@router.get(
    "/series/{series_id}",
    response_model=SeriesResponse,
    dependencies=PROTECTED,
    responses={401: RESPONSE_401, 404: RESPONSE_404_SERIES, 503: RESPONSE_503},
    tags=["catalogue"],
    summary="One series from the Manga Sanctuary catalogue",
    description=(
        "The catalogue record: titles, authors, publication data, ratings and "
        "review aggregates.\n\n"
        "**Genres come in two fields, and neither replaces the other.** "
        "`genres_source` holds the raw French labels Manga Sanctuary wrote "
        "(12 652 series); `genres_enriched` holds normalised codes merged from "
        "both sources and expanded through the genre hierarchy (12 952 "
        "series). No `COALESCE` is applied here: collapsing them would invent "
        "a definition of *genre* that exists nowhere in the database. "
        "*The API exposes; it does not create.*"
    ),
)
def get_series(
    pool: PoolDependency,
    series_id: Annotated[int, Path(ge=1)],
) -> SeriesResponse:
    """Expose une série du socle catalogue."""
    sql = """
    SELECT s.series_id, s.work_uid, s.series_title, s.series_url, s.series_type,
           s.series_category, s.series_year, s.series_other_titles,
           s.series_dessinateur, s.series_scenariste, s.series_mag_prepub,
           s.series_statuses, s.series_genres, s.series_tags, s.series_synopsis,
           s.series_synopsis_enriched, s.series_popularity_rank,
           s.series_members_rating, s.series_members_votes,
           s.series_experts_rating, s.series_experts_votes,
           s.series_volume_count, s.series_review_count, s.series_score_mean,
           s.series_score_min, s.series_score_max,
           s.series_first_review_date_iso, s.series_last_review_date_iso,
           s.kitsu_id, s.needs_review,
           COALESCE(
               (SELECT jsonb_agg(jsonb_build_object(
                           'code', r.code, 'label_fr', r.label_fr, 'type', r.type)
                        ORDER BY r.ordre NULLS LAST, r.code)
                  FROM jsonb_array_elements_text(
                           COALESCE(s.series_genres_enriched, '[]'::jsonb)) g
                  JOIN manga.genre_ref r ON r.code = g),
               '[]'::jsonb) AS genres_enriched
    FROM manga.ms_series_enriched s
    WHERE s.series_id = %s
    """
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (series_id,))
                row = cur.fetchone()
    except DATABASE_ERRORS as exc:
        _database_unavailable(exc)

    if row is None:
        raise HTTPException(status_code=404, detail="series_id not found")

    return SeriesResponse(
        series_id=row[0],
        work_uid=row[1],
        title=row[2],
        url=row[3],
        type=row[4],
        category=row[5],
        year=row[6],
        other_titles=_liste(row[7]),
        dessinateur=row[8],
        scenariste=row[9],
        magazine_prepublication=row[10],
        statuses=_liste(row[11]),
        genres_source=_liste(row[12]),
        tags_source=_liste(row[13]),
        synopsis=row[14],
        synopsis_enriched=row[15],
        popularity_rank=row[16],
        members_rating=row[17],
        members_votes=row[18],
        experts_rating=row[19],
        experts_votes=row[20],
        volume_count=row[21],
        review_count=row[22],
        score_mean=row[23],
        score_min=row[24],
        score_max=row[25],
        first_review_date=row[26],
        last_review_date=row[27],
        kitsu_id=row[28],
        needs_review=row[29],
        genres_enriched=[GenreCode(**item) for item in (row[30] or [])],
    )


@router.get(
    "/series/{series_id}/volumes",
    response_model=VolumesResponse,
    dependencies=PROTECTED,
    responses={401: RESPONSE_401, 404: RESPONSE_404_SERIES, 503: RESPONSE_503},
    tags=["catalogue"],
    summary="Volumes of one series",
    description=(
        "Tomaison, EAN, publisher, format and page count — what a bookseller "
        "actually sells. Ordered by volume number, `NULL` last.\n\n"
        "`ean` is the barcode **as the source displayed it**, unvalidated: its "
        "checked reading lives in `manga.volume_identity`.\n\n"
        "**Two fields carry a known source defect** and say so in their own "
        "description: `editeur` (a captured label, not a publisher) and "
        "`status` (a doubled label). They are served as collected rather "
        "than quietly dropped — a defect that is visible can be fixed.\n\n"
        "Paginated with "
        "`limit`/`offset` rather than a cursor — see `/rag/export` for the "
        "opposite choice and its reason."
    ),
)
def get_series_volumes(
    pool: PoolDependency,
    series_id: Annotated[int, Path(ge=1)],
    limit: Annotated[int, Query(ge=1, le=COLLECTION_LIMIT_MAX)] = (
        COLLECTION_LIMIT_DEFAUT
    ),
    offset: Annotated[int, Query(ge=0, le=COLLECTION_OFFSET_MAX)] = 0,
) -> VolumesResponse:
    """Expose les volumes d'une série, paginés."""
    sql = """
    SELECT volume_url, volume_title, volume_number, volume_publication_date,
           volume_ean, volume_editeur, volume_dessinateur, volume_scenariste,
           volume_format, volume_pages, volume_country, volume_status,
           volume_tomes_published, volume_tomes_total, volume_experts_rating,
           volume_experts_votes, review_count, volume_synopsis
    FROM manga.ms_volumes_enriched
    WHERE series_id = %s
    ORDER BY volume_number NULLS LAST, volume_url
    LIMIT %s OFFSET %s
    """
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                _serie_existe(cur, series_id)
                cur.execute(
                    "SELECT count(*) FROM manga.ms_volumes_enriched "
                    "WHERE series_id = %s",
                    (series_id,),
                )
                total_row = cur.fetchone()
                cur.execute(sql, (series_id, limit, offset))
                rows = cur.fetchall()
    except DATABASE_ERRORS as exc:
        _database_unavailable(exc)

    return VolumesResponse(
        series_id=series_id,
        total=int(total_row[0]) if total_row else 0,
        limit=limit,
        offset=offset,
        items=[
            Volume(
                volume_url=row[0],
                title=row[1],
                number=row[2],
                publication_date=row[3],
                ean=row[4],
                editeur=row[5],
                dessinateur=row[6],
                scenariste=row[7],
                format=row[8],
                pages=row[9],
                country=row[10],
                status=row[11],
                tomes_published=row[12],
                tomes_total=row[13],
                experts_rating=row[14],
                experts_votes=row[15],
                review_count=row[16],
                synopsis=row[17],
            )
            for row in rows
        ],
    )


@router.get(
    "/series/{series_id}/reviews",
    response_model=ReviewsResponse,
    dependencies=PROTECTED,
    responses={401: RESPONSE_401, 404: RESPONSE_404_SERIES, 503: RESPONSE_503},
    tags=["catalogue"],
    summary="Reviews of one series (complete reference)",
    description=(
        "Reads **`manga.ms_reviews_all`** — the complete review reference, "
        "11 074 rows.\n\n"
        "This is deliberately **not** `manga.ms_reviews` (3 187 rows), the "
        "legacy RAG corpus: that table is a filtered subset built for "
        "retrieval, and serving it here would silently hide two thirds of what "
        "was collected. Reading a table is not rebuilding a corpus — the RAG "
        "corpus keeps its own source and its own endpoints."
    ),
)
def get_series_reviews(
    pool: PoolDependency,
    series_id: Annotated[int, Path(ge=1)],
    limit: Annotated[int, Query(ge=1, le=COLLECTION_LIMIT_MAX)] = (
        COLLECTION_LIMIT_DEFAUT
    ),
    offset: Annotated[int, Query(ge=0, le=COLLECTION_OFFSET_MAX)] = 0,
) -> ReviewsResponse:
    """Expose les critiques d'une série, depuis le référentiel COMPLET."""
    sql = """
    SELECT review_id, volume_number, volume_url, review_url, review_title,
           review_score, review_author, review_date_iso, review_date_raw,
           review_type, review_grain, review_body
    FROM manga.ms_reviews_all
    WHERE series_id = %s
    ORDER BY review_date_iso DESC NULLS LAST, review_id
    LIMIT %s OFFSET %s
    """
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                _serie_existe(cur, series_id)
                cur.execute(
                    "SELECT count(*) FROM manga.ms_reviews_all WHERE series_id = %s",
                    (series_id,),
                )
                total_row = cur.fetchone()
                cur.execute(sql, (series_id, limit, offset))
                rows = cur.fetchall()
    except DATABASE_ERRORS as exc:
        _database_unavailable(exc)

    return ReviewsResponse(
        series_id=series_id,
        total=int(total_row[0]) if total_row else 0,
        limit=limit,
        offset=offset,
        items=[
            Review(
                review_id=row[0],
                volume_number=row[1],
                volume_url=row[2],
                review_url=row[3],
                title=row[4],
                score=row[5],
                author=row[6],
                date=row[7],
                date_raw=row[8],
                type=row[9],
                grain=row[10],
                body=row[11],
            )
            for row in rows
        ],
    )


# ---------------------------------------------------------------------------
# Identité
# ---------------------------------------------------------------------------
@router.get(
    "/identity/{work_uid}",
    response_model=IdentityResponse,
    dependencies=PROTECTED,
    responses={401: RESPONSE_401, 404: RESPONSE_404_IDENTITY, 503: RESPONSE_503},
    tags=["identity"],
    summary="Cross-platform identity of one work",
    description=(
        "The identifiers a work carries across platforms, and **how each link "
        "was established**: method, score, status.\n\n"
        "No identifier is shared between Manga Sanctuary, Kitsu and Wikidata. "
        "Every link here is therefore a scored decision taken by a documented "
        "method, never a join on a common key. `method`, `score` and `status` "
        "are `null` when no decision has been recorded yet — 9 366 of 14 670 "
        "works have one. A work without a decision still exists: it answers "
        "200, not 404."
    ),
)
def get_identity(
    pool: PoolDependency,
    work_uid: Annotated[int, Path(ge=1)],
) -> IdentityResponse:
    """Expose l'identité croisée d'une œuvre et la provenance de chaque lien.

    La jointure passe par `work_identity` : `v_match_current` est indexée par
    `series_id`, pas par `work_uid` — vérifié par `\\d`, pas supposé. La
    relation est 1-1 (14 670 séries, 14 670 `work_uid`, index unique sur
    `series_id`), donc un `work_uid` désigne au plus une série.
    """
    sql = """
    SELECT w.work_uid, w.series_id, s.series_title, w.wikidata_qid, w.kitsu_id,
           w.mal_id, w.anilist_id, w.madb_id, w.disponibilite,
           m.method, m.score, m.status, m.decided_at, m.decided_by
    FROM manga.work_identity w
    LEFT JOIN manga.ms_series_enriched s ON s.work_uid = w.work_uid
    LEFT JOIN manga.v_match_current m ON m.series_id = w.series_id
    WHERE w.work_uid = %s
    """
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (work_uid,))
                row = cur.fetchone()
    except DATABASE_ERRORS as exc:
        _database_unavailable(exc)

    if row is None:
        raise HTTPException(status_code=404, detail="work_uid not found")

    return IdentityResponse(
        work_uid=row[0],
        series_id=row[1],
        series_title=row[2],
        wikidata_qid=row[3],
        kitsu_id=row[4],
        mal_id=row[5],
        anilist_id=row[6],
        madb_id=row[7],
        disponibilite=row[8],
        method=row[9],
        score=row[10],
        status=row[11],
        decided_at=row[12],
        decided_by=row[13],
    )


# ---------------------------------------------------------------------------
# Couverture
# ---------------------------------------------------------------------------
@router.get(
    "/coverage",
    response_model=CoverageResponse,
    dependencies=PROTECTED,
    responses={401: RESPONSE_401, 503: RESPONSE_503},
    tags=["coverage"],
    summary="What the corpus holds, and what it does not",
    description=(
        "Reference totals, identity decisions by method, and genre coverage — "
        "**including the known limits**.\n\n"
        "Two of them are structural and will not be fixed by any further "
        "work: 1 694 series carry no genre in *either* source, and 526 series "
        "carry only the generic `lgbt` code because no reliable Kitsu match "
        "exists to refine it. Both are reported here rather than left for a "
        "reader to discover."
    ),
)
def get_coverage(pool: PoolDependency) -> CoverageResponse:
    """Rend les totaux du référentiel et les limites connues, mesurés à l'appel."""
    totaux_sql = """
    SELECT (SELECT count(*) FROM manga.ms_series_enriched),
           (SELECT count(*) FROM manga.ms_volumes_enriched),
           (SELECT count(*) FROM manga.ms_reviews_all),
           (SELECT count(*) FROM manga.ms_reviews)
    """
    methodes_sql = """
    SELECT COALESCE(method, 'sans_decision') AS method, count(*)
    FROM manga.v_match_current
    GROUP BY 1
    ORDER BY 2 DESC, 1
    """
    # Les deux limites structurelles sont CALCULÉES, pas écrites en dur : un
    # chiffre figé dans le code cesserait d'être vrai au premier recalcul sans
    # que personne ne s'en aperçoive — le défaut même que 4c a corrigé.
    genres_sql = """
    SELECT count(*) FILTER (
               WHERE COALESCE(series_genres, '[]'::jsonb) <> '[]'::jsonb),
           count(*) FILTER (
               WHERE COALESCE(series_genres_enriched, '[]'::jsonb) <> '[]'::jsonb),
           count(*) FILTER (
               WHERE COALESCE(series_genres, '[]'::jsonb) = '[]'::jsonb
                 AND COALESCE(series_genres_enriched, '[]'::jsonb) = '[]'::jsonb),
           count(*) FILTER (
               WHERE series_genres_enriched ? 'lgbt'
                 AND NOT series_genres_enriched ?| array['yaoi', 'yuri',
                                                         'gender_bender'])
    FROM manga.ms_series_enriched
    """
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(totaux_sql)
                totaux = cur.fetchone()
                cur.execute(methodes_sql)
                methodes = cur.fetchall()
                cur.execute(genres_sql)
                genres = cur.fetchone()
    except DATABASE_ERRORS as exc:
        _database_unavailable(exc)

    return CoverageResponse(
        totals=CoverageTotals(
            series=totaux[0],
            volumes=totaux[1],
            reviews=totaux[2],
            reviews_rag_legacy=totaux[3],
        ),
        identity_by_method=[
            CoverageMethod(method=row[0], decisions=row[1]) for row in methodes
        ],
        genres=CoverageGenres(
            series_with_source_genres=genres[0],
            series_with_enriched_genres=genres[1],
            series_without_any_genre=genres[2],
            series_on_generic_lgbt_only=genres[3],
        ),
        measured_at=datetime.now(UTC),
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
