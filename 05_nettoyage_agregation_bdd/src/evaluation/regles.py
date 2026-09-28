"""Les attendus dérivés par règle SQL — F9 et F10 (décision du 2026-09-29).

Une question F9 ou F10 attend un ENSEMBLE de séries, que les colonnes du
catalogue décrivent : « un seinen des années 90 bien noté ». L'ensemble n'est
pas recopié à la main ; il est dérivé par une règle SQL versionnée,
`v1/regles/Qnnn.sql`, exécutée en lecture seule, et son texte entre dans
l'empreinte du jeu au gel.

LES DEUX CONTRAINTES, ET CE QUI LES TIENT

  1. La règle n'emploie que des COLONNES — genre, année, note, tomes, statut —,
     jamais les mots de la question. Tenu mécaniquement par l'interdiction de
     toute recherche textuelle (`LIKE`, `ILIKE`, `~`, `SIMILAR TO`, plein texte,
     similarité) : une règle filtre des colonnes, elle ne cherche pas des mots.
     Filtrer `series_category_clean = 'Shonen'` pour « un bon shonen » est une
     colonne, pas une recherche. La LISTE des colonnes admises, elle, n'est pas
     vérifiable sans analyseur SQL : elle relève de la relecture.
  2. Elle PLAFONNE son résultat — les vingt premières par note, par exemple —
     pour que le nDCG garde un sens. Le plafond est dans le SQL : `LIMIT n`
     obligatoire, sous un `ORDER BY` qui départage par `series_id`, pour que la
     même règle rende toujours le même ensemble.

Et, parce qu'une règle ne doit jamais lire ce que le jeu mesurera : aucune
référence au schéma `bench`.
"""

from __future__ import annotations

import re
from pathlib import Path

import psycopg

INTERDITS = {
    "recherche textuelle": re.compile(
        r"\b(I?LIKE|SIMILAR\s+TO|TO_TSQUERY|PLAINTO_TSQUERY|WEBSEARCH_TO_TSQUERY|"
        r"TO_TSVECTOR|SIMILARITY|WORD_SIMILARITY|LEVENSHTEIN|SOUNDEX|STRPOS|POSITION)\b"
        r"|~|@@",
        re.IGNORECASE,
    ),
    "lecture du corpus": re.compile(r"\bbench\s*\.", re.IGNORECASE),
}
PLAFOND = re.compile(r"\bLIMIT\s+(\d+)\s*;?\s*$", re.IGNORECASE)
TRI = re.compile(r"\bORDER\s+BY\b(.*?)\bLIMIT\b", re.IGNORECASE | re.DOTALL)
DELAI = "10s"


class RegleInvalide(Exception):
    """La règle viole une contrainte ; le message dit laquelle."""


def lire(dossier: Path, question_id: str) -> str:
    chemin = dossier / "regles" / f"{question_id}.sql"
    if not chemin.is_file():
        raise RegleInvalide(f"règle introuvable : regles/{question_id}.sql")
    return chemin.read_text(encoding="utf-8")


def _sans_commentaires(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql).strip()


def controler(sql: str) -> int:
    """Vérifie la règle sans l'exécuter ; rend son plafond."""
    corps = _sans_commentaires(sql)
    litteraux_retires = re.sub(r"'(?:[^']|'')*'", "''", corps)
    if ";" in litteraux_retires.rstrip().rstrip(";"):
        raise RegleInvalide("une seule instruction par règle")
    if not re.match(r"(SELECT|WITH)\b", corps, re.IGNORECASE):
        raise RegleInvalide("une règle est un SELECT")
    for motif, expression in INTERDITS.items():
        trouve = expression.search(litteraux_retires)
        if trouve:
            raise RegleInvalide(f"{motif} interdite : « {trouve.group(0)} »")
    plafond = PLAFOND.search(litteraux_retires)
    if not plafond:
        raise RegleInvalide("plafond absent : la règle doit finir par LIMIT n")
    tri = TRI.search(litteraux_retires)
    if not tri or not re.search(r"\bseries_id\b", tri.group(1)):
        raise RegleInvalide("tri non total : ORDER BY …, series_id avant le LIMIT")
    return int(plafond.group(1))


def executer(cx: psycopg.Connection, sql: str) -> list[int]:
    """Exécute la règle en lecture seule ; rend les series_id, dans l'ordre."""
    plafond = controler(sql)
    corps = _sans_commentaires(sql).rstrip().rstrip(";")
    try:
        with cx.transaction():
            cx.execute(f"SET LOCAL statement_timeout = '{DELAI}'")
            cur = cx.execute(corps)
            colonnes = [c.name for c in cur.description or []]
            if not colonnes or colonnes[0] != "series_id":
                raise RegleInvalide("la première colonne rendue doit être series_id")
            ids = [ligne[0] for ligne in cur.fetchall()]
    except psycopg.Error as erreur:
        raise RegleInvalide(f"SQL invalide : {str(erreur).splitlines()[0]}") from erreur
    if len(ids) > plafond:
        raise RegleInvalide(f"{len(ids)} séries rendues pour un plafond de {plafond}")
    if len(set(ids)) != len(ids):
        raise RegleInvalide("la règle rend une série plusieurs fois")
    return ids
