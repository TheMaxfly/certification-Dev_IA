"""Extraction du schéma RÉEL d'apimanga — la source des modèles de données.

Lecture seule : uniquement des SELECT sur `pg_catalog`. Rien n'est inventé ni
déduit de mémoire ; les diagrammes sont dérivés de ce que cette extraction
produit, et `schema_reel.md` en est la trace vérifiable.

    DATABASE_URL='postgresql://…' uv run --with psycopg[binary] \
        python docs/modeles/extraire_schema.py > docs/modeles/schema.json

Pourquoi `pg_catalog` et non `information_schema`
-------------------------------------------------
`information_schema` **filtre par privilège** : il ne montre que les objets sur
lesquels le rôle courant détient un droit. Or l'extraction est jouée sous
`manga_api`, le rôle de consultation, qui depuis la migration 012 n'a `USAGE`
que sur `manga` — pas sur `staging`. Sous ce rôle, `information_schema` faisait
donc disparaître les **11 tables `staging`** et leurs 175 colonnes : le livrable
documentait un schéma amputé sans que rien ne le signale.

`pg_catalog` n'applique pas ce filtre : la *structure* y est lisible par tous.
Les requêtes ci-dessous reproduisent les définitions d'`information_schema`
(mêmes expressions `data_type` / `is_nullable`, mêmes fonctions d'assistance
`information_schema._pg_*`), ce qui a été vérifié par un diff **vide** sur les
484 colonnes du schéma `manga`, où les deux sources sont comparables.

Ce qui reste hors de portée du rôle : le **contenu**. `count(*)` sur `staging`
est refusé, et c'est voulu. Ces comptes sont alors reportés dans
`comptes_indisponibles` avec leur motif, plutôt que tus.
"""

from __future__ import annotations

import json
import os
import sys

import psycopg

SCHEMAS = ("manga", "staging")

# Reproduit `information_schema.columns` (mêmes expressions, mêmes fonctions
# d'assistance) mais sans son filtre de privilège — cf. docstring.
SQL_COLONNES = """
SELECT nc.nspname AS table_schema,
       c.relname  AS table_name,
       a.attnum   AS ordinal_position,
       a.attname  AS column_name,
       CASE WHEN t.typtype = 'd' THEN
              CASE WHEN bt.typelem <> 0 AND bt.typlen = -1 THEN 'ARRAY'
                   WHEN nbt.nspname = 'pg_catalog' THEN format_type(t.typbasetype, NULL)
                   ELSE 'USER-DEFINED' END
            ELSE
              CASE WHEN t.typelem <> 0 AND t.typlen = -1 THEN 'ARRAY'
                   WHEN nt.nspname = 'pg_catalog' THEN format_type(a.atttypid, NULL)
                   ELSE 'USER-DEFINED' END
       END AS data_type,
       COALESCE(bt.typname, t.typname) AS udt_name,
       CASE WHEN a.attnotnull OR (t.typtype = 'd' AND t.typnotnull)
            THEN 'NO' ELSE 'YES' END AS is_nullable,
       CASE WHEN a.attgenerated = '' THEN pg_get_expr(ad.adbin, ad.adrelid) END
            AS column_default,
       information_schema._pg_char_max_length(
           information_schema._pg_truetypid(a.*, t.*),
           information_schema._pg_truetypmod(a.*, t.*)) AS character_maximum_length,
       information_schema._pg_numeric_precision(
           information_schema._pg_truetypid(a.*, t.*),
           information_schema._pg_truetypmod(a.*, t.*)) AS numeric_precision
  FROM pg_attribute a
  LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
  JOIN (pg_class c JOIN pg_namespace nc ON nc.oid = c.relnamespace)
    ON a.attrelid = c.oid
  JOIN (pg_type t JOIN pg_namespace nt ON nt.oid = t.typnamespace)
    ON a.atttypid = t.oid
  LEFT JOIN (pg_type bt JOIN pg_namespace nbt ON nbt.oid = bt.typnamespace)
    ON t.typtype = 'd' AND t.typbasetype = bt.oid
 WHERE a.attnum > 0 AND NOT a.attisdropped
   AND c.relkind IN ('r', 'p', 'v', 'f')
   AND nc.nspname = ANY(%s)
 ORDER BY nc.nspname, c.relname, a.attnum
"""

# Idem pour `information_schema.tables` : même mappage relkind -> table_type.
SQL_TABLES = """
SELECT n.nspname AS table_schema,
       c.relname AS table_name,
       CASE c.relkind WHEN 'r' THEN 'BASE TABLE'
                      WHEN 'p' THEN 'BASE TABLE'
                      WHEN 'v' THEN 'VIEW'
                      WHEN 'f' THEN 'FOREIGN'
                      WHEN 'm' THEN 'MATERIALIZED VIEW' END AS table_type
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p', 'v', 'f', 'm')
   AND n.nspname = ANY(%s)
 ORDER BY n.nspname, c.relname
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
        # La structure est lisible par tous ; le CONTENU ne l'est pas. Sous
        # `manga_api`, `count(*)` sur `staging` est refusé (42501) — c'est le
        # cloisonnement voulu par la migration 012, pas une panne. On note alors
        # l'indisponibilité et son motif au lieu de laisser un trou muet.
        comptes: dict[str, int] = {}
        indisponibles: dict[str, str] = {}
        for table in donnees["tables"]:
            if table["table_type"] != "BASE TABLE":
                continue
            cle = f"{table['table_schema']}.{table['table_name']}"
            try:
                with cx.transaction(force_rollback=True):
                    cur.execute(
                        SQL_COMPTE.format(table["table_schema"], table["table_name"])
                    )
                    comptes[cle] = cur.fetchone()[0]
            except psycopg.errors.InsufficientPrivilege as erreur:
                indisponibles[cle] = str(erreur).strip().splitlines()[0]
        donnees["comptes"] = comptes
        donnees["comptes_indisponibles"] = indisponibles

    json.dump(donnees, sys.stdout, ensure_ascii=False, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
