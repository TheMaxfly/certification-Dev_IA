"""Le harnais de bout en bout, sur base PostgreSQL + pgvector JETABLE.

Un mini-catalogue, un mini-corpus (critiques françaises, synopsis Kitsu, dont un
rattaché par le moyeu), un mini-jeu gelé, des vecteurs unitaires : chaque
configuration tourne pour de vrai — le plein texte par PostgreSQL, le TF-IDF par
scikit-learn, le sens sur les vecteurs de la table, la fusion sur les deux. Seul
l'encodage de la question est une doublure (le service n'est pas lancé ici).
Le banc et la doublure sont dans `conftest.py`.
"""

from __future__ import annotations

import hashlib

import psycopg
import pytest

from mesures_recherche import diagnostic, executer, jeu, recherche


def questions(resultat):
    return {q["question_id"]: q for q in resultat["questions"]}


def test_empreinte_fausse_arrete_avant_toute_lecture(banc):
    dsn, config, dossier = banc
    (dossier / "questions.csv").write_text("modifié\n", encoding="utf-8")
    with pytest.raises(jeu.ArretEmpreinte, match="v9"):
        executer.ouvrir(dsn, config)


def test_empreinte_de_la_base_aussi_verifiee(banc):
    dsn, config, _ = banc
    config["jeu"]["empreinte"] = hashlib.sha256(b"autre").hexdigest()
    with pytest.raises(jeu.ArretEmpreinte):
        executer.ouvrir(dsn, config)


def test_plein_texte_ou_et_entites(banc):
    dsn, config, _ = banc
    ctx = executer.ouvrir(dsn, config)
    r = questions(executer.mesurer(ctx, 3))
    # « pirates » OU « aventure » : les deux critiques de pirates, et le synopsis
    # anglais (le stemmer français ne retient de lui que « pirates » / « adventure »).
    top = [c for c, s in r["Q001"]["top"] if s > 0]
    assert set(top[:2]) == {"serie:1", "serie:2"}
    assert r["Q001"]["metriques"]["hit_rate@5"] == 1.0
    assert r["Q002"]["top"][0][0] == "serie:3"
    assert r["Q003"]["metriques"] is None, "refus : hors des métriques de rang"
    assert len(r["Q001"]["top"]) == 4, "toutes les entités ont un rang"


def test_tfidf(banc):
    dsn, config, _ = banc
    ctx = executer.ouvrir(dsn, config)
    resultat = executer.mesurer(ctx, 6)
    r = questions(resultat)
    assert r["Q001"]["top"][0][0] in {"serie:1", "serie:2"}
    assert resultat["parametres"]["vocabulaire"] > 0


def test_sens_et_fusion(banc, encodeur_doublure):
    dsn, config, _ = banc
    ctx = executer.ouvrir(dsn, config)
    sens = questions(executer.mesurer(ctx, 1))
    assert sens["Q001"]["top"][0] == ("serie:2", 1.0)
    # Q002 vise le synopsis 104, rattaché à la série 3 par le moyeu.
    assert sens["Q002"]["top"][0] == ("serie:3", 1.0)
    assert sens["Q002"]["rang_premiere_attendue"] == 1
    fusionne = executer.mesurer(ctx, 4)
    assert fusionne["parametres"]["source_1_encodage_id"] >= 1
    assert questions(fusionne)["Q001"]["metriques"]["hit_rate@10"] == 1.0


def test_session_en_lecture_seule(banc):
    dsn, config, _ = banc
    ctx = executer.ouvrir(dsn, config)
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        ctx.cx.execute("CREATE TEMP TABLE t (x int)")


def test_rejeu_identique(banc):
    dsn, config, _ = banc
    a = executer.mesurer(executer.ouvrir(dsn, config), 3)
    b = executer.mesurer(executer.ouvrir(dsn, config), 3)
    assert a["agregats"] == b["agregats"] and a["questions"] == b["questions"]


#: L'état git que l'enregistrement relève : un arbre propre, à un commit fixé. Le
#: test ne dépend pas de l'état du dépôt où il tourne.
ARBRE_PROPRE = {"commit": "c" * 40, "arbre_module_propre": "True"}


