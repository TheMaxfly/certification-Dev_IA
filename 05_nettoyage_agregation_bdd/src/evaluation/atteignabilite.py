"""Atteignabilité — quelle part du catalogue le corpus peut-il seulement rendre ?

    uv run python -m evaluation.atteignabilite   # section de rapport, lecture seule

DÉFINITION (E6, 2026-09-29). Une série du catalogue est ATTEIGNABLE dans le
corpus `bench` si au moins un FRAGMENT de `bench.corpus_chunks` appartient à un
document rattaché à elle :

  - une critique (`ms_review`), par son `series_id` ;
  - un synopsis Kitsu (`kitsu_synopsis`), dont le `kitsu_id` est rattaché à la
    série par le moyeu d'identité (`manga.work_identity`) — la cascade.

Le fragment, et non le document : c'est sur les fragments que la récupération
opère. Un document sans fragment (plancher de 50 caractères du banc de
décembre) n'est pas atteignable, et l'écart est rapporté.

L'atteignabilité dépend du CORPUS mesuré, pas du jeu : elle n'est jamais écrite
dans le jeu, elle se recalcule à chaque mesure. Chaque mesure rend ses chiffres
sur toutes les questions et sur les questions atteignables.
"""

from __future__ import annotations

import os
import sys

import psycopg
import typer

#: LA définition. La mesure et ce rapport exécutent ce texte-ci, et lui seul.
SQL_ATTEIGNABLES = """
WITH par_critique AS (
  SELECT DISTINCT d.series_id
  FROM bench.corpus_docs d
  WHERE d.source = 'ms_review'
    AND EXISTS (SELECT 1 FROM bench.corpus_chunks k WHERE k.doc_key = d.doc_key)
),
par_kitsu AS (
  SELECT DISTINCT w.series_id::bigint AS series_id
  FROM bench.corpus_docs d
  JOIN manga.work_identity w ON w.kitsu_id = d.kitsu_id::text
  WHERE d.source = 'kitsu_synopsis' AND w.series_id IS NOT NULL
    AND EXISTS (SELECT 1 FROM bench.corpus_chunks k WHERE k.doc_key = d.doc_key)
)
SELECT series_id FROM par_critique
UNION
SELECT series_id FROM par_kitsu
"""

SQL_DECOMPOSITION = """
WITH frag AS (SELECT DISTINCT doc_key FROM bench.corpus_chunks),
critique_doc AS (
  SELECT DISTINCT series_id, doc_key IN (SELECT doc_key FROM frag) AS a_fragment
  FROM bench.corpus_docs WHERE source = 'ms_review'),
kitsu_doc AS (
  SELECT DISTINCT w.series_id::bigint AS series_id,
         d.doc_key IN (SELECT doc_key FROM frag) AS a_fragment
  FROM bench.corpus_docs d
  JOIN manga.work_identity w ON w.kitsu_id = d.kitsu_id::text
  WHERE d.source = 'kitsu_synopsis' AND w.series_id IS NOT NULL),
c AS (SELECT DISTINCT series_id FROM critique_doc WHERE a_fragment),
k AS (SELECT DISTINCT series_id FROM kitsu_doc WHERE a_fragment),
docs AS (SELECT series_id FROM critique_doc UNION SELECT series_id FROM kitsu_doc)
SELECT
  (SELECT count(*) FROM manga.ms_series_enriched)                 AS catalogue,
  (SELECT count(*) FROM c)                                        AS par_critique,
  (SELECT count(*) FROM k)                                        AS par_kitsu,
  (SELECT count(*) FROM c JOIN k USING (series_id))               AS les_deux,
  (SELECT count(*) FROM (SELECT series_id FROM c UNION SELECT series_id FROM k) u)
                                                                  AS atteignables,
  (SELECT count(*) FROM docs)                                     AS au_niveau_document
"""

#: Séries que la cascade rattache à une œuvre Kitsu, mais qu'aucun synopsis
#: Kitsu du corpus n'atteint. Depuis le 2026-09-29, la part Kitsu vient du raw
#: de juillet (`corpus.kitsu`) : une œuvre rattachée n'y manque que si elle n'a
#: pas de synopsis dans ce snapshot — le bilan du chargeur le compte à part
#: (« rattachées écartées faute de synopsis »), et les deux chiffres doivent
#: coïncider.
SQL_DEFAUT_KITSU = """
WITH liees AS (
  SELECT w.series_id, w.kitsu_id::bigint AS kitsu_id
  FROM manga.work_identity w WHERE w.kitsu_id IS NOT NULL),
docs AS (
  SELECT d.kitsu_id,
         EXISTS (SELECT 1 FROM bench.corpus_chunks k WHERE k.doc_key = d.doc_key)
           AS a_fragment
  FROM bench.corpus_docs d WHERE d.source = 'kitsu_synopsis')
SELECT
  (SELECT count(*) FROM liees) AS liees_par_la_cascade,
  (SELECT count(*) FROM liees l
    WHERE NOT EXISTS (SELECT 1 FROM docs d
                      WHERE d.kitsu_id = l.kitsu_id AND d.a_fragment))
    AS sans_synopsis_atteignable,
  (SELECT count(*) FROM liees l
    WHERE NOT EXISTS (SELECT 1 FROM docs d WHERE d.kitsu_id = l.kitsu_id))
    AS sans_document_au_corpus,
  (SELECT count(*) FROM liees l
    WHERE EXISTS (SELECT 1 FROM docs d
                  WHERE d.kitsu_id = l.kitsu_id AND NOT d.a_fragment))
    AS document_sans_fragment
"""

