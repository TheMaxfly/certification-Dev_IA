"""Plusieurs corpus et plusieurs encodages d'un même modèle (migration 021) — ce
que le banc doit tenir.

  - aucune lecture de vecteurs sans encodage désigné ;
  - la mesure lit l'encodage désigné, sinon celui en service, sinon le seul de son
    modèle — jamais deviné entre plusieurs ; le corpus suit l'encodage ;
  - le run inscrit son corpus et son encodage, avec ses mesures ;
  - ce que sert l'instance est confronté au registre avant de mesurer.
"""

from __future__ import annotations

import inspect
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg
import pytest
from conftest import FRAGMENTS, unitaire

from mesures_recherche import executer, recherche

#: Le v2 : les mêmes documents, d'autres fragments, des vecteurs permutés — Q001
#: (axe 1) y trouve la série 1, et non plus la série 2.
AXES_V2 = {201: 1, 202: 0, 203: 2, 204: 3, 205: 4}


def ajouter_v2(dsn: str) -> int:
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.corpus (corpus_id, regle, chunking_id)"
            " SELECT 'v2', 'essai', chunking_id FROM bench.chunking_strategies"
            " WHERE name = 'char_1200_overlap_200'"
        )
        cx.execute(
            "INSERT INTO bench.corpus_docs (corpus_id, doc_key, source, series_id,"
            " kitsu_id, doc_text, title) SELECT 'v2', doc_key, source, series_id,"
            " kitsu_id, doc_text, title FROM bench.corpus_docs WHERE corpus_id = 'v1'"
        )
        for chunk_id, (doc_key, texte) in FRAGMENTS.items():
            cx.execute(
                "INSERT INTO bench.corpus_chunks (corpus_id, chunk_id, doc_key,"
                " chunk_index, chunk_text) VALUES ('v2', %s, %s, 0, %s)",
                (chunk_id + 100, doc_key, texte),
            )
        eid = cx.execute(
            "INSERT INTO bench.encodages (corpus_id, modele, revision, dimension,"
            " precision_calcul, prefixe_document, prefixe_requete, outil,"
            " outil_version, image, image_digest, taille_lot, nb_fragments,"
            " termine_le) SELECT 'v2', modele, revision, dimension, precision_calcul,"
            " prefixe_document, prefixe_requete, outil, outil_version, image,"
            " image_digest, taille_lot, nb_fragments, now() FROM bench.encodages"
            " WHERE corpus_id = 'v1' RETURNING encodage_id"
        ).fetchone()[0]
        for chunk_id, axe in AXES_V2.items():
            cx.execute(
                "INSERT INTO bench.vecteurs_bge_m3 (encodage_id, chunk_id, corpus_id,"
                " embedding) VALUES (%s, %s, 'v2', %s)",
                (eid, chunk_id, unitaire(axe)),
            )
    return eid


def encodage_v1(dsn: str) -> int:
    with psycopg.connect(dsn) as cx:
        return cx.execute(
            "SELECT encodage_id FROM bench.encodages WHERE corpus_id = 'v1'"
        ).fetchone()[0]


def tete(resultat: dict, question_id: str) -> str:
    (q,) = [q for q in resultat["questions"] if q["question_id"] == question_id]
    return q["top"][0][0]