def test_enregistrement_mlflow_et_eval_mesures(
    banc, encodeur_doublure, tmp_path, stockage_mlflow, monkeypatch
):
    """Un run de la mesure 1 (sens, donc un corpus et un encodage) : MLflow et la
    base portent les mêmes valeurs, corpus et encodage compris."""
    import mlflow

    from mesures_recherche import enregistrement
    from mesures_recherche.enregistrement import enregistrer, uri_suivi

    monkeypatch.setattr(enregistrement, "etat_git", lambda: dict(ARBRE_PROPRE))
    dsn, config, _ = banc
    with psycopg.connect(dsn) as cx:
        (e1,) = cx.execute(
            "SELECT encodage_id FROM bench.encodages WHERE corpus_id = 'v1'"
        ).fetchone()
    resultat = executer.mesurer(executer.ouvrir(dsn, config), 1)
    resultat["experience"] = "essai"
    run_id = enregistrer(resultat, dsn, stockage=stockage_mlflow)

    mlflow.set_tracking_uri(uri_suivi(stockage_mlflow))
    run = mlflow.get_run(run_id)
    assert run.info.status == "FINISHED"
    params = run.data.params
    assert params["jeu_version"] == "v9"
    assert params["reglage.instance"] == "bge-m3"
    assert params["reglage.table"] == "bench.vecteurs_bge_m3"
    assert len(params["code_empreinte"]) == 64
    assert (params["commit"], params["arbre_module_propre"]) == ("c" * 40, "True")
    assert (params["corpus_id"], params["encodage_id"]) == ("v1", str(e1))
    assert run.data.metrics["hit_rate_10/global/toutes"] == pytest.approx(1.0)
    assert run.data.tags == run.data.tags | {
        "mesure": "1",
        "tour": "1",
        "spec": "E2 jour 2",
    }
    pieces = {a.path for a in mlflow.MlflowClient().list_artifacts(run_id)}
    assert {"par_question.csv", "mesures.toml", "resultat.json"} <= pieces

    with psycopg.connect(dsn) as cx:
        assert cx.execute(
            "SELECT corpus_id, encodage_id FROM bench.eval_runs WHERE run_id = %s",
            (run_id,),
        ).fetchall() == [("v1", e1)]
        lignes = cx.execute(
            "SELECT portee, perimetre, metrique, k, valeur, n_questions"
            " FROM bench.eval_mesures WHERE run_id = %s",
            (run_id,),
        ).fetchall()
    attendu = {
        (
            a["portee"],
            a["perimetre"],
            a["metrique"],
            a["k"],
            a["valeur"],
            a["n_questions"],
        )
        for a in resultat["agregats"]
    }
    assert set(lignes) == attendu


def test_un_arbre_sale_refuse_l_enregistrement(
    banc, encodeur_doublure, stockage_mlflow, monkeypatch
):
    """On commite d'abord, on mesure ensuite : rien n'est écrit, ni run MLflow ni
    ligne en base."""
    import mlflow

    from mesures_recherche import enregistrement

    monkeypatch.setattr(
        enregistrement,
        "etat_git",
        lambda: ARBRE_PROPRE | {"arbre_module_propre": "False"},
    )
    dsn, config, _ = banc
    resultat = executer.mesurer(executer.ouvrir(dsn, config), 3)
    resultat["experience"] = "essai_arbre_sale"
    with pytest.raises(enregistrement.EnregistrementRefuse, match="non propre"):
        enregistrement.enregistrer(resultat, dsn, stockage=stockage_mlflow)
    mlflow.set_tracking_uri(enregistrement.uri_suivi(stockage_mlflow))
    assert mlflow.get_experiment_by_name("essai_arbre_sale") is None
    with psycopg.connect(dsn) as cx:
        assert cx.execute("SELECT count(*) FROM bench.eval_runs").fetchone() == (0,)


# --------------------------------------------------------------------------- #
#  Tour 2 — périmètre « catalogue », fusion TF-IDF + sens
# --------------------------------------------------------------------------- #


