#!/usr/bin/env python3
import os
import time
import re
import unicodedata
import requests
import psycopg2
from psycopg2.extras import execute_values

# ✅ pgvector adapter
from pgvector.psycopg2 import register_vector

# -----------------------
# Config
# -----------------------
DSN = os.getenv("POSTGRES_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/manganews")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

MODEL_PRIMARY = os.getenv("EMBED_MODEL", "qllama/multilingual-e5-base:latest")

FALLBACK_MODELS = [
    m.strip() for m in os.getenv(
        "FALLBACK_MODELS",
        "qllama/multilingual-e5-base:q8_0,qllama/multilingual-e5-base:q4_k_m"
    ).split(",")
    if m.strip()
]

# ✅ Anti-OOM defaults (plus safe)
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "50"))
EMBED_BATCH = int(os.getenv("EMBED_BATCH", "32"))
COMMIT_EVERY = int(os.getenv("COMMIT_EVERY", "200"))
SLEEP_BETWEEN = float(os.getenv("SLEEP_BETWEEN", "0.05"))

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))
RETRY_SLEEP = float(os.getenv("RETRY_SLEEP", "0.1"))

TRUNCATE_STEPS = [int(x) for x in os.getenv("TRUNCATE_STEPS", "1200,900,700,500,350,250").split(",")]
MIN_TRUNC = int(os.getenv("MIN_TRUNC", "200"))

E5_PREFIX = os.getenv("E5_PREFIX", "passage: ")
ORDER = os.getenv("ORDER_BY_LEN", "desc").lower()  # "asc" or "desc"


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


def _post_embed(model: str, inputs):
    """
    Call Ollama /api/embed.
    ✅ Always send list for inputs for stability.
    Returns list of vectors.
    """
    if isinstance(inputs, str):
        inputs = [inputs]

    payload = {"model": model, "input": inputs}
    r = requests.post(f"{OLLAMA_URL}/api/embed", json=payload, timeout=180)
    r.raise_for_status()
    data = r.json()

    if "error" in data:
        raise RuntimeError(data["error"])
    if "embeddings" not in data:
        raise RuntimeError(f"unexpected response keys={list(data.keys())}")

    return data["embeddings"]


def _build_steps(p_clean: str):
    """✅ Clamp + dedupe truncation sizes (avoid retrying same candidate)."""
    L = len(p_clean)
    steps = [n for n in TRUNCATE_STEPS if n >= MIN_TRUNC]
    if not steps:
        steps = [MIN_TRUNC]

    # clamp to length
    steps = [min(n, L) for n in steps]
    # ensure last step is exactly L if smaller than all steps
    if L < steps[-1]:
        steps.append(L)

    # dedupe while preserving order
    seen = set()
    steps2 = []
    for x in steps:
        if x not in seen and x > 0:
            seen.add(x)
            steps2.append(x)

    # if text is already very short, just try it once
    if L <= steps2[-1]:
        steps2 = [L]

    return steps2


def embed_batch_best_effort(passages):
    """
    Try batch with primary model.
    If it fails, fall back per-item with sanitize + truncation + retries + fallbacks.
    Returns: list of (vec_or_None, model_used_or_None)
    """
    try:
        vecs = _post_embed(MODEL_PRIMARY, passages)
        return [(v, MODEL_PRIMARY) for v in vecs]
    except Exception as e:
        print(f"[WARN] batch embed failed -> fallback per-item. err={e}")

    results = []
    models_to_try = [MODEL_PRIMARY] + FALLBACK_MODELS

    for p in passages:
        p_clean = sanitize_text(p)
        if not p_clean:
            results.append((None, None))
            continue

        steps = _build_steps(p_clean)

        vec = None
        model_used = None

        for model in models_to_try:
            for n in steps:
                candidate = p_clean[:n]
                last_err = None
                for attempt in range(MAX_RETRIES + 1):
                    try:
                        vec = _post_embed(model, candidate)[0]
                        model_used = model
                        break
                    except Exception as ex:
                        last_err = ex
                        if attempt < MAX_RETRIES:
                            time.sleep(RETRY_SLEEP)
                if vec is not None:
                    break
            if vec is not None:
                break

        if vec is None:
            print(f"[WARN] skip passage after retries. len={len(p_clean)} last_err={last_err}")
        results.append((vec, model_used))

    return results


# -----------------------
# Main
# -----------------------
def main():
    conn = psycopg2.connect(DSN)
    conn.autocommit = False

    # ✅ register vector adapter for psycopg2
    register_vector(conn)

    order_sql = "DESC" if ORDER == "desc" else "ASC"

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM manga.mn_series WHERE indexable_rag IS TRUE;")
        total_rows = cur.fetchone()[0]
    print(f"indexable_rag rows: {total_rows}")

    inserted_since_commit = 0
    offset = 0

    total_chunks = 0
    total_inserted = 0
    total_skipped = 0
    total_batches = 0
    total_batches_fallback = 0

    while True:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT url, resume, points_forts
                FROM manga.mn_series
                WHERE indexable_rag IS TRUE
                ORDER BY rag_char_len {order_sql} NULLS LAST
                OFFSET %s LIMIT %s
                """,
                (offset, PAGE_SIZE),
            )
            rows = cur.fetchall()

        if not rows:
            break

        chunk_rows = []
        for (url, resume, points_forts) in rows:
            for doc_type, txt in (("resume", resume), ("points_forts", points_forts)):
                cleaned = sanitize_text(txt or "")
                if not cleaned:
                    continue
                chunks = chunk_text(cleaned, CHUNK_SIZE, CHUNK_OVERLAP)
                for idx, ch in enumerate(chunks):
                    chunk_rows.append((url, doc_type, idx, ch))

        total_chunks += len(chunk_rows)

        i = 0
        while i < len(chunk_rows):
            batch = chunk_rows[i:i + EMBED_BATCH]
            passages = [f"{E5_PREFIX}{t[3]}" for t in batch]

            total_batches += 1
            results = embed_batch_best_effort(passages)

            # fallback used if any model differs from primary or any None
            if any((mu != MODEL_PRIMARY) or (v is None) for (v, mu) in results):
                total_batches_fallback += 1

            insert_tuples = []
            for (row, (vec, model_used)) in zip(batch, results):
                if vec is None:
                    total_skipped += 1
                    continue
                (url, doc_type, idx, ch) = row
                insert_tuples.append((url, doc_type, idx, ch, vec, model_used))

            if insert_tuples:
                with conn.cursor() as cur:
                    execute_values(
                        cur,
                        """
                        INSERT INTO manga.mn_docs_chunks
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
            time.sleep(SLEEP_BETWEEN)

        offset += PAGE_SIZE
        print(
            f"progress rows_offset={offset}/{total_rows} chunks_page={len(chunk_rows)} "
            f"inserted={total_inserted} skipped={total_skipped} "
            f"batches={total_batches} batches_with_fallback={total_batches_fallback}"
        )

    if inserted_since_commit:
        conn.commit()

    conn.close()
    print("done")
    print(
        f"stats: rows={total_rows} chunks={total_chunks} inserted={total_inserted} "
        f"skipped={total_skipped} batches={total_batches} batches_with_fallback={total_batches_fallback}"
    )


if __name__ == "__main__":
    main()
