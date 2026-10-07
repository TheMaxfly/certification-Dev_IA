"""Diagnostic des classements : rangs complet et au catalogue, entités hors
catalogue, résumés par portée — sur des cas écrits à la main, puis de bout en bout
sur la base jetable, confronté au résultat d'une mesure."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from mesures_recherche import diagnostic, executer
from mesures_recherche.entites import construire
from mesures_recherche.jeu import Question
from mesures_recherche.recherche import classer

# Entités : serie:5, serie:9, kitsu:7, kitsu:5 (cf. test_entites_recherche).
LIGNES = [
    (30, "kitsu_synopsis", None, 7),
    (10, "ms_review", 5, None),
    (11, "ms_review", 5, None),
    (20, "kitsu_synopsis", 9, 70),
    (40, "kitsu_synopsis", None, 5),
]


@pytest.fixture
def ent():
    return construire(LIGNES, {5, 9})


def question(attendus, famille="F1", mode="proposition"):
    return Question("Q001", "texte", mode, famille, "au_catalogue", attendus)


def test_rangs_complet_catalogue_et_hors_catalogue(ent):
    # Fragments 10, 11, 20, 30, 40 → classement : kitsu:7 (0,9), serie:9 (0,8),
    # kitsu:5 (0,7), serie:5 (0,1).
    c = classer(ent, ent.agreger(np.array([0.1, 0.0, 0.8, 0.9, 0.7])))
    r = diagnostic.rangs(ent, c, question({5: 2, 9: 1}))
    assert (r.serie, r.rang_complet, r.rang_catalogue) == (9, 2, 1)
    assert r.hors_catalogue_top10 == 2
    assert r.score == pytest.approx(0.8) and not r.score_nul
    seule = diagnostic.rangs(ent, c, question({5: 2}))
    assert (seule.rang_complet, seule.rang_catalogue) == (4, 2)


def test_hors_catalogue_compte_dans_les_k_premieres_seulement(ent):
    c = classer(ent, ent.agreger(np.array([0.1, 0.0, 0.8, 0.9, 0.7])))
    assert diagnostic.rangs(ent, c, question({9: 1}), k=1).hors_catalogue_top10 == 1


def test_score_nul_le_rang_ne_tient_qu_au_departage(ent):
    c = classer(ent, ent.agreger(np.zeros(5)))
    r = diagnostic.rangs(ent, c, question({9: 1}))
    assert r.score_nul and r.rang_complet == 4 and r.rang_catalogue == 2


def test_aucune_serie_attendue_parmi_les_entites(ent):
    c = classer(ent, ent.agreger(np.zeros(5)))
    with pytest.raises(ValueError, match="aucune série attendue"):
        diagnostic.rangs(ent, c, question({404: 2}))


def test_resumer_medianes_et_parts():
    def r(rc, rcat, hors, famille, mode="proposition"):
        return diagnostic.Rangs("Q", famille, mode, 1, rc, rcat, hors, 0.5, False)

    lignes = [
        r(1, 1, 3, "F1"),
        r(60, 40, 5, "F1"),
        r(150, 90, 8, "F2", "reconnaissance"),
    ]
    s = diagnostic.resumer(lignes)
    assert s["global"]["n"] == 3
    assert s["global"]["mediane_rang_complet"] == 60
    assert s["global"]["mediane_rang_catalogue"] == 40
    assert s["global"]["mediane_hors_catalogue_top10"] == 5
    assert s["global"]["part_complet_50"] == pytest.approx(1 / 3)
    assert s["global"]["part_complet_100"] == pytest.approx(2 / 3)
    assert s["global"]["part_catalogue_50"] == pytest.approx(2 / 3)
    assert s["global"]["part_catalogue_100"] == pytest.approx(1.0)
    assert s["famille:F1"]["mediane_rang_complet"] == 30.5
    assert s["mode:reconnaissance"]["n"] == 1


def test_bout_en_bout_confronte_au_resultat_de_la_mesure(banc):
    dsn, config, _ = banc
    ctx = executer.ouvrir(dsn, config)
    reference = executer.mesurer(ctx, 3) | {"run_id": "essai"}
    d = diagnostic.diagnostiquer(ctx, 3, reference, lecture="F4")
    assert d["verifie_contre_run"] == "essai"
    rangs = {q["question_id"]: q for q in d["questions"]}
    assert set(rangs) == {"Q001", "Q002"}, "le refus Q003 n'est pas de rang"
    for q in reference["questions"]:
        if q["de_rang"]:
            assert (
                rangs[q["question_id"]]["rang_complet"] == (q["rang_premiere_attendue"])
            )
    (a_lire,) = d["lecture"]
    assert a_lire["question_id"] == "Q002"
    tete = a_lire["top"][0]
    assert tete["entite"] == "serie:3" and tete["titre"] == "Série 3"
    assert tete["catalogue"] is True
    kitsu = [x for x in a_lire["top"] if x["entite"].startswith("kitsu:")]
    assert kitsu and all(not x["catalogue"] for x in kitsu)
    assert all(x["titre"] == f"Kitsu {x['entite'][6:]}" for x in kitsu)
    assert a_lire["attendus"] == [{"serie": 3, "titre": "Série 3", "grade": 2}]


def test_recalcul_different_arret(banc):
    dsn, config, _ = banc
    ctx = executer.ouvrir(dsn, config)
    reference = copy.deepcopy(executer.mesurer(ctx, 3)) | {"run_id": "essai"}
    reference["questions"][0]["top"] = list(reversed(reference["questions"][0]["top"]))
    with pytest.raises(RuntimeError, match="recalcul ≠ jour 2"):
        diagnostic.diagnostiquer(ctx, 3, reference)
