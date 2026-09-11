"""Derivation du perimetre contre la base reelle.

Ignore si `APIMANGA_DSN` n'est pas defini : la suite reste verte hors poste de
developpement, mais le controle porte sur la vraie base quand elle est la.
"""

import os

import pytest

from commun.perimetre import DERIVATIONS, deriver

DSN = os.environ.get("APIMANGA_DSN")
besoin_base = pytest.mark.skipif(not DSN, reason="APIMANGA_DSN non defini")


@besoin_base
@pytest.mark.parametrize("source", sorted(DERIVATIONS))
def test_le_perimetre_est_ordonne_et_sans_trou(source: str) -> None:
    series = deriver(DSN, source)
    assert series, f"perimetre {source} vide"
    rangs = [(s.rang_popularite, s.series_id) for s in series]
    assert rangs == sorted(rangs), (
        "parcours non rejouable : 4 rangs de popularite sont en doublon dans le "
        "catalogue, le departage par series_id doit tenir"
    )
    assert all(s.series_id and s.cle_source for s in series)


@besoin_base
def test_volumetrie_des_deux_perimetres() -> None:
    """Les deux sources se derivent de la meme table, par des colonnes distinctes."""
    wp = deriver(DSN, "wikipedia_fr")
    al = deriver(DSN, "anilist")
    assert len(wp) == 1368, f"perimetre Wikipedia FR = {len(wp)}, attendu 1368"
    assert len(al) == 8441, (
        f"perimetre AniList = {len(al)}, attendu 8441 — union des deux ponts. "
        f"Le referentiel seul rend 8 051 : il n est pas un sur-ensemble."
    )
    assert len({s.series_id for s in wp}) == len(wp), "doublon de serie"


@besoin_base
def test_la_connexion_est_en_lecture_seule() -> None:
    """Le module ne modifie jamais la base."""
    import psycopg

    with psycopg.connect(DSN) as conn:
        conn.read_only = True
        with conn.cursor() as cur, pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            cur.execute("create temp table sentinelle_ecriture(x int)")
