#!/usr/bin/env python3
import os
import time
import re
import unicodedata
import requests
import psycopg2
from psycopg2.extras import execute_values
from pgvector.psycopg2 import register_vector

# -----------------------
# Config
# -----------------------
DSN = os.getenv("POSTGRES_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/manganews")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("EMBED_MODEL", "qllama/multilingual-e5-base:q4_k_m")

# Safe+fast defaults for your laptop (you can override via env)
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "100"))       # how many series per loop
EMBED_BATCH = int(os.getenv("EMBED_BATCH", "16"))    # chunks per embed call (raise to 32 if stable)
COMMIT_EVERY = int(os.getenv("COMMIT_EVERY", "400"))
SLEEP_BETWEEN = float(os.getenv("SLEEP_BETWEEN", "0"))

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))

E5_PREFIX = os.getenv("E5_PREFIX", "passage: ")

# We only handle this doc_type in your current schema
DOC_TYPE = os.getenv("DOC_TYPE", "rag")


# -----------------------
# Helpers
# -----------------------
def sanitize_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\ufeff", "").replace("\u200b", "")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def chunk_text(text: str, size: int, overlap: int):
    if not text:
        return []
    text = text.strip()
    if not text:
        return []
    out = []
    i = 0
    n = len(text)
    step = max(1, size - overlap)
    while i < n:
        out.append(text[i:i + size])
        i += step
    return out


def post_embed_batch(passages):
    """
    Call Ollama /api/embed with list[str], returns list[vectors].
    """
    if isinstance(passages, str):
        passages = [passages]
    payload = {"model": MODEL, "input": passages}
    r = requests.post(f"{OLLAMA_URL}/api/embed", json=payload, timeout=180)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    vecs = data.get("embeddings")
    if not vecs:
        raise RuntimeError(f"unexpected response keys={list(data.keys())}")
    return vecs


def ensure_table_exists(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('manga.mn_series_chunks');")
        if cur.fetchone()[0] is None:
            raise RuntimeError("Table manga.mn_series_chunks does not exist.")


# -----------------------
# Main
# -----------------------
def main():
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    register_vector(conn)
    ensure_table_exists(conn)

    # How many remain?
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM manga.mn_series s
            LEFT JOIN (
              SELECT DISTINCT series_url
              FROM manga.mn_series_chunks
              WHERE doc_type = %s
            ) c ON c.series_url = s.url
            WHERE s.indexable_rag IS TRUE
              AND c.series_url IS NULL
            """,
            (DOC_TYPE,),
        )
        remaining = cur.fetchone()[0]
    print(f"remaining series without chunks (doc_type={DOC_TYPE}): {remaining}")

    inserted_since_commit = 0
    total_inserted = 0
    total_series_processed = 0
    total_batches = 0

    while True:
        # Fetch only missing series (no chunks yet for doc_type)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.url, s.rag_text
                FROM manga.mn_series s
                LEFT JOIN (
                  SELECT DISTINCT series_url
                  FROM manga.mn_series_chunks
                  WHERE doc_type = %s
                ) c ON c.series_url = s.url
                WHERE s.indexable_rag IS TRUE
                  AND c.series_url IS NULL
                ORDER BY s.rag_char_len DESC NULLS LAST
                LIMIT %s
                """,
                (DOC_TYPE, PAGE_SIZE),
            )
            rows = cur.fetchall()

        if not rows:
            break

        total_series_processed += len(rows)

        # Build chunks for this page
        chunk_rows = []
        for (url, rag_text) in rows:
            cleaned = sanitize_text(rag_text or "")
            if not cleaned:
                continue
            chunks = chunk_text(cleaned, CHUNK_SIZE, CHUNK_OVERLAP)
            for idx, ch in enumerate(chunks):
                chunk_rows.append((url, DOC_TYPE, idx, ch))

        # Embed + insert chunks in batches
        i = 0
        while i < len(chunk_rows):
            batch = chunk_rows[i:i + EMBED_BATCH]
            passages = [f"{E5_PREFIX}{t[3]}" for t in batch]

            total_batches += 1
            vecs = post_embed_batch(passages)

            insert_tuples = []
            for (row, vec) in zip(batch, vecs):
                (url, doc_type, idx, ch) = row
                insert_tuples.append((url, doc_type, idx, ch, vec, MODEL))

            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO manga.mn_series_chunks
                      (series_url, doc_type, chunk_index, chunk_text, embedding, embedding_model, embedded_at)
                    VALUES %s
                    ON CONFLICT (series_url, doc_type, chunk_index)
                    DO NOTHING
                    """,
                    insert_tuples,
                    template="(%s,%s,%s,%s,%s,%s,now())",
                    page_size=len(insert_tuples),
                )

            inserted_since_commit += len(insert_tuples)
            total_inserted += len(insert_tuples)

            if inserted_since_commit >= COMMIT_EVERY:
                conn.commit()
                inserted_since_commit = 0

            i += EMBED_BATCH
            if SLEEP_BETWEEN:
                time.sleep(SLEEP_BETWEEN)

        # Lightweight progress (no heavy queries)
        print(
            f"progress: series_processed={total_series_processed} "
            f"inserted_chunks={total_inserted} batches={total_batches}"
        )

    if inserted_since_commit:
        conn.commit()

    conn.close()
    print("done")
    print(f"final: series_processed={total_series_processed} inserted_chunks={total_inserted} batches={total_batches}")


if __name__ == "__main__":
    main()

