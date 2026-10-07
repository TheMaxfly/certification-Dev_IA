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


# --------------------------------------------------------------------------- #
#  Bloc D — les voisins
# --------------------------------------------------------------------------- #


def test_hit_rate_par_profondeur():
    rangs = {"Q1": 1, "Q2": 4, "Q3": 30}
    r = etudes.hit_rate_profondeurs(rangs, [1, 3, 5, 50])
    assert r == {1: pytest.approx(1 / 3), 3: pytest.approx(1 / 3), 5: 2 / 3, 50: 1.0}


def test_distances_meme_ordre_pour_des_vecteurs_unitaires():
    import numpy as np

    rng = np.random.default_rng(1)
    m = rng.normal(size=(50, 8))
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    q = rng.normal(size=8)
    q /= np.linalg.norm(q)
    ordres = {
        d: list(np.argsort(-etudes.scores_distance(m, q, d)))
        for d in ("produit_scalaire", "cosinus", "euclidienne")
    }
    assert ordres["cosinus"] == ordres["produit_scalaire"] == ordres["euclidienne"]
    # Des vecteurs de longueurs différentes : le produit scalaire s'en écarte.
    m2 = m * rng.uniform(0.2, 3.0, size=(50, 1))
    assert list(np.argsort(-etudes.scores_distance(m2, q, "produit_scalaire"))) != (
        list(np.argsort(-etudes.scores_distance(m2, q, "cosinus")))
    )
    with pytest.raises(ValueError, match="distance inconnue"):
        etudes.scores_distance(m, q, "manhattan")


def test_voisins_de_bout_en_bout_et_mlflow(banc, encodeur_doublure, tmp_path):
    import copy

    import mlflow

    from mesures_recherche.enregistrement import uri_suivi

    dsn, config, _ = banc
    e = copy.deepcopy(ETUDES)
    e["voisins"]["mesure"] = 7  # BGE-M3, catalogue : la table du banc
    ctx = executer.ouvrir(dsn, config)
    r = etudes.etudier_voisins(ctx, e)
    assert r["distances"] == {"identiques": 3, "questions": 3, "differentes": []}
    assert set(r["hit_rate"]) == {"global", "mode:proposition", "mode:reconnaissance"}
    assert r["hit_rate"]["global"][1] == 1.0, "Q001 et Q002 trouvées au rang 1"
    run = etudes.journaliser_voisins(e, r, {"essai": "oui"}, stockage=tmp_path / "m")
    mlflow.set_tracking_uri(uri_suivi(tmp_path / "m"))
    lu = mlflow.get_run(run)
    assert lu.info.status == "FINISHED" and lu.data.tags["etude"] == "voisins"
    assert lu.data.metrics["hit_rate_50/global"] == 1.0
    assert lu.data.metrics["distances_listes_identiques"] == 3


# --------------------------------------------------------------------------- #
#  Bloc E — classification d'intention
# --------------------------------------------------------------------------- #


def test_vote_majoritaire_et_egalite_par_la_plus_proche():
    assert etudes.voter([("a", 0.9), ("b", 0.8), ("b", 0.7)]) == "b"
    assert etudes.voter([("a", 0.9), ("b", 0.8)]) == "a", "égalité : la plus proche"
    assert etudes.voter([("b", 0.9), ("a", 0.8), ("a", 0.7), ("b", 0.6)]) == "b"


def test_plus_proches_voisins_parmi_les_autres():
    import numpy as np

    vecteurs = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0], [0.1, 0.9], [0.2, 0.8]])
    etiquettes = ["a", "a", "b", "b", "b"]
    assert etudes.plus_proches_voisins(vecteurs, etiquettes, 1) == etiquettes
    # k = 3 : la première voit ses trois voisines (0.9/0.1, puis deux « b »).
    assert etudes.plus_proches_voisins(vecteurs, etiquettes, 3)[0] == "b"


def test_evaluation_exactitude_rappel_matrice_hors_valeurs():
    r = etudes.evaluer_classement(
        ["proposition", "proposition", "reconnaissance", "refus"],
        ["proposition", "reconnaissance", "reconnaissance", None],
    )
    assert r["exactitude"] == 0.5 and r["questions"] == 4
    assert r["rappel"] == {"proposition": 0.5, "reconnaissance": 1.0, "refus": 0.0}
    assert r["hors_valeurs"] == 1
    assert r["matrice"]["refus"]["hors_valeurs"] == 1
    assert r["matrice"]["proposition"]["reconnaissance"] == 1


