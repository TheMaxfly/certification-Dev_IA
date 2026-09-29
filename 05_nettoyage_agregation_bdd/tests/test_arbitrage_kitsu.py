"""Arbitrage humain d'un point A — plusieurs entrées Kitsu pour une série.

Scénario : la série 1 (llm_review, MAL 1) mène à deux fiches Kitsu, 10 (manga)
et 11 (manhua) ; la série 2 est déjà rattachée à l'entrée 20.
"""

from __future__ import annotations

import psycopg
import pytest

from identity.arbitrage_kitsu import ErreurArbitrage, arbitrer

MOTIF = "catalogue de manga : l'entrée manga ; la manhua est l'autre édition"


@pytest.fixture
def base_a(base) -> str:
    with psycopg.connect(base, autocommit=True) as cx:
        for sid, mal, kid in ((1, "1", None), (2, "2", "20")):
            cx.execute(
                "INSERT INTO manga.work_identity "
                "(series_id, mal_id, wikidata_qid, kitsu_id) VALUES (%s, %s, %s, %s)",
                (sid, mal, f"Q{sid}", kid),
            )
            cx.execute(
                "INSERT INTO manga.match_decision "
                "(series_id, wikidata_qid, method, status) "
                "VALUES (%s, %s, 'llm_review', 'auto')",
                (sid, f"Q{sid}"),
            )
        for kid, ext in ((10, "1"), (11, "1"), (20, "1"), (20, "2")):
            cx.execute(
                "INSERT INTO manga.kitsu_mappings (kitsu_id, external_site, "
                "external_id) VALUES (%s, 'myanimelist/manga', %s)",
                (kid, ext),
            )
    return base


def test_l_arbitrage_ecrit_une_decision_derivee_qui_porte_tout(base_a):
    with psycopg.connect(base_a) as cx:
        with pytest.raises(ErreurArbitrage, match="déjà rattachée"):
            arbitrer(cx, 1, 20, MOTIF)
        cx.rollback()
        arbitrer(cx, 1, 10, MOTIF)
        cx.commit()
        methode, statut, qui, qid, details = cx.execute(
            "SELECT method, status, decided_by, wikidata_qid, details "
            "FROM manga.match_decision WHERE series_id = 1 "
            "ORDER BY decision_id DESC LIMIT 1"
        ).fetchone()
        source = cx.execute(
            "SELECT min(decision_id) FROM manga.match_decision WHERE series_id = 1"
        ).fetchone()[0]
        (kitsu_id,) = cx.execute(
            "SELECT kitsu_id FROM manga.work_identity WHERE series_id = 1"
        ).fetchone()
    assert (methode, statut, qui, qid) == ("human_review", "validated", "human", "Q1")
    assert details["kitsu_id"] == 10
    assert details["entrees"] == [10, 11, 20]
    assert details["ecartees"] == [11, 20]
    assert details["motif"] == MOTIF
    assert details["decision_source"] == source
    assert details["methode_source"] == "llm_review"
    assert kitsu_id == "10"


def test_une_entree_hors_des_identifiants_est_refusee(base_a):
    with psycopg.connect(base_a) as cx, pytest.raises(ErreurArbitrage, match="hors"):
        arbitrer(cx, 1, 99, MOTIF)


def test_un_arbitrage_sans_motif_est_refuse(base_a):
    with psycopg.connect(base_a) as cx, pytest.raises(ErreurArbitrage, match="motif"):
        arbitrer(cx, 1, 10, "  ")


def test_le_rejeu_n_ecrit_rien(base_a):
    with psycopg.connect(base_a) as cx:
        arbitrer(cx, 1, 10, MOTIF)
        cx.commit()
        assert "rien à écrire" in arbitrer(cx, 1, 10, MOTIF)
        cx.commit()
        (n,) = cx.execute(
            "SELECT count(*) FROM manga.match_decision WHERE series_id = 1"
        ).fetchone()
    assert n == 2


def test_un_kitsu_id_deja_renseigne_n_est_pas_reecrit(base_a):
    with psycopg.connect(base_a) as cx, pytest.raises(ErreurArbitrage, match="déjà"):
        arbitrer(cx, 2, 10, MOTIF)
