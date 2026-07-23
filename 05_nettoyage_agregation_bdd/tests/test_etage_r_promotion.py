"""Étage R, run 2 — tests d'INTÉGRATION sur base jetable (schéma réel 000→011).

On vérifie les invariants qui protègent la première écriture décisionnelle :
la règle de collision (groupe entier exclu), l'unicité (candidats multiples
écartés), le partiel non promu, et la correction 1428 (append-only : la ligne
fautive n'est pas effacée, l'identité est remise à NULL). `apimanga` n'est
jamais atteignable : tout tourne sur PostgreSQL jetable.
"""

from __future__ import annotations

import psycopg
from conftest import lire

from identity import etage_r_promotion as pr


def _serie(cur, sid, *, method="trgm", status="needs_review"):
    cur.execute(
        "INSERT INTO manga.ms_series_enriched (series_id, series_title, series_year) "
        "VALUES (%s, %s, 2000)",
        (sid, f"Serie {sid}"),
    )
    cur.execute(
        "INSERT INTO manga.work_identity (series_id) VALUES (%s)",
        (sid,),
    )
    cur.execute(
        "INSERT INTO manga.match_decision (series_id, method, status) "
        "VALUES (%s, %s, %s)",
        (sid, method, status),
    )


def _avis(
    cur,
    sid,
    ct,
    cid,
    *,
    verdict="same_work",
    conf="haute",
    partiel=False,
    seau=False,
    phase="file",
):
    cur.execute(
        "INSERT INTO manga.llm_avis (series_id, run_ts, phase, candidat_type, "
        "candidat_id, verdict, confiance, justification, modele, prompt_version, "
        "dossier_partiel, pre_validation_bandes) "
        "VALUES (%s, now(), %s, %s, %s, %s, %s, 'x', 'gpt-5.6-luna', 'v1', %s, %s)",
        (sid, phase, ct, cid, verdict, conf, partiel, seau),
    )


def _wd(cur, qid, mal=None, anilist=None):
    cur.execute(
        "INSERT INTO manga.wd_pivot (qid, mal_id, anilist_id) VALUES (%s, %s, %s)",
        (qid, mal, anilist),
    )


def _identite_existante(cur, sid, *, mal=None, qid=None):
    """Une série DÉJÀ identifiée (auto), pour tester la collision externe."""
    cur.execute(
        "INSERT INTO manga.ms_series_enriched (series_id, series_title, series_year) "
        "VALUES (%s, %s, 2000)",
        (sid, f"Serie {sid}"),
    )
    cur.execute(
        "INSERT INTO manga.work_identity (series_id, mal_id, wikidata_qid) "
        "VALUES (%s, %s, %s)",
        (sid, mal, qid),
    )
    cur.execute(
        "INSERT INTO manga.match_decision (series_id, method, status) "
        "VALUES (%s, 'exact', 'auto')",
        (sid,),
    )


def _population(cur):
    pr.construire_population(cur)
    return pr.mesures(cur)


def test_une_serie_propre_est_promue_et_remplit_lidentite(base):
    with psycopg.connect(base) as cx:
        with cx.cursor() as cur:
            _serie(cur, 10)
            _avis(cur, 10, "qid", "Q10")
            _wd(cur, "Q10", mal="1000", anilist="2000")
        cx.commit()
        with cx.cursor() as cur:
            m = _population(cur)
            assert m["promus"] == 1
            cur.execute(pr.INSERT_PROMOTIONS, (pr.SCORE_PROMO,))
            cur.execute(pr.UPDATE_IDENTITES)
        cx.commit()

    dec = lire(
        base,
        "SELECT method, status FROM manga.match_decision "
        "WHERE series_id=10 AND method='llm_review'",
    )
    assert dec == [("llm_review", "auto")]
    ident = lire(
        base,
        "SELECT wikidata_qid, mal_id, anilist_id FROM manga.work_identity "
        "WHERE series_id=10",
    )
    assert ident == [("Q10", "1000", "2000")]


def test_collision_interne_exclut_le_groupe_entier(base):
    """Deux séries dérivant le même mal_id : jamais résolu par ordre d'arrivée,
    le GROUPE entier est exclu."""
    with psycopg.connect(base) as cx:
        with cx.cursor() as cur:
            _serie(cur, 11)
            _avis(cur, 11, "qid", "Q11")
            _wd(cur, "Q11", mal="500")
            _serie(cur, 12)
            _avis(cur, 12, "qid", "Q12")
            _wd(cur, "Q12", mal="500")  # même mal → collision
        cx.commit()
        with cx.cursor() as cur:
            m = _population(cur)
            assert m["promus"] == 0
            assert m["collisions"] == 2