def test_aucune_lecture_de_vecteurs_sans_encodage_designe():
    p = inspect.signature(recherche.Semantique.charger).parameters["encodage_id"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY
    assert p.default is inspect.Parameter.empty
    with pytest.raises(TypeError, match="encodage_id"):
        recherche.Semantique.charger(None, None, "bench.vecteurs_bge_m3", None)


def test_la_mesure_lit_l_encodage_designe_et_son_corpus(banc, encodeur_doublure):
    dsn, config, _ = banc
    e2 = ajouter_v2(dsn)
    r1 = executer.mesurer(executer.ouvrir(dsn, config, encodage_v1(dsn)), 1)
    ctx2 = executer.ouvrir(dsn, config, e2)
    r2 = executer.mesurer(ctx2, 1)
    assert ctx2.entites.corpus_id == "v2"
    assert (r1["corpus_id"], r2["corpus_id"], r2["encodage_id"]) == ("v1", "v2", e2)
    assert tete(r1, "Q001") == "serie:2" and tete(r2, "Q001") == "serie:1"
    # La lexicale du v2 ne lit que les fragments du v2.
    assert (
        executer.mesurer(executer.ouvrir(dsn, config, corpus_id="v2"), 3)["corpus_id"]
        == "v2"
    )


def test_plusieurs_encodages_sans_designation_refuse(banc, encodeur_doublure):
    dsn, config, _ = banc
    ajouter_v2(dsn)
    with pytest.raises(Exception, match="nommer le corpus"):
        executer.ouvrir(dsn, config)
    ctx = executer.ouvrir(dsn, config, corpus_id="v1")
    with pytest.raises(RuntimeError, match="désigner l'encodage"):
        executer.mesurer(ctx, 1)


def test_un_encodage_d_un_autre_corpus_refuse(banc, encodeur_doublure):
    dsn, config, _ = banc
    e2 = ajouter_v2(dsn)
    with pytest.raises(RuntimeError, match="corpus v2, pas v1"):
        executer.ouvrir(dsn, config, e2, "v1")


def test_par_defaut_l_encodage_en_service(banc, encodeur_doublure):
    dsn, config, _ = banc
    e2 = ajouter_v2(dsn)
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.promotions (decision, encodage_id, motif, decide_par)"
            " VALUES ('promu', %s, 'essai', 'essai')",
            (e2,),
        )
    r = executer.mesurer(executer.ouvrir(dsn, config), 1)
    assert (r["corpus_id"], r["encodage_id"], tete(r, "Q001")) == ("v2", e2, "serie:1")


def test_le_run_inscrit_son_corpus_et_son_encodage(banc, encodeur_doublure, tmp_path):
    import mlflow

    from mesures_recherche.enregistrement import enregistrer, uri_suivi

    dsn, config, _ = banc
    e1 = encodage_v1(dsn)
    resultat = executer.mesurer(executer.ouvrir(dsn, config), 1)
    resultat["experience"] = "essai"
    run_id = enregistrer(resultat, dsn, stockage=tmp_path / "mlflow")
    with psycopg.connect(dsn) as cx:
        assert cx.execute(
            "SELECT corpus_id, encodage_id FROM bench.eval_runs WHERE run_id = %s",
            (run_id,),
        ).fetchall() == [("v1", e1)]
    mlflow.set_tracking_uri(uri_suivi(tmp_path / "mlflow"))
    params = mlflow.get_run(run_id).data.params
    assert (params["corpus_id"], params["encodage_id"]) == ("v1", str(e1))


# --------------------------------------------------------------------------- #
#  Le service confronté au registre
# --------------------------------------------------------------------------- #


class Info(BaseHTTPRequestHandler):
    reponse: dict = {}

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        donnees = json.dumps(Info.reponse).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)


@pytest.fixture
def service_simule(monkeypatch):
    """Une instance « embeddinggemma » simulée : `/info` seulement."""
    from service_embedding import configuration

    serveur = HTTPServer(("127.0.0.1", 0), Info)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    vraie = configuration.charger("embeddinggemma")
    monkeypatch.setattr(
        configuration,
        "charger",
        lambda nom, dossier=None: replace(vraie, port=serveur.server_address[1]),
    )
    Info.reponse = {
        "model_id": vraie.model_id,
        "model_sha": vraie.revision,
        "model_dtype": vraie.dtype,
        "version": "1.9.4",
    }
    encodage = {
        "modele": vraie.model_id,
        "revision": vraie.revision,
        "precision_calcul": vraie.dtype,
        "outil_version": "1.9.4",
        "prefixe_requete": vraie.prefixe_requete,
    }
    yield encodage
    serveur.shutdown()


def test_un_service_conforme_au_registre_est_admis(service_simule):
    d = executer.Designation("embeddinggemma", service_simule)
    _, params = executer.encodeur_du_service(d)
    assert params["service_revision"] == service_simule["revision"]


@pytest.mark.parametrize(
    "champ, valeur",
    [("model_sha", "0" * 40), ("model_dtype", "float16"), ("version", "1.9.5")],
)
def test_un_ecart_avec_le_registre_arrete(service_simule, champ, valeur):
    Info.reponse = Info.reponse | {champ: valeur}
    with pytest.raises(executer.ServiceNonConforme):
        executer.encodeur_du_service(
            executer.Designation("embeddinggemma", service_simule)
        )


def test_sans_encodage_le_service_est_confronte_a_la_configuration(service_simule):
    executer.encodeur_du_service(executer.Designation("embeddinggemma"))
    Info.reponse = Info.reponse | {"model_sha": "0" * 40}
    with pytest.raises(executer.ServiceNonConforme):
        executer.encodeur_du_service(executer.Designation("embeddinggemma"))
