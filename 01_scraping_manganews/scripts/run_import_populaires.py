#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

import psycopg2
from psycopg2.extras import execute_values, register_uuid
from dotenv import load_dotenv


POP_STAGING_TABLE = "manga.mn_populaires_staging"
POP_FINAL_TABLE = "manga.mn_populaires"

FINAL_COLS = [
    "serie_url",
    "source",
    "collection",
    "category",
    "rank_in_category",
    "title",
    "image_url",
    "volumes_text",
    "volumes_count",
    "category_desc",
    "serie_slug",
    "schema_version",
    "enrich_version",
    "scraped_at",
]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"JSON invalide ligne {i} dans {path}: {e}") from e
    return rows


def insert_into_staging(conn, items: List[Dict[str, Any]], run_id: uuid.UUID, source_file: str, batch_size: int) -> int:
    loaded_at = utc_now()
    staging_cols = FINAL_COLS + ["run_id", "loaded_at", "source_file"]

    values: List[Tuple[Any, ...]] = []
    for it in items:
        row_vals = [it.get(c) for c in FINAL_COLS]
        row_vals.extend([run_id, loaded_at, source_file])
        values.append(tuple(row_vals))

    cols_sql = ", ".join(staging_cols)
    template = "(" + ", ".join(["%s"] * len(staging_cols)) + ")"
    sql = f"INSERT INTO {POP_STAGING_TABLE} ({cols_sql}) VALUES %s"

    with conn.cursor() as cur:
        execute_values(cur, sql, values, page_size=batch_size, template=template)
    return len(values)


def upsert_into_final(conn, run_id: uuid.UUID) -> int:
    cols_sql = ", ".join(FINAL_COLS)
    update_sql = ", ".join([f"{c}=EXCLUDED.{c}" for c in FINAL_COLS if c != "serie_url"])

    sql = f"""
    INSERT INTO {POP_FINAL_TABLE} ({cols_sql})
    SELECT {cols_sql}
    FROM {POP_STAGING_TABLE}
    WHERE run_id = %s
    ON CONFLICT (serie_url) DO UPDATE
    SET {update_sql};
    """
    with conn.cursor() as cur:
        cur.execute(sql, (run_id,))
        cur.execute(f"SELECT COUNT(*) FROM {POP_STAGING_TABLE} WHERE run_id=%s", (run_id,))
        n = cur.fetchone()[0]
    return int(n)


def cleanup_staging(conn, run_id: uuid.UUID) -> None:
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {POP_STAGING_TABLE} WHERE run_id=%s", (run_id,))


def main() -> None:
    dotenv_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=dotenv_path)
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--file",
        default="/home/maxime/certification/scrapping_manga-news/data/enriched/populaires.backfilled.jsonl",
        help="Chemin du JSONL (populaires.backfilled.jsonl)",
    )
    ap.add_argument("--dsn", default=os.getenv("POSTGRES_DSN"), help="DSN Postgres (sinon env POSTGRES_DSN)")
    ap.add_argument("--run-id", default=None, help="UUID à imposer (sinon généré)")
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--no-merge", action="store_true", help="Charge staging uniquement (pas d'upsert final)")
    ap.add_argument(
        "--keep-staging",
        dest="keep_staging",
        action="store_true",
        default=True,
        help="Conserve la staging du run_id (par defaut)",
    )
    ap.add_argument(
        "--no-keep-staging",
        dest="keep_staging",
        action="store_false",
        help="Supprime la staging du run_id apres merge",
    )
    args = ap.parse_args()

    if not args.dsn:
        raise SystemExit("DSN manquant : passe --dsn ou exporte POSTGRES_DSN")

    run_id = uuid.UUID(args.run_id) if args.run_id else uuid.uuid4()
    source_file = os.path.relpath(args.file)

    items = read_jsonl(args.file)
    if not items:
        raise SystemExit("Fichier JSONL vide, rien à importer.")

    conn = psycopg2.connect(args.dsn)
    register_uuid(conn)
    conn.autocommit = False
    try:
        inserted = insert_into_staging(conn, items, run_id, source_file, args.batch_size)

        merged = 0
        if not args.no_merge:
            merged = upsert_into_final(conn, run_id)

        if (not args.keep_staging) and (not args.no_merge):
            cleanup_staging(conn, run_id)

        conn.commit()

        print("OK")
        print("run_id:", str(run_id))
        print("staging_inserted:", inserted)
        print("final_upsert_input_rows:", merged)
        print("file:", source_file)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
