"""Le rattachement au grain fragment, sur la base réelle — lecture seule.

Ne tourne que si `APIMANGA_DSN` est défini (précédent : `test_controle_tetes.py`).
Motif : le 2026-10-07, `SQL_ATTEIGNABLES` est devenue une DÉRIVÉE de
`SQL_RATTACHEMENT` (une seule définition, réutilisée par le banc de mesure). Ce
qui doit tenir, et ne se vérifie que sur le corpus réel :

  - l'ensemble des séries atteignables est le MÊME qu'avec la définition
    antérieure — égalité d'ensembles, pas seulement de comptes ;
  - chaque fragment a une entité, et une seule ;
  - les entités du corpus au 2026-10-07 : 38 028, dont 8 718 séries et 29 310
    identifiants Kitsu.

Les comptes sont ceux du corpus `bench` du 2026-09-28 (part Kitsu refaite le
2026-09-29) et du moyeu du 2026-09-30 : un corpus reconstruit, ou un moyeu
enrichi, les changera — le test le dira, et c'est son rôle.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from evaluation import atteignabilite

APIMANGA_DSN = os.environ.get("APIMANGA_DSN")
besoin_apimanga = pytest.mark.skipif(not APIMANGA_DSN, reason="APIMANGA_DSN non défini")

FRAGMENTS = 66_290
SERIES_ATTEIGNABLES = 8_718
ENTITES_KITSU = 29_310

#: La définition d'avant le 2026-10-07, gelée ici VERBATIM pour la seule
#: non-régression : elle n'est exécutée par rien d'autre que ce test.
SQL_ATTEIGNABLES_AVANT = """
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


@pytest.fixture(scope="module")
def cx():
    with psycopg.connect(
        APIMANGA_DSN, options="-c default_transaction_read_only=on"
    ) as connexion:
        yield connexion


@besoin_apimanga
def test_atteignables_identiques_avant_et_apres(cx):
    avant = {s for (s,) in cx.execute(SQL_ATTEIGNABLES_AVANT)}
    apres = atteignabilite.atteignables(cx)
    assert apres == avant
    assert len(apres) == SERIES_ATTEIGNABLES


@besoin_apimanga
def test_chaque_fragment_a_une_entite_et_une_seule(cx):
    lignes = atteignabilite.rattachement(cx)
    fragments = [chunk_id for chunk_id, *_ in lignes]
    assert len(lignes) == FRAGMENTS
    assert len(set(fragments)) == FRAGMENTS, "un fragment, une ligne"
    total = cx.execute("SELECT count(*) FROM bench.corpus_chunks").fetchone()[0]
    assert total == FRAGMENTS, "aucun fragment hors du rattachement"
    sans_entite = [
        c for c, _, serie, kitsu in lignes if serie is None and kitsu is None
    ]
    assert sans_entite == []


@besoin_apimanga
def test_entites_du_corpus(cx):
    lignes = atteignabilite.rattachement(cx)
    series = {serie for _, _, serie, _ in lignes if serie is not None}
    kitsu = {kitsu for _, _, serie, kitsu in lignes if serie is None}
    assert len(series) == SERIES_ATTEIGNABLES
    assert series == atteignabilite.atteignables(cx)
    assert len(kitsu) == ENTITES_KITSU
    assert len(series) + len(kitsu) == 38_028