def test_collision_externe_avec_une_identite_existante(base):
    with psycopg.connect(base) as cx:
        with cx.cursor() as cur:
            _identite_existante(cur, 99, mal="600")  # déjà en base
            _serie(cur, 13)
            _avis(cur, 13, "qid", "Q13")
            _wd(cur, "Q13", mal="600")  # entre en collision avec 99
        cx.commit()
        with cx.cursor() as cur:
            m = _population(cur)
            assert m["promus"] == 0
            assert m["collisions"] == 1


def test_serie_a_candidats_multiples_est_ecartee(base):
    with psycopg.connect(base) as cx:
        with cx.cursor() as cur:
            _serie(cur, 14)
            _avis(cur, 14, "qid", "Q14a")
            _avis(cur, 14, "qid", "Q14b")  # deux same_work haute → ambiguïté
            _wd(cur, "Q14a", mal="700")
            _wd(cur, "Q14b", mal="701")
        cx.commit()
        with cx.cursor() as cur:
            m = _population(cur)
            assert m["promus"] == 0
            assert m["multi_exclus"] == 1


def test_dossier_partiel_nest_pas_promu(base):
    with psycopg.connect(base) as cx:
        with cx.cursor() as cur:
            _serie(cur, 15)
            _avis(cur, 15, "qid", "Q15", partiel=True)
            _wd(cur, "Q15", mal="800")
        cx.commit()
        with cx.cursor() as cur:
            m = _population(cur)
            assert m["promus"] == 0


def test_1428_rejected_est_append_only_et_vide_lidentite(base):
    """La ligne kitsu_bridge fautive n'est NI modifiée NI supprimée ; une
    décision rejected s'ajoute et l'identité est remise à NULL."""
    with psycopg.connect(base) as cx:
        with cx.cursor() as cur:
            cur.execute(
                "INSERT INTO manga.ms_series_enriched "
                "(series_id, series_title, series_year) VALUES (1428, 'Sister', 1996)"
            )
            cur.execute(
                "INSERT INTO manga.work_identity "
                "(series_id, wikidata_qid, kitsu_id, mal_id, anilist_id) "
                "VALUES (1428, 'Q1045285', '421', '176', '30176')"
            )
            cur.execute(
                "INSERT INTO manga.match_decision (series_id, method, status, "
                "wikidata_qid) VALUES (1428, 'kitsu_bridge', 'auto', 'Q1045285')"
            )
        cx.commit()
        with cx.cursor() as cur:
            resultat = pr.corriger_1428(cur)
            assert resultat["applique"] is True
        cx.commit()

        # la ligne fautive existe TOUJOURS + une rejected s'est ajoutée
        methods = lire(
            base,
            "SELECT method, status FROM manga.match_decision "
            "WHERE series_id=1428 ORDER BY decision_id",
        )
        assert ("kitsu_bridge", "auto") in methods
        assert ("human_review", "rejected") in methods
        # l'identité est vierge
        ident = lire(
            base,
            "SELECT wikidata_qid, kitsu_id, mal_id, anilist_id "
            "FROM manga.work_identity WHERE series_id=1428",
        )
        assert ident == [(None, None, None, None)]

        # idempotence : rejouer ne réécrit pas
        with cx.cursor() as cur:
            assert pr.corriger_1428(cur)["applique"] is False


def test_idempotence_une_serie_deja_promue_est_skippee(base):
    with psycopg.connect(base) as cx:
        with cx.cursor() as cur:
            _serie(cur, 16)
            _avis(cur, 16, "qid", "Q16")
            _wd(cur, "Q16", mal="900")
        cx.commit()
        with cx.cursor() as cur:
            assert _population(cur)["promus"] == 1
            cur.execute(pr.INSERT_PROMOTIONS, (pr.SCORE_PROMO,))
            cur.execute(pr.UPDATE_IDENTITES)
        cx.commit()
        # la série est désormais 'auto' → rejeu : 0 promotion
        with cx.cursor() as cur:
            assert _population(cur)["promus"] == 0
