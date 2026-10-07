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

from mesures_recherche import executer, jeu


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


def test_enregistrement_mlflow_et_eval_mesures(banc, tmp_path):
    import mlflow
    from mesures_recherche.enregistrement import enregistrer, uri_suivi

    dsn, config, _ = banc
    resultat = executer.mesurer(executer.ouvrir(dsn, config), 3)
    resultat["experience"] = "essai"
    run_id = enregistrer(resultat, dsn, stockage=tmp_path / "mlflow")

    mlflow.set_tracking_uri(uri_suivi(tmp_path / "mlflow"))
    run = mlflow.get_run(run_id)
    assert run.info.status == "FINISHED"
    assert run.data.params["jeu_version"] == "v9"
    assert run.data.params["reglage.configuration"] == "french"
    assert len(run.data.params["code_empreinte"]) == 64
    assert run.data.metrics["hit_rate_10/global/toutes"] == pytest.approx(1.0)
    pieces = {a.path for a in mlflow.MlflowClient().list_artifacts(run_id)}
    assert {"par_question.csv", "mesures.toml", "resultat.json"} <= pieces

    with psycopg.connect(dsn) as cx:
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
