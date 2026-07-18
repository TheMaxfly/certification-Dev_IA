"""Étage 1 (jointure exacte MS × Wikidata) de bout en bout, sur base JETABLE.

Une fixture unique couvre les DIX cases de la matrice figée : les trois qui
mènent à l'auto, et les sept qui mènent à la revue. Chaque série de test est
construite pour tomber dans une case et une seule.

Le point le plus important à verrouiller est la règle transverse : une année
discordante interdit l'auto MÊME quand l'auteur concorde. Sans ce test, une
régression la rendrait silencieusement permissive.
"""

from __future__ import annotations

import psycopg
import pytest
from conftest import lire

from identity.wikidata_dump import normaliser

AUTEUR_A = "Kouhei HORIKOSHI"
AUTEUR_B = "Naoki URASAWA"


def _seed(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as cx:
        # ------------------------------------------------------------------ #
        # Vérité du pont : sert UNIQUEMENT à calibrer la fenêtre d'année.
        # Ces séries portent une décision, donc sortent du périmètre.
        # ------------------------------------------------------------------ #
        for i, (annee_ms, annee_wd) in enumerate(
            [(2000, 2000), (2005, 2005), (2010, 2009)], start=1
        ):
            qid = f"QP{i}"
            cx.execute(
                "INSERT INTO manga.ms_series_enriched (series_id, series_year) "
                "VALUES (%s, %s)",
                (i, annee_ms),
            )
            cx.execute(
                "INSERT INTO manga.wd_pivot (qid, annee) VALUES (%s, %s)",
                (qid, annee_wd),
            )
            cx.execute(
                "INSERT INTO manga.work_identity (series_id, wikidata_qid) "
                "VALUES (%s, %s)",
                (i, qid),
            )
            cx.execute(
                "INSERT INTO manga.match_decision "
                "(series_id, wikidata_qid, method, score, status) "
                "VALUES (%s, %s, 'kitsu_bridge', 1.0, 'auto')",
                (i, qid),
            )

        def serie(sid, titre, annee=None, scenariste=None):
            cx.execute(
                "INSERT INTO manga.ms_series_enriched "
                "(series_id, series_title, series_year, series_scenariste) "
                "VALUES (%s, %s, %s, %s)",
                (sid, titre, annee, scenariste),
            )
            cx.execute(
                "INSERT INTO manga.work_identity (series_id) VALUES (%s)", (sid,)
            )

        def forme_ms(sid, forme):
            cx.execute(
                "INSERT INTO manga.ms_formes "
                "(series_id, forme, forme_norm, forme_type) "
                "VALUES (%s, %s, %s, 'title')",
                (sid, forme, normaliser(forme)),
            )

        def oeuvre_wd(qid, forme, annee=None, auteur=None, mal=None, anilist=None):
            cx.execute(
                "INSERT INTO manga.wd_pivot (qid, label_principal, annee, "
                " mal_id, anilist_id) VALUES (%s, %s, %s, %s, %s)",
                (qid, forme, annee, mal, anilist),
            )
            cx.execute(
                "INSERT INTO manga.wd_formes (qid, forme, forme_norm, forme_type) "
                "VALUES (%s, %s, %s, 'label')",
                (qid, forme, normaliser(forme)),
            )
            if auteur is not None:
                auteur_qid = f"A{qid}"
                cx.execute(
                    "INSERT INTO manga.wd_auteurs (qid, auteur_qid, auteur) "
                    "VALUES (%s, %s, %s)",
                    (qid, auteur_qid, auteur),
                )
                cx.execute(
                    "INSERT INTO manga.wd_auteurs_formes "
                    "(auteur_qid, forme, forme_norm, forme_type) "
                    "VALUES (%s, %s, %s, 'label')",
                    (auteur_qid, auteur, normaliser(auteur)),
                )

        # 10 — unique + auteur concordant + année concordante -> AUTO 0.97
        serie(10, "Alpha", 2000, AUTEUR_A)
        forme_ms(10, "Alpha")
        oeuvre_wd("Q10", "Alpha", 2000, AUTEUR_A, mal="500", anilist="600")

        # 11 — unique + auteur incomparable (aucun auteur MS) + année -> AUTO 0.93
        serie(11, "Beta", 2001, None)
        forme_ms(11, "Beta")
        oeuvre_wd("Q11", "Beta", 2001, AUTEUR_A)

        # 12 — unique, aucun signal (ni auteur MS, ni année) -> sans signal
        serie(12, "Gamma", None, None)
        forme_ms(12, "Gamma")
        oeuvre_wd("Q12", "Gamma", None, AUTEUR_A)

        # 13 — unique + auteur discordant -> review
        serie(13, "Delta", 2003, AUTEUR_A)
        forme_ms(13, "Delta")
        oeuvre_wd("Q13", "Delta", 2003, AUTEUR_B)

        # 14 — auteur CONCORDANT mais année hors fenêtre -> jamais auto
        serie(14, "Epsilon", 2050, AUTEUR_A)
        forme_ms(14, "Epsilon")
        oeuvre_wd("Q14", "Epsilon", 2000, AUTEUR_A)

        # 15 — multi-candidats, un seul départagé par l'auteur -> AUTO 0.95
        serie(15, "Zeta", 2005, AUTEUR_A)
        forme_ms(15, "Zeta")
        oeuvre_wd("Q15a", "Zeta", 2005, AUTEUR_A)
        oeuvre_wd("Q15b", "Zeta", 2005, AUTEUR_B)

        # 16 — multi-candidats sans aucun départage -> ambiguïté persistante
        serie(16, "Eta", None, None)
        forme_ms(16, "Eta")
        oeuvre_wd("Q16a", "Eta", None, None)
        oeuvre_wd("Q16b", "Eta", None, None)

        # 17 — multi-candidats, seule l'année départage -> review (jamais auto)
        serie(17, "Theta", 2007, None)
        forme_ms(17, "Theta")
        oeuvre_wd("Q17a", "Theta", 2007, None)
        oeuvre_wd("Q17b", "Theta", 1900, None)

        # 18 et 19 — deux séries MS visant le MÊME qid -> tout le groupe en revue
        for sid in (18, 19):
            serie(sid, f"Iota {sid}", 2008, AUTEUR_A)
            cx.execute(
                "INSERT INTO manga.ms_formes "
                "(series_id, forme, forme_norm, forme_type) "
                "VALUES (%s, 'Iota', %s, 'title')",
                (sid, normaliser("Iota")),
            )
        oeuvre_wd("Q18", "Iota", 2008, AUTEUR_A)


@pytest.fixture
def etage1(base, tmp_path):
    _seed(base)
    from identity import etage1_exact

    dossier = tmp_path / "rapport"
    etage1_exact.construire(dry_run=False, rapport_dir=str(dossier))
    return base, dossier


def _cas(dsn, series_id: str) -> str:
    lignes = lire(
        dsn,
        "SELECT status, method, score, wikidata_qid FROM manga.match_decision "
        "WHERE series_id=%s",
        (series_id,),
    )
    return lignes[0] if lignes else None


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #
def test_le_plancher_elargit_une_fenetre_empirique_etroite():
    from identity.etage1_exact import calibrer

    # Distribution serrée sur 0 : [p5, p95] vaudrait [0, 0].
    calib = calibrer([0] * 50 + [1] * 5)
    assert calib["fenetre"] == (-1, 8)


def test_la_calibration_elargit_au_dela_du_plancher_si_les_donnees_l_exigent():
    from identity.etage1_exact import calibrer

    calib = calibrer([-5] * 10 + [0] * 10 + [20] * 10)
    basse, haute = calib["fenetre"]
    assert basse <= -5 and haute >= 20


def test_sans_paire_exploitable_c_est_un_arret():
    from identity.etage1_exact import ErreurEtage1, calibrer

    with pytest.raises(ErreurEtage1):
        calibrer([])


# --------------------------------------------------------------------------- #
# Les trois cases AUTO
# --------------------------------------------------------------------------- #
def test_unique_avec_auteur_concordant(etage1):
    dsn, _ = etage1
    assert _cas(dsn, 10) == ("auto", "exact_author", pytest.approx(0.97), "Q10")


def test_unique_sans_auteur_mais_annee_concordante(etage1):
    dsn, _ = etage1
    assert _cas(dsn, 11) == ("auto", "exact", pytest.approx(0.93), "Q11")


def test_multi_departage_par_l_auteur(etage1):
    dsn, _ = etage1
    assert _cas(dsn, 15) == ("auto", "exact_author", pytest.approx(0.95), "Q15a")


def test_l_identite_arrive_complete_a_l_auto(etage1):
    dsn, _ = etage1
    assert lire(
        dsn,
        "SELECT wikidata_qid, mal_id, anilist_id FROM manga.work_identity "
        "WHERE series_id=10",
    ) == [("Q10", "500", "600")]


# --------------------------------------------------------------------------- #
# Les cases de revue
# --------------------------------------------------------------------------- #
def test_sans_aucun_signal_part_en_revue(etage1):
    dsn, _ = etage1
    statut, methode, score, qid = _cas(dsn, 12)
    assert (statut, methode, score, qid) == ("needs_review", "exact", None, None)


def test_auteur_discordant_part_en_revue(etage1):
    dsn, _ = etage1
    assert _cas(dsn, 13)[0] == "needs_review"


def test_annee_discordante_interdit_l_auto_malgre_l_auteur(etage1):
    """La règle transverse : la contradiction de date prime sur l'auteur."""
    dsn, _ = etage1
    assert _cas(dsn, 14)[0] == "needs_review"
    assert lire(
        dsn, "SELECT wikidata_qid FROM manga.work_identity WHERE series_id=14"
    ) == [(None,)]


def test_ambiguite_persistante(etage1):
    dsn, _ = etage1
    assert _cas(dsn, 16)[0] == "needs_review"


def test_l_annee_seule_ne_departage_pas_un_multi(etage1):
    dsn, _ = etage1
    assert _cas(dsn, 17)[0] == "needs_review"
    assert lire(
        dsn, "SELECT wikidata_qid FROM manga.work_identity WHERE series_id=17"
    ) == [(None,)]


def test_deux_series_sur_un_meme_qid_partent_toutes_deux_en_revue(etage1):
    dsn, _ = etage1
    assert [_cas(dsn, s)[0] for s in (18, 19)] == ["needs_review", "needs_review"]
    assert lire(
        dsn,
        "SELECT count(*) FROM manga.work_identity "
        "WHERE series_id IN (18,19) AND wikidata_qid IS NOT NULL",
    ) == [(0,)]


def test_aucune_identite_remplie_hors_auto(etage1):
    dsn, _ = etage1
    assert lire(
        dsn,
        "SELECT count(*) FROM manga.work_identity "
        "WHERE series_id IN (12,13,14,16,17) AND wikidata_qid IS NOT NULL",
    ) == [(0,)]


# --------------------------------------------------------------------------- #
# Journal, livrables, idempotence
# --------------------------------------------------------------------------- #
def test_une_seule_decision_par_serie(etage1):
    dsn, _ = etage1
    assert lire(
        dsn,
        "SELECT count(*) FROM (SELECT series_id FROM manga.match_decision "
        "GROUP BY series_id HAVING count(*)>1) d",
    ) == [(0,)]


def test_livrables_ecrits(etage1):
    _, dossier = etage1
    for nom in (
        "entonnoir.md",
        "calibration_annees.md",
        "echantillon_auto.csv",
        "needs_review.csv",
        "sans_signal.csv",
    ):
        assert (dossier / nom).is_file(), nom
    sans_signal = (dossier / "sans_signal.csv").read_text(encoding="utf-8")
    assert "12," in sans_signal  # la série sans aucun signal y est isolée


def test_rejeu_n_ecrit_rien(etage1, tmp_path):
    dsn, _ = etage1
    from identity import etage1_exact

    avant = lire(
        dsn,
        "SELECT (SELECT count(*) FROM manga.match_decision), "
        "(SELECT count(*) FROM manga.work_identity WHERE wikidata_qid IS NOT NULL)",
    )[0]
    etage1_exact.construire(dry_run=False, rapport_dir=str(tmp_path / "rejeu"))
    apres = lire(
        dsn,
        "SELECT (SELECT count(*) FROM manga.match_decision), "
        "(SELECT count(*) FROM manga.work_identity WHERE wikidata_qid IS NOT NULL)",
    )[0]
    assert avant == apres


def test_dry_run_n_ecrit_rien(base, tmp_path):
    _seed(base)
    from identity import etage1_exact

    avant = lire(base, "SELECT count(*) FROM manga.match_decision")[0]
    etage1_exact.construire(dry_run=True, rapport_dir=str(tmp_path / "dry"))
    assert lire(base, "SELECT count(*) FROM manga.match_decision")[0] == avant
