"""Derivation du perimetre contre une base.

L'ordre et le contenu de chaque perimetre sont eprouves sur une base jetable, a
partir d'un catalogue factice. La volumetrie de la base reelle (`APIMANGA_DSN`)
ne se controle que sur le poste, dans l'etape « etat d'apimanga » de
`verify-poste`.
"""

import os

import psycopg
import pytest

from commun.perimetre import deriver

DSN = os.environ.get("APIMANGA_DSN")
besoin_base = pytest.mark.skipif(not DSN, reason="APIMANGA_DSN non defini")


def _catalogue(dsn: str) -> None:
    """Cinq series. Les series 2 et 3 partagent le rang 1 : le departage par
    series_id doit tenir. La serie 4 n'atteint AniList que par le pont Kitsu ;
    la serie 3 n'a qu'un titre d'article blanc ; la serie 5 n'a aucune cle."""
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO manga.ms_series_enriched"
            " (series_id, series_popularity_rank, kitsu_id)"
            " VALUES (1, 3, NULL), (2, 1, NULL), (3, 1, 30), (4, 2, 40), (5, 5, NULL)"
        )
        cx.execute(
            "INSERT INTO manga.work_identity (series_id, wikidata_qid, anilist_id)"
            " VALUES (1, 'Q1', '101'), (2, 'Q2', '102'), (3, 'Q3', '103'),"
            " (5, NULL, ' ')"
        )
        cx.execute(
            "INSERT INTO manga.wd_pivot (qid, wiki_fr)"
            " VALUES ('Q1', 'Naruto'), ('Q2', 'Bleach'), ('Q3', '  ')"
        )
        cx.execute(
            "INSERT INTO manga.kitsu_mappings (kitsu_id, external_site, external_id)"
            " VALUES (40, 'anilist/manga', '104'), (30, 'myanimelist/manga', '3')"
        )


@pytest.mark.parametrize(
    "source, attendu",
    [
        ("wikipedia_fr", [(2, "Bleach", 1), (1, "Naruto", 3)]),
        ("anilist", [(2, "102", 1), (3, "103", 1), (4, "104", 2), (1, "101", 3)]),
    ],
)
def test_le_perimetre_est_ordonne_et_sans_trou(base, source, attendu) -> None:
    _catalogue(base)
    series = deriver(base, source)
    assert [(s.series_id, s.cle_source, s.rang_popularite) for s in series] == attendu


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