#: Le marqueur « atteignable par synopsis anglais seulement » (décision du
#: 2026-09-30) : aucune critique à fragment, mais un synopsis Kitsu à fragment
#: rattaché par la cascade. Comme l'atteignabilité, il dépend du CORPUS mesuré :
#: CALCULÉ à chaque mesure, JAMAIS écrit dans le jeu. Il partage F3 (et toute
#: famille) en deux sous-groupes — le lexical français se tait par la langue,
#: pas par la paraphrase.
SQL_SYNOPSIS_SEUL = """
SELECT s.series_id
FROM unnest(%s::bigint[]) AS s(series_id)
WHERE NOT EXISTS (
        SELECT 1 FROM bench.corpus_docs d
        WHERE d.source = 'ms_review' AND d.series_id = s.series_id
          AND EXISTS (SELECT 1 FROM bench.corpus_chunks k WHERE k.doc_key = d.doc_key))
  AND EXISTS (
        SELECT 1 FROM bench.corpus_docs d
        JOIN manga.work_identity w ON w.kitsu_id = d.kitsu_id::text
        WHERE d.source = 'kitsu_synopsis' AND w.series_id = s.series_id
          AND EXISTS (SELECT 1 FROM bench.corpus_chunks k WHERE k.doc_key = d.doc_key))
"""

app = typer.Typer(add_completion=False, help=__doc__)


def atteignables(cx: psycopg.Connection) -> set[int]:
    return {s for (s,) in cx.execute(SQL_ATTEIGNABLES)}


def synopsis_seul(cx: psycopg.Connection, series_ids: list[int]) -> set[int]:
    """Parmi ces séries, celles que seul un synopsis Kitsu (anglais) atteint."""
    return {s for (s,) in cx.execute(SQL_SYNOPSIS_SEUL, (list(series_ids),))}


def mesurer(cx: psycopg.Connection) -> tuple[dict, dict]:
    d = cx.execute(SQL_DECOMPOSITION)
    decomposition = dict(
        zip([c.name for c in d.description], d.fetchone(), strict=True)
    )
    k = cx.execute(SQL_DEFAUT_KITSU)
    defaut = dict(zip([c.name for c in k.description], k.fetchone(), strict=True))
    return decomposition, defaut


def section(decomposition: dict, defaut: dict) -> str:
    def n(x: int) -> str:
        return f"{x:_}".replace("_", " ")

    t = decomposition
    pct = 100 * t["atteignables"] / t["catalogue"] if t["catalogue"] else 0
    return "\n".join(
        [
            "## Atteignabilité",
            "",
            "**Définition.** Une série du catalogue est atteignable si au moins un "
            "**fragment** du corpus appartient à un document rattaché à elle : une "
            "critique par son `series_id`, ou un synopsis Kitsu dont le `kitsu_id` "
            "est rattaché à la série par `manga.work_identity`. La requête exacte :",
            "",
            "```sql" + SQL_ATTEIGNABLES.rstrip() + "\n```",
            "",
            "| | Séries |",
            "|---|---:|",
            f"| Catalogue (`ms_series_enriched`) | {n(t['catalogue'])} |",
            f"| atteintes par une critique | {n(t['par_critique'])} |",
            f"| atteintes par un synopsis Kitsu, via la cascade "
            f"| {n(t['par_kitsu'])} |",
            f"| dont par les deux | {n(t['les_deux'])} |",
            f"| **atteignables (union)** | **{n(t['atteignables'])} — "
            f"{pct:.1f} %**".replace(".", ",")
            + " |",
            f"| au niveau document (sans exiger de fragment) | "
            f"{n(t['au_niveau_document'])} |",
            "",
            "**Rattachées à Kitsu, sans synopsis atteignable.** Séries que la cascade "
            "rattache à une œuvre Kitsu, mais qu'aucun synopsis du corpus n'atteint. "
            "La part Kitsu venant du raw de juillet, une œuvre rattachée n'y manque "
            "que faute de synopsis dans ce snapshot (règle K1) :",
            "",
            "| | Séries |",
            "|---|---:|",
            f"| rattachées à Kitsu par la cascade "
            f"| {n(defaut['liees_par_la_cascade'])} |",
            f"| **sans synopsis atteignable** | "
            f"**{n(defaut['sans_synopsis_atteignable'])}** |",
            f"| — sans document Kitsu au corpus (pas de synopsis dans le snapshot) | "
            f"{n(defaut['sans_document_au_corpus'])} |",
            f"| — document au corpus, sans fragment | "
            f"{n(defaut['document_sans_fragment'])} |",
            "",
        ]
    )


@app.command()
def principal() -> None:
    """Imprime la section « Atteignabilité », en session lecture seule."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise typer.BadParameter("DATABASE_URL n'est pas définie.")
    # Lecture seule dès la PREMIÈRE requête : `SET SESSION CHARACTERISTICS` ne
    # vaudrait que pour les transactions suivantes, pas pour celle qu'il ouvre.
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as cx:
        typer.echo(section(*mesurer(cx)))


if __name__ == "__main__":
    sys.exit(app())