def test_filtre_catalogue_sens_et_tfidf(banc, encodeur_doublure):
    dsn, config, _ = banc
    ctx = executer.ouvrir(dsn, config)
    for sans, avec in ((1, 7), (6, 9)):
        r_sans, r_avec = executer.mesurer(ctx, sans), executer.mesurer(ctx, avec)
        assert r_avec["tour"] == 2 and r_avec["spec"] == "E2 jour 3"
        assert r_avec["perimetre_entites"] == "catalogue"
        assert r_sans["perimetre_entites"] == "toutes"
        for q in r_avec["questions"]:
            assert q["top"] and all(c.startswith("serie:") for c, _ in q["top"])
        controle = diagnostic.controle_filtre(r_sans, r_avec)
        assert controle["reculs"] == [] and controle["n"] == 2
    # Q001 vise le fragment 102 (série 2) : au catalogue, kitsu:60 ne compte plus.
    assert questions(executer.mesurer(ctx, 7))["Q001"]["top"][0] == ("serie:2", 1.0)


def test_controle_filtre_signale_un_recul():
    def r(n, rang):
        return {
            "mesure": n,
            "questions": [
                {"question_id": "Q001", "de_rang": True, "rang_premiere_attendue": rang}
            ],
        }

    assert diagnostic.controle_filtre(r(1, 5), r(7, 3))["avance"] == 1
    assert diagnostic.controle_filtre(r(1, 5), r(7, 6))["reculs"] == ["Q001"]


def test_fusion_tfidf_et_sens_au_catalogue(banc, encodeur_doublure, monkeypatch):
    dsn, config, _ = banc
    config["mesures"].append(
        {
            "numero": 101,
            "nom": "essai-fusion-tfidf-sens-catalogue",
            "tour": 2,
            "type": "fusion",
            "perimetre_entites": "catalogue",
            "sources": [6, 1],
            "methode": "rang_reciproque",
            "k_rrf": 60,
            "profondeur": 100,
        }
    )
    ctx = executer.ouvrir(dsn, config)
    perimetres = []
    classer = recherche.classer

    def espion(entites, scores, perimetre="toutes"):
        perimetres.append(perimetre)
        return classer(entites, scores, perimetre)

    monkeypatch.setattr(recherche, "classer", espion)
    r = executer.mesurer(ctx, 101)
    # Chaque liste source ET la liste fusionnée : au catalogue.
    assert len(perimetres) == 3 * len(ctx.questions)
    assert set(perimetres) == {"catalogue"}
    assert {"source_6_vocabulaire", "source_1_encodage_id"} <= set(r["parametres"])
    for q in r["questions"]:
        assert all(c.startswith("serie:") for c, _ in q["top"])
    assert questions(r)["Q001"]["metriques"]["hit_rate@10"] == 1.0


# --------------------------------------------------------------------------- #
#  Tour 3 — vecteur de série, trois meilleurs fragments
# --------------------------------------------------------------------------- #


def test_representations_de_l_oeuvre(banc, encodeur_doublure):
    dsn, config, _ = banc
    for numero, representation in ((102, "vecteur_serie"), (103, "trois_meilleurs")):
        config["mesures"].append(
            {
                "numero": numero,
                "nom": f"essai-{representation}",
                "tour": 3,
                "type": "semantique",
                "representation": representation,
                "perimetre_entites": "toutes",
                "instance": "bge-m3",
                "table": "bench.vecteurs_bge_m3",
                "modele": "BAAI/bge-m3",
                "similarite": "cosinus",
            }
        )
    ctx = executer.ouvrir(dsn, config)
    sens, serie, trois = (questions(executer.mesurer(ctx, n)) for n in (1, 102, 103))
    # Q002 vise le fragment 104 ; la série 3 a deux fragments (103 et 104).
    assert dict(sens["Q002"]["top"])["serie:3"] == pytest.approx(1.0)
    assert dict(serie["Q002"]["top"])["serie:3"] == pytest.approx(2**-0.5)
    assert dict(trois["Q002"]["top"])["serie:3"] == pytest.approx(0.5)
    # Q001 vise le fragment 102, seul fragment de la série 2 : rien ne change.
    for r in (serie, trois):
        assert dict(r["Q001"]["top"])["serie:2"] == pytest.approx(1.0)
