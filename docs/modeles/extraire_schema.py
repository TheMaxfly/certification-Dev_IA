"""Extraction du schéma RÉEL d'apimanga — la source des modèles de données.

Lecture seule : uniquement des SELECT sur `information_schema` et `pg_catalog`.
Rien n'est inventé ni déduit de mémoire ; les diagrammes sont dérivés de ce que
cette extraction produit, et `schema_reel.md` en est la trace vérifiable.

    DATABASE_URL='postgresql://…' uv run --with psycopg[binary] \
        python docs/modeles/extraire_schema.py > docs/modeles/schema.json
"""

from __future__ import annotations

import json
import os
import sys

import psycopg

SCHEMAS = ("manga", "staging")

SQL_COLONNES = """
SELECT table_schema, table_name, ordinal_position, column_name,
       data_type, udt_name, is_nullable, column_default,
       character_maximum_length, numeric_precision
  FROM information_schema.columns
 WHERE table_schema = ANY(%s)
 ORDER BY table_schema, table_name, ordinal_position
"""

SQL_TABLES = """
SELECT table_schema, table_name, table_type
  FROM information_schema.tables
 WHERE table_schema = ANY(%s)
 ORDER BY table_schema, table_name
"""

# PK et UNIQUE déclarés en contrainte.
SQL_CONTRAINTES = """
SELECT n.nspname   AS schema,
       t.relname   AS table,
       c.conname   AS nom,
       c.contype   AS type,
       pg_get_constraintdef(c.oid) AS definition
  FROM pg_constraint c
  JOIN pg_class t ON t.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = ANY(%s)
 ORDER BY 1, 2, 4, 3
"""

# Index UNIQUE, y compris PARTIELS (clause WHERE) : ce sont eux qui portent
# l'unicité conditionnelle des identifiants externes de work_identity.
SQL_INDEX = """
SELECT n.nspname AS schema,
       t.relname AS table,
       i.relname AS index,
       ix.indisunique AS unique,
       ix.indisprimary AS primaire,
       pg_get_indexdef(i.oid) AS definition
  FROM pg_index ix
  JOIN pg_class i ON i.oid = ix.indexrelid
  JOIN pg_class t ON t.oid = ix.indrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = ANY(%s)
 ORDER BY 1, 2, 3
"""

# Clés étrangères : source → cible, avec les colonnes des deux côtés.
SQL_FK = """
SELECT n.nspname AS schema_source,
       t.relname AS table_source,
       c.conname AS nom,
       (SELECT array_agg(a.attname ORDER BY x.ord)
          FROM unnest(c.conkey) WITH ORDINALITY AS x(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = c.conrelid
                             AND a.attnum = x.attnum) AS colonnes_source,
       nc.nspname AS schema_cible,
       tc.relname AS table_cible,
       (SELECT array_agg(a.attname ORDER BY x.ord)
          FROM unnest(c.confkey) WITH ORDINALITY AS x(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = c.confrelid
                             AND a.attnum = x.attnum) AS colonnes_cible,
       c.confdeltype AS on_delete
  FROM pg_constraint c
  JOIN pg_class t ON t.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
  JOIN pg_class tc ON tc.oid = c.confrelid
  JOIN pg_namespace nc ON nc.oid = tc.relnamespace
 WHERE c.contype = 'f' AND n.nspname = ANY(%s)
 ORDER BY 1, 2, 3
"""

SQL_VUES = """
SELECT schemaname AS schema, viewname AS nom, definition
  FROM pg_views
 WHERE schemaname = ANY(%s)
 ORDER BY 1, 2
"""

# Volumétrie : une ligne par table, comptée réellement (pas l'estimation
# reltuples, qui dérive après des chargements en masse).
SQL_COMPTE = "SELECT count(*) FROM {}.{}"


def lignes(curseur, sql: str, params=None) -> list[dict]:
    curseur.execute(sql, params or [list(SCHEMAS)])
    colonnes = [d[0] for d in curseur.description]
    return [dict(zip(colonnes, r, strict=False)) for r in curseur.fetchall()]


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'",
            file=sys.stderr,
        )
        return 2

    with psycopg.connect(url, connect_timeout=10) as cx, cx.cursor() as cur:
        donnees = {
            "tables": lignes(cur, SQL_TABLES),
            "colonnes": lignes(cur, SQL_COLONNES),
            "contraintes": lignes(cur, SQL_CONTRAINTES),
            "index": lignes(cur, SQL_INDEX),
            "fk": lignes(cur, SQL_FK),
            "vues": lignes(cur, SQL_VUES),
        }
        comptes = {}
        for table in donnees["tables"]:
            if table["table_type"] != "BASE TABLE":
                continue
            cle = f"{table['table_schema']}.{table['table_name']}"
            cur.execute(SQL_COMPTE.format(table["table_schema"], table["table_name"]))
            comptes[cle] = cur.fetchone()[0]
        donnees["comptes"] = comptes

    json.dump(donnees, sys.stdout, ensure_ascii=False, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
