#!/usr/bin/env python3
import os
import json
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from pgvector.psycopg2 import register_vector

DSN = os.getenv("POSTGRES_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/manganews")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "qllama/multilingual-e5-base:latest")

# RAG defaults (tu peux adapter)
DOC_TYPE = os.getenv("DOC_TYPE", "rag")
TOP_K_CHUNKS = int(os.getenv("TOP_K_CHUNKS", "12"))      # chunks retournés (preuves)
TOP_SERIES = int(os.getenv("TOP_SERIES", "5"))           # séries candidates (optionnel)
MAX_CHUNKS_PER_SERIES = int(os.getenv("MAX_CHUNKS_PER_SERIES", "3"))  # diversité


def ollama_embed_query(text: str) -> list[float]:
    """
    E5 : query: ... (pour requête utilisateur)
    Retour : liste de 768 floats
    """
    payload = {
        "model": EMBED_MODEL,
        "input": [f"query: {text.strip()}"],
    }
    r = requests.post(f"{OLLAMA_URL}/api/embed", json=payload, timeout=180)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    vec = data["embeddings"][0]
    if not isinstance(vec, list) or len(vec) == 0:
        raise RuntimeError("embedding vide/invalide")
    return vec


def search_top_chunks(conn, qvec: list[float], doc_type: str, top_k: int):
    """
    Renvoie les meilleurs chunks (preuves) triés par distance cosinus (pgvector <=>).
    1 - distance = cosine_sim approx (plus haut = mieux).
    """
    sql = """
    SELECT
      series_url,
      doc_type,
      chunk_index,
      left(chunk_text, 220) AS preview,
      1 - (embedding <=> %s::vector) AS cosine_sim
    FROM manga.mn_series_chunks
    WHERE doc_type = %s
    ORDER BY embedding <=> %s::vector
    LIMIT %s;
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (qvec, doc_type, qvec, top_k))
        return cur.fetchall()


def rank_series_from_chunks(chunks, top_series: int, max_chunks_per_series: int):
    """
    Petit scoring simple côté Python :
    - on prend les chunks déjà triés par similarité
    - on garde max N chunks par série
    - score série = somme des cosine_sim des chunks conservés
    """
    by_series = {}
    for row in chunks:
        s = row["series_url"]
        by_series.setdefault(s, [])
        if len(by_series[s]) < max_chunks_per_series:
            by_series[s].append(row)

    ranked = []
    for s, rows in by_series.items():
        score = sum(r["cosine_sim"] for r in rows)
        ranked.append({
            "series_url": s,
            "score": score,
            "evidences": rows,
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:top_series]


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("query", help="Texte utilisateur (ex: 'manga sombre avec des titans...')")
    p.add_argument("--doc-type", default=DOC_TYPE)
    p.add_argument("--top-k-chunks", type=int, default=TOP_K_CHUNKS)
    p.add_argument("--top-series", type=int, default=TOP_SERIES)
    p.add_argument("--max-chunks-per-series", type=int, default=MAX_CHUNKS_PER_SERIES)
    args = p.parse_args()

    qvec = ollama_embed_query(args.query)

    conn = psycopg2.connect(DSN)
    register_vector(conn)  # important pour passer un list[float] -> vector
    try:
        chunks = search_top_chunks(conn, qvec, args.doc_type, args.top_k_chunks)
    finally:
        conn.close()

    ranked = rank_series_from_chunks(chunks, args.top_series, args.max_chunks_per_series)

    # Output JSON (pratique pour brancher sur ton service API plus tard)
    out = {
        "query": args.query,
        "doc_type": args.doc_type,
        "embedding_model": EMBED_MODEL,
        "top_chunks": chunks,
        "top_series": ranked,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
