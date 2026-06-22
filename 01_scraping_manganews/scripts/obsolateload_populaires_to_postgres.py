#!/usr/bin/env python3
import os
import json
import argparse
import psycopg2
from psycopg2.extras import execute_values


def _none_if_blank(x):
    if x is None:
        return None
    s = str(x).strip()
    if s == "" or s.lower() == "nan":
        return None
    return x


def _int_or_none(x):
    x = _none_if_blank(x)
    if x is None:
        return None
    try:
        # gère "22", 22, "22.0"
        return int(float(x))
    except Exception:
        return None


def ensure_table(cur):
    # Optionnel mais pratique : crée la table si elle n’existe pas
    cur.execute(
        """
        CREATE SCHEMA IF NOT EXISTS manga;

        CREATE TABLE IF NOT EXISTS manga.mn_populaires (
          serie_url text PRIMARY KEY,             -- clé de merge avec mn_series.url
          source text NOT NULL,
          collection text NOT NULL,               -- "populaires"
          category text NOT NULL,                 -- Seinen/Shonen/Shojo/...
          rank_in_category int NOT NULL,
          title text NOT NULL,

          image_url text,
          volumes_text text,
          volumes_count int,

          category_desc text,
          serie_slug text,

          schema_version text NOT NULL,
          enrich_version text NOT NULL,
          scraped_at timestamptz NOT NULL
        );

        CREATE INDEX IF NOT EXISTS mn_pop_category_rank_idx
          ON manga.mn_populaires (category, rank_in_category);
        """
    )


def main():
    parser = argparse.ArgumentParser(description="Load populaires.backfilled.jsonl into PostgreSQL (manga.mn_populaires).")
    parser.add_argument(
        "--file",
        default="data/enriched/populaires.backfilled.jsonl",
        help="Path to JSONL file (default: data/enriched/populaires.backfilled.jsonl)",
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("POSTGRES_DSN") or os.getenv("APIMANGA_DSN"),
        help="PostgreSQL DSN. If omitted, uses POSTGRES_DSN or APIMANGA_DSN env var.",
    )
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size for INSERT (default: 500)")
    parser.add_argument("--no-create-table", action="store_true", help="Do not CREATE TABLE IF NOT EXISTS")
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("❌ DSN manquant. Passe --dsn ou exporte POSTGRES_DSN (ou APIMANGA_DSN).")

    path = args.file
    if not os.path.exists(path):
        raise SystemExit(f"❌ Fichier introuvable: {path}")

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        if not args.no_create_table:
            ensure_table(cur)
            conn.commit()

        insert_sql = """
            INSERT INTO manga.mn_populaires (
                serie_url,
                source, collection, category, rank_in_category, title,
                image_url, volumes_text, volumes_count,
                category_desc, serie_slug,
                schema_version, enrich_version, scraped_at
            ) VALUES %s
            ON CONFLICT (serie_url) DO UPDATE SET
                source = EXCLUDED.source,
                collection = EXCLUDED.collection,
                category = EXCLUDED.category,
                rank_in_category = EXCLUDED.rank_in_category,
                title = EXCLUDED.title,
                image_url = EXCLUDED.image_url,
                volumes_text = EXCLUDED.volumes_text,
                volumes_count = EXCLUDED.volumes_count,
                category_desc = EXCLUDED.category_desc,
                serie_slug = EXCLUDED.serie_slug,
                schema_version = EXCLUDED.schema_version,
                enrich_version = EXCLUDED.enrich_version,
                scraped_at = EXCLUDED.scraped_at
        """

        buffer = []
        n_total = 0
        n_inserted = 0

        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    raise SystemExit(f"❌ JSON invalide ligne {line_no}: {e}")

                # Champs attendus / clés
                serie_url = _none_if_blank(obj.get("serie_url"))
                title = _none_if_blank(obj.get("title"))
                category = _none_if_blank(obj.get("category"))
                rank_in_category = _int_or_none(obj.get("rank_in_category"))

                schema_version = _none_if_blank(obj.get("schema_version"))
                enrich_version = _none_if_blank(obj.get("enrich_version"))
                scraped_at = _none_if_blank(obj.get("scraped_at"))

                if not serie_url:
                    raise SystemExit(f"❌ serie_url manquant ligne {line_no}")
                if not title:
                    raise SystemExit(f"❌ title manquant ligne {line_no} (serie_url={serie_url})")
                if not category:
                    raise SystemExit(f"❌ category manquant ligne {line_no} (serie_url={serie_url})")
                if rank_in_category is None:
                    raise SystemExit(f"❌ rank_in_category manquant/illisible ligne {line_no} (serie_url={serie_url})")
                if not schema_version or not enrich_version or not scraped_at:
                    raise SystemExit(
                        f"❌ schema_version/enrich_version/scraped_at manquant ligne {line_no} (serie_url={serie_url})"
                    )

                row = (
                    serie_url,
                    _none_if_blank(obj.get("source")) or "manga_news",
                    _none_if_blank(obj.get("collection")) or "populaires",
                    category,
                    rank_in_category,
                    title,
                    _none_if_blank(obj.get("image_url")),
                    _none_if_blank(obj.get("volumes_text")),
                    _int_or_none(obj.get("volumes_count")),
                    _none_if_blank(obj.get("category_desc")),
                    _none_if_blank(obj.get("serie_slug")),
                    schema_version,
                    enrich_version,
                    scraped_at,  # ISO → Postgres cast vers timestamptz
                )

                buffer.append(row)
                n_total += 1

                if len(buffer) >= args.batch_size:
                    execute_values(cur, insert_sql, buffer, page_size=args.batch_size)
                    conn.commit()
                    n_inserted += len(buffer)
                    buffer.clear()

        if buffer:
            execute_values(cur, insert_sql, buffer, page_size=args.batch_size)
            conn.commit()
            n_inserted += len(buffer)
            buffer.clear()

        print(f"✅ Terminé. Lignes lues: {n_total}, lignes upsert: {n_inserted}")
        print("Astuce vérif : SELECT COUNT(*) FROM manga.mn_populaires;")

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
