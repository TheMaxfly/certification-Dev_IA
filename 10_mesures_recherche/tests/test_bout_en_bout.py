"""Le harnais de bout en bout, sur base PostgreSQL + pgvector JETABLE.

Un mini-catalogue, un mini-corpus (critiques françaises, synopsis Kitsu, dont un
rattaché par le moyeu), un mini-jeu gelé, des vecteurs unitaires : chaque
configuration tourne pour de vrai — le plein texte par PostgreSQL, le TF-IDF par
scikit-learn, le sens sur les vecteurs de la table, la fusion sur les deux. Seul
l'encodage de la question est une doublure (le service n'est pas lancé ici).
"""

from __future__ import annotations

import copy
import hashlib

import numpy as np
import psycopg
import pytest

from mesures_recherche import configuration, executer, jeu

FRAGMENTS = {
    # chunk_id : (doc_key, texte)
    101: ("ms:1", "Un équipage de pirates part à l'aventure en haute mer."),
    102: ("ms:2", "Des pirates du ciel affrontent la marine dans une aventure."),
    103: ("ms:3", "Un carnet mortel tombe entre les mains d'un lycéen."),
    104: ("kitsu:50", "A notebook that kills whoever is named in it."),
    105: ("kitsu:60", "Space pirates on a long adventure across the stars."),
}
QUESTIONS = [
    # id, texte, mode, famille, issue, attendus
    (
        "Q001",
        "un manga de pirates et d'aventure",
        "proposition",
        "F1",
        "au_catalogue",
        {1: 2, 2: 1},
    ),
    ("Q002", "le carnet du lycéen", "reconnaissance", "F4", "au_catalogue", {3: 2}),
    ("Q003", "Le grimoire des brumes de Zolthar", "refus", "F7", "inconnue", {}),
]


def unitaire(axe: int, dim: int = 1024) -> str:
    v = [0.0] * dim
    v[axe] = 1.0
    return "[" + ",".join(map(str, v)) + "]"


@pytest.fixture
def banc(base, tmp_path):
    """Base garnie, et une configuration qui pointe sur le mini-jeu gelé."""
    dossier = tmp_path / "jeu"
    dossier.mkdir()
    (dossier / "questions.csv").write_text("le mini-jeu\n", encoding="utf-8")
    empreinte = jeu.empreinte_fichiers(dossier)
    with psycopg.connect(base) as cx:
        for sid in (1, 2, 3, 4):
            cx.execute(
                "INSERT INTO manga.ms_series_enriched (series_id, series_title)"
                " VALUES (%s, %s)",
                (sid, f"Série {sid}"),
            )
        cx.execute(
            "INSERT INTO manga.work_identity (series_id, kitsu_id) VALUES (3, '50')"
        )
        for doc_key in {d for d, _ in FRAGMENTS.values()}:
            source, ident = doc_key.split(":")
            if source == "ms":
                cx.execute(
                    "INSERT INTO bench.corpus_docs (doc_key, source, series_id,"
                    " doc_text) VALUES (%s, 'ms_review', %s, 'x')",
                    (doc_key, int(ident)),
                )
            else:
                cx.execute(
                    "INSERT INTO bench.corpus_docs (doc_key, source, kitsu_id,"
                    " doc_text) VALUES (%s, 'kitsu_synopsis', %s, 'x')",
                    (doc_key, int(ident)),
                )
        for chunk_id, (doc_key, texte) in FRAGMENTS.items():
            cx.execute(
                "INSERT INTO bench.corpus_chunks (chunk_id, doc_key, chunk_index,"
                " chunk_text) VALUES (%s, %s, 0, %s)",
                (chunk_id, doc_key, texte),
            )
        eid = cx.execute(
            "INSERT INTO bench.encodages (modele, revision, dimension,"
            " precision_calcul, prefixe_document, prefixe_requete, outil,"
            " outil_version, image, image_digest, taille_lot, nb_fragments,"
            " termine_le) VALUES"
            " ('BAAI/bge-m3', %s, 1024, 'float32', '', '', 'text-embeddings-inference',"
            " '1.9.4', 'img', %s, 16, 5, now()) RETURNING encodage_id",
            ("a" * 40, "sha256:" + "b" * 64),
        ).fetchone()[0]
        for axe, chunk_id in enumerate(FRAGMENTS):
            cx.execute(
                "INSERT INTO bench.vecteurs_bge_m3 (chunk_id, encodage_id, embedding)"
                " VALUES (%s, %s, %s)",
                (chunk_id, eid, unitaire(axe)),
            )
        cx.execute(
            "INSERT INTO bench.eval_jeux VALUES ('v9', %s, now(), 'mini-jeu de test')",
            (empreinte,),
        )
        for qid, texte, mode, famille, issue, attendus in QUESTIONS:
            cx.execute(
                "INSERT INTO bench.eval_questions (jeu_version, question_id, texte,"
                " mode, famille, issue_attendue, origine, note) VALUES"
                " ('v9', %s, %s, %s, %s, %s, 'nouvelle', 'test')",
                (qid, texte, mode, famille, issue),
            )
            for sid, grade in attendus.items():
                cx.execute(
                    "INSERT INTO bench.eval_attendus VALUES ('v9', %s, %s, %s)",
                    (qid, sid, grade),
                )
    config = copy.deepcopy(configuration.charger())
    config["jeu"] |= {"version": "v9", "empreinte": empreinte, "dossier": str(dossier)}
    return base, config, dossier


@pytest.fixture
def encodeur_doublure(monkeypatch):
    """Q001 « vise » le fragment 102, Q002 le fragment 104 (axes 1 et 3)."""
    axes = {"un manga de pirates et d'aventure": 1, "le carnet du lycéen": 3}

    def fabrique(instance_nom):
        def encoder(texte):
            v = np.zeros(1024)
            v[axes.get(texte, 4)] = 1.0
            return v

        return encoder, {"service_version": "doublure"}

    monkeypatch.setattr(executer, "encodeur_du_service", fabrique)


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