def test_lire_le_mode_ou_rien():
    assert etudes.lire_mode('{"mode": "refus"}') == "refus"
    assert etudes.lire_mode('{"mode": "autre"}') is None
    assert etudes.lire_mode("proposition") is None
    assert etudes.lire_mode("[1]") is None


def test_invite_sans_aucun_exemple_tire_du_jeu():
    import csv
    import re

    from mesures_recherche.configuration import RACINE_DEPOT
    from mesures_recherche.generation import charger_toml

    invite = charger_toml(etudes.RACINE_MODULE / ETUDES["intention"]["llm"]["invite"])
    texte = " ".join(
        re.findall(r"\w+", (invite["systeme"] + invite["utilisateur"]).lower())
    )
    fichier = RACINE_DEPOT / "database/donnees/jeu_evaluation/v2/questions.csv"
    with fichier.open(encoding="utf-8") as f:
        questions = [ligne["texte"] for ligne in csv.DictReader(f)]
    assert len(questions) == 69
    for q in questions:
        mots = re.findall(r"\w+", q.lower())
        for i in range(len(mots) - 3):
            assert " ".join(mots[i : i + 4]) not in texte, q
    for mode in etudes.MODES:
        assert mode in invite["systeme"]


class ModeleDoublure:
    def __init__(self, reponses):
        self.reponses = iter(reponses)
        self.formats = []

    def discuter(self, msgs, opts, format=None):
        self.formats.append((format, opts["temperature"]))
        return {
            "reponse": next(self.reponses),
            "done_reason": "stop",
            "jetons_entree": 300,
            "jetons_sortie": 6,
            "latence_s": 0.2,
            "duree_totale_s": 0.2,
            "duree_chargement_s": 0.0,
        }


def test_llm_contraint_aux_trois_valeurs_et_hors_valeurs_comptees():
    from mesures_recherche.jeu import Question

    questions = [
        Question("Q1", "un manga de pirates", "proposition", "F1", "au_catalogue", {}),
        Question("Q2", "Berserk", "reconnaissance", "F1", "au_catalogue", {}),
        Question("Q3", "le grimoire inventé", "refus", "F7", "inconnue", {}),
    ]
    modele = ModeleDoublure(
        ['{"mode": "proposition"}', '{"mode": "reconnaissance"}', "je ne sais pas"]
    )
    invite = {"systeme": "S", "utilisateur": "Demande : {question}"}
    r = etudes.classer_par_llm(questions, ETUDES, modele, invite)
    assert r["exactitude"] == pytest.approx(2 / 3) and r["hors_valeurs"] == 1
    assert r["rappel"]["refus"] == 0.0
    assert all(f == etudes.SCHEMA_MODE and t == 0.0 for f, t in modele.formats)
    assert r["latence_mediane_s"] == pytest.approx(0.2)


def test_intention_par_voisins_de_bout_en_bout_et_mlflow(
    banc, encodeur_doublure, tmp_path
):
    import copy

    import mlflow

    from mesures_recherche.enregistrement import uri_suivi

    dsn, config, _ = banc
    e = copy.deepcopy(ETUDES)
    e["intention"]["voisins"]["instance"] = "bge-m3"
    ctx = executer.ouvrir(dsn, config)
    r = etudes.classer_par_voisins(ctx, e)
    assert [x["k"] for x in r["runs"]] == [1, 3, 5, 7]
    assert all(x["questions"] == 3 for x in r["runs"])
    m = etudes._experience(e, tmp_path / "m", e["intention"]["experience"])
    with m.start_run(run_name="voisins-k=1") as run:
        etudes.journaliser_classement(
            m, e, r["runs"][0], list(r["etiquettes"].values()), {"essai": "oui"}
        )
    mlflow.set_tracking_uri(uri_suivi(tmp_path / "m"))
    lu = mlflow.get_run(run.info.run_id)
    assert lu.data.tags["etude"] == "intention" and lu.data.params["k"] == "1"
    assert lu.data.metrics["reference_naive_exactitude"] == pytest.approx(1 / 3)
    pieces = {a.path for a in mlflow.MlflowClient().list_artifacts(run.info.run_id)}
    assert {"matrice_confusion.json", "matrice_confusion.md", "predictions.json"} <= (
        pieces
    )
