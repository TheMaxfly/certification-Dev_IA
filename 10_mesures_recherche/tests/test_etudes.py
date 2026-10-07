"""Études — la grille du TF-IDF, son résumé, la garde de mémoire, MLflow."""

from __future__ import annotations

import sys

import pytest

from mesures_recherche import etudes, executer

ETUDES = etudes.charger()


def test_grille_declaree_36_combinaisons_et_le_temoin():
    grille = etudes.combinaisons(ETUDES)
    assert len(grille) == 36
    assert {c["unite"] for c in grille} == {"mots", "mots_et_paires", "caracteres_3_5"}
    temoin = ETUDES["tfidf"]["temoin"]
    assert temoin in grille
    m = etudes.mesure_tfidf(ETUDES, {**temoin, "unite": "caracteres_3_5"})
    assert (m["analyzer"], m["ngram_min"], m["ngram_max"]) == ("char_wb", 3, 5)
    assert m["perimetre_entites"] == "catalogue"


def agregat(portee, metrique, k, v):
    return {
        "portee": portee,
        "perimetre": "toutes",
        "metrique": metrique,
        "k": k,
        "valeur": v,
        "n_questions": 1,
    }


def resultat(unite, sub, min_df, max_df, hit, f4=0.5, duree=1.0, vocab=10):
    return {
        "combinaison": {
            "unite": unite,
            "sublinear_tf": sub,
            "min_df": min_df,
            "max_df": max_df,
        },
        "agregats": [
            agregat("global", "hit_rate", 10, hit),
            agregat("global", "ndcg", 10, hit / 2),
            agregat("famille:F4", "hit_rate", 10, f4),
            agregat("famille:F4", "ndcg", 10, f4 / 2),
            agregat("famille:F4", "mrr", 10, f4 / 4),
        ],
        "vocabulaire": vocab,
        "duree_construction_s": duree,
        "rss_pic_mio": 100.0,
        "egalites_au_seuil": 0,
    }


def test_resume_moyenne_et_etendue_par_valeur_sans_classement():
    resultats = [
        resultat("mots", True, 1, 1.0, 0.2, f4=0.1, duree=2.0, vocab=100),
        resultat("mots", False, 1, 1.0, 0.4, f4=0.3, duree=4.0, vocab=300),
        resultat("caracteres_3_5", True, 1, 1.0, 0.6, f4=0.9),
        {
            "combinaison": {
                "unite": "caracteres_3_5",
                "sublinear_tf": False,
                "min_df": 1,
                "max_df": 1.0,
            },
            "motif": "mémoire",
        },
    ]
    r = etudes.resumer_grille(resultats)
    mots = r["reglages"]["unite"]["mots"]["hit_rate@10"]
    assert mots == {"moyenne": pytest.approx(0.3), "min": 0.2, "max": 0.4, "n": 2}
    sub = r["reglages"]["sublinear_tf"]["True"]["ndcg@10"]
    assert sub["moyenne"] == pytest.approx(0.2) and sub["n"] == 2
    assert r["f4_par_unite"]["mots"]["hit_rate@10"]["moyenne"] == pytest.approx(0.2)
    assert r["cout_par_unite"]["mots"]["vocabulaire"]["max"] == 300
    assert r["cout_par_unite"]["mots"]["duree_construction_s"]["moyenne"] == 3.0
    assert r["sautees"] == [
        {"combinaison": "caracteres_3_5/brut/min_df=1/max_df=1.0", "motif": "mémoire"}
    ]
    assert "classement" not in r and "meilleur" not in str(r)


ATTENDRE = [sys.executable, "-c", "import time; time.sleep(30)"]


def test_garde_arrete_sous_le_seuil_de_memoire():
    g = etudes.sous_garde(
        ATTENDRE,
        5_000_000_000,
        10,
        lire_ram=lambda: 4_000_000_000,
        lire_swap=lambda: 0,
        periode=0.05,
    )
    assert g["motif"].startswith("mémoire vive disponible 4.00 Go")
    assert g["code"] != 0 and g["duree_s"] < 10


def test_garde_arrete_sur_swap_continu():
    compteur = iter(range(10**6))
    g = etudes.sous_garde(
        ATTENDRE,
        0,
        0.2,
        lire_ram=lambda: 10**10,
        lire_swap=lambda: next(compteur),
        periode=0.05,
    )
    assert g["motif"] == "swap écrit 0.2 s d'affilée"


def test_garde_laisse_finir_un_processus_sobre():
    g = etudes.sous_garde(
        [sys.executable, "-c", "print('ok')"],
        0,
        10,
        lire_ram=lambda: 10**10,
        lire_swap=lambda: 0,
        periode=0.05,
    )
    assert g["code"] == 0 and g["motif"] is None


def test_temoin_redonne_la_mesure_9_de_bout_en_bout(banc):
    dsn, config, _ = banc
    ctx = executer.ouvrir(dsn, config)
    mesure_9 = executer.mesurer(ctx, 9)
    r = etudes.evaluer(ctx, ETUDES, ETUDES["tfidf"]["temoin"])
    assert r["agregats"] == mesure_9["agregats"]
    assert r["vocabulaire"] == mesure_9["parametres"]["vocabulaire"]
    caracteres = etudes.evaluer(
        ctx,
        ETUDES,
        {**ETUDES["tfidf"]["temoin"], "unite": "caracteres_3_5", "min_df": 1},
    )
    assert caracteres["vocabulaire"] > r["vocabulaire"]
    assert etudes.valeur(caracteres["agregats"], "global", "hit_rate@10") is not None


def test_mlflow_un_parent_un_enfant_par_combinaison(tmp_path):
    import mlflow

    from mesures_recherche.enregistrement import uri_suivi

    resultats = [
        resultat("mots", True, 1, 1.0, 0.2),
        {
            "combinaison": {
                "unite": "caracteres_3_5",
                "sublinear_tf": True,
                "min_df": 1,
                "max_df": 1.0,
            },
            "motif": "mémoire vive disponible 4.00 Go < 5 Go",
        },
    ]
    parent = etudes.journaliser_grille(
        ETUDES,
        resultats,
        etudes.resumer_grille(resultats),
        {"essai": "oui"},
        stockage=tmp_path / "mlflow",
    )
    mlflow.set_tracking_uri(uri_suivi(tmp_path / "mlflow"))
    runs = mlflow.search_runs(
        experiment_names=[ETUDES["mlflow"]["experience"]], output_format="list"
    )
    p = mlflow.get_run(parent)
    assert p.info.status == "FINISHED" and p.data.tags["etude"] == "tfidf-sensibilite"
    enfants = [r for r in runs if r.data.tags.get("mlflow.parentRunId") == parent]
    assert len(enfants) == 2
    statuts = {r.info.run_name: r.info.status for r in enfants}
    assert statuts == {
        "mots/sublineaire/min_df=1/max_df=1.0": "FINISHED",
        "caracteres_3_5/sublineaire/min_df=1/max_df=1.0": "KILLED",
    }
    fini = next(r for r in enfants if r.info.status == "FINISHED")
    assert fini.data.metrics["hit_rate_10/global"] == pytest.approx(0.2)
