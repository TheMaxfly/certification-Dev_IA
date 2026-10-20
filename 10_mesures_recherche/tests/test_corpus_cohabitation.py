"""Plusieurs corpus et plusieurs encodages d'un même modèle (migration 021) — ce
que le banc doit tenir.

  - aucune lecture de vecteurs sans encodage désigné ;
  - la mesure lit l'encodage désigné, sinon celui en service, sinon le seul de son
    modèle — jamais deviné entre plusieurs ; le corpus suit l'encodage ;
  - le run inscrit son corpus et son encodage, avec ses mesures
    (`test_bout_en_bout.py::test_enregistrement_mlflow_et_eval_mesures`) ;
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
from service_embedding.client import ServiceNonConforme

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


def test_la_regle_du_corpus_lu_est_la_meme_dans_le_05_et_le_09():
    """« Plusieurs corpus → le nommer » est écrite deux fois, sans vue partagée
    (décision de Max, pas de migration) : 05 `atteignabilite.corpus_lu` et 09
    `controles.fragments`. Le 10 voit les deux ; une copie qui dériverait casse
    ici. Le comportement est éprouvé dans le 05
    (`test_un_seul_corpus_est_lu_sans_etre_nomme`)."""
    from service_embedding import controles

    from mesures_recherche.entites import charger_atteignabilite

    attendu = charger_atteignabilite().SQL_CORPUS_PAR_DEFAUT
    assert "v_encodage_en_service" in attendu, "le test ne prouverait rien"
    assert controles.SQL_CORPUS_LU == attendu


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


def test_l_encodage_en_service_d_un_autre_corpus_arrete_la_mesure(
    banc, encodeur_doublure
):
    """La mesure lit le v2 (`--corpus v2`), l'encodage en service est celui du v1 :
    la mesure par le sens s'arrête — désigner l'encodage ; la lexicale, elle, lit le
    v2 sans vecteurs."""
    dsn, config, _ = banc
    ajouter_v2(dsn)
    e1 = encodage_v1(dsn)
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.promotions (decision, encodage_id, motif, decide_par)"
            " VALUES ('promu', %s, 'essai', 'essai')",
            (e1,),
        )
    ctx = executer.ouvrir(dsn, config, corpus_id="v2")
    assert executer.mesurer(ctx, 3)["corpus_id"] == "v2"
    with pytest.raises(RuntimeError, match=f"encodage {e1} : corpus v1, la mesure lit"):
        executer.mesurer(ctx, 1)


def test_un_encodage_d_un_autre_modele_ou_non_termine_refuse(banc):
    dsn, _, _ = banc
    e1 = encodage_v1(dsn)
    with psycopg.connect(dsn) as cx:
        with pytest.raises(RuntimeError, match="la mesure attend google/"):
            executer.encodage_designe(cx, "google/embeddinggemma-300m", e1)
        (en_cours,) = cx.execute(
            "INSERT INTO bench.encodages (modele, revision, dimension,"
            " precision_calcul, prefixe_document, prefixe_requete, outil,"
            " outil_version, image, image_digest, taille_lot) SELECT modele, %s,"
            " dimension, precision_calcul, prefixe_document, prefixe_requete, outil,"
            " outil_version, image, image_digest, taille_lot FROM bench.encodages"
            " WHERE encodage_id = %s RETURNING encodage_id",
            ("d" * 40, e1),
        ).fetchone()
        with pytest.raises(RuntimeError, match=f"encodage {en_cours} : non terminé"):
            executer.encodage_designe(cx, "BAAI/bge-m3", en_cours)
        # Sans désignation, un encodage en cours n'est jamais choisi.
        assert executer.encodage_designe(cx, "BAAI/bge-m3")["encodage_id"] == e1


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
    # Interrogation courte : `shutdown()` attend la fin d'un cycle (0,5 s par défaut).
    threading.Thread(
        target=serveur.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
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


def test_l_encodeur_rendu_encode_la_question_au_role_requete(
    service_simule, monkeypatch
):
    """Une question, un appel au service, rôle « requete » : son vecteur."""
    from service_embedding.client import ClientService

    recus = []

    def encoder(self, textes, role):
        recus.append((list(textes), role))
        return [[0.6, 0.8]]

    monkeypatch.setattr(ClientService, "encoder", encoder)
    d = executer.Designation("embeddinggemma", service_simule)
    encoder_question, _ = executer.encodeur_du_service(d)
    assert encoder_question("un manga de pirates") == [0.6, 0.8]
    assert recus == [(["un manga de pirates"], "requete")]


def test_un_ecart_avec_le_registre_arrete(service_simule):
    """La preuve du branchement : la règle, champ par champ, est éprouvée dans le
    09 (`test_client.py`, `confronter`)."""
    Info.reponse = Info.reponse | {"model_sha": "0" * 40}
    with pytest.raises(ServiceNonConforme):
        executer.encodeur_du_service(
            executer.Designation("embeddinggemma", service_simule)
        )


def test_sans_encodage_le_service_est_confronte_a_la_configuration(service_simule):
    executer.encodeur_du_service(executer.Designation("embeddinggemma"))
    Info.reponse = Info.reponse | {"model_sha": "0" * 40}
    with pytest.raises(ServiceNonConforme):
        executer.encodeur_du_service(executer.Designation("embeddinggemma"))


def test_un_prefixe_de_requete_qui_n_est_pas_celui_de_l_encodage_arrete(
    service_simule,
):
    """La mesure confronte le préfixe du rôle « requete » (EmbeddingGemma en a un,
    distinct de celui des documents)."""
    encodage = service_simule | {"prefixe_requete": "un autre préfixe : "}
    with pytest.raises(ServiceNonConforme, match="prefixe_requete"):
        executer.encodeur_du_service(executer.Designation("embeddinggemma", encodage))
