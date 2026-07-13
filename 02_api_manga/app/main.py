import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from psycopg_pool import ConnectionPool

app = FastAPI(title="ApiManga API", version="0.2.0")

# Pool global (simple et efficace)
POOL: ConnectionPool | None = None


@app.on_event("startup")
def startup() -> None:
    global POOL
    conninfo = (
        f"host={os.getenv('DB_HOST', 'host.docker.internal')} "
        f"port={os.getenv('DB_PORT', '5432')} "
        f"dbname={os.getenv('DB_NAME', 'apimanga')} "
        f"user={os.getenv('DB_USER', 'postgres')} "
        f"password={os.getenv('DB_PASSWORD', '')} "
        f"connect_timeout=5"
    )
    POOL = ConnectionPool(conninfo=conninfo, min_size=1, max_size=5)


@app.on_event("shutdown")
def shutdown() -> None:
    global POOL
    if POOL is not None:
        POOL.close()
        POOL = None


def get_conn():
    if POOL is None:
        raise RuntimeError("DB pool not initialized")
    return POOL.connection()


@app.get("/health")
def health() -> dict[str, Any]:
    """
    Vérifie que l’API répond + que la DB est joignable.
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        return {"status": "ok", "db": "error", "detail": str(e)}


@app.get("/kitsu/{kitsu_id}")
def get_kitsu_core(kitsu_id: int) -> dict[str, Any]:
    """
    Lecture simple (kitsu_series_core) : utile pour exposer les métadonnées Kitsu.
    """
    sql = """
    SELECT kitsu_id, slug, title_canonical, synopsis_clean,
           rating_average_10, rating_rank, popularity_rank
    FROM manga.kitsu_series_core
    WHERE kitsu_id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (kitsu_id,))
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="kitsu_id not found")

    keys = [
        "kitsu_id",
        "slug",
        "title_canonical",
        "synopsis_clean",
        "rating_average_10",
        "rating_rank",
        "popularity_rank",
    ]
    return dict(zip(keys, row, strict=False))


@app.get("/rag/export")
def rag_export(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0, le=20000),
) -> dict[str, Any]:
    """
    Renvoie un extrait des documents prêts à exporter pour la vectorisation.
    - limit/offset : pagination
    - preview : tronque doc_text pour rester léger
    """
    sql = """
    SELECT doc_key, source, boost_score, left(doc_text, 500) AS preview
    FROM manga.rag_export_docs
    ORDER BY boost_score DESC NULLS LAST
    LIMIT %s OFFSET %s
    """
    count_sql = "SELECT COUNT(*) FROM manga.rag_export_docs;"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(count_sql)
            total = cur.fetchone()[0]

            cur.execute(sql, (limit, offset))
            rows = cur.fetchall()

    items = [
        {
            "doc_key": r[0],
            "source": r[1],
            "boost_score": float(r[2]),
            "preview": r[3],
        }
        for r in rows
    ]
    return {"total": int(total), "limit": limit, "offset": offset, "items": items}


@app.get("/rag/doc/{doc_key}")
def rag_doc(doc_key: str) -> dict[str, Any]:
    """
    Récupère un document complet (doc_text complet + metadata_json).
    """
    sql = """
    SELECT doc_key, source, boost_score, doc_text, metadata_json
    FROM manga.rag_export_docs
    WHERE doc_key = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (doc_key,))
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="doc_key not found")

    return {
        "doc_key": row[0],
        "source": row[1],
        "boost_score": float(row[2]),
        "doc_text": row[3],
        "metadata": row[4],
    }


@app.get("/search")
def search(
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0, le=5000),
) -> dict[str, Any]:
    """
    Recherche plein texte PostgreSQL (FTS) dans le corpus RAG.
    - q: requête utilisateur (mots)
    - retourne doc_key + source + scores
    """
    # Simple : websearch_to_tsquery supporte une requête "naturelle" (avec espaces)
    # 'simple' évite les règles de langue trop agressives (FR/EN mélangés)
    sql = """
    WITH q AS (
      SELECT websearch_to_tsquery('simple', %s) AS tsq
    )
    SELECT
      d.doc_key,
      d.source,
      d.boost_score,
      ts_rank_cd(to_tsvector('simple', d.doc_text), q.tsq) AS text_score,
      left(d.doc_text, 300) AS preview
    FROM manga.rag_export_docs d, q
    WHERE to_tsvector('simple', d.doc_text) @@ q.tsq
    ORDER BY
      (ts_rank_cd(to_tsvector('simple', d.doc_text), q.tsq) * 10.0 + d.boost_score) DESC
    LIMIT %s OFFSET %s;
    """

    count_sql = """
    WITH q AS (
      SELECT websearch_to_tsquery('simple', %s) AS tsq
    )
    SELECT COUNT(*)
    FROM manga.rag_export_docs d, q
    WHERE to_tsvector('simple', d.doc_text) @@ q.tsq;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(count_sql, (q,))
            total = cur.fetchone()[0]

            cur.execute(sql, (q, limit, offset))
            rows = cur.fetchall()

    items = []
    for r in rows:
        items.append(
            {
                "doc_key": r[0],
                "source": r[1],
                "boost_score": float(r[2]),
                "text_score": float(r[3]),
                "preview": r[4],
            }
        )

    return {
        "query": q,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "items": items,
    }
