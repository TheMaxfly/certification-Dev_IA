"""`/v1/recherche` : le classement du banc, ses passages, ses erreurs.

Sur la base de test : la série 1 (une critique, fragments 701 et 702), la série 2
(atteinte seulement par le synopsis Kitsu 50, rattaché par le moyeu), l'œuvre Kitsu
60 hors catalogue ; un encodage leurre, non promu, aux vecteurs permutés. La
question d'essai vaut 0,1 · 701 + 0,7 · 702 + 0,3 · 703 + 0,9 · 704.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace

import numpy as np
import psycopg
import pytest
from conftest import (
    AXES,
    CORPUS,
    FRAGMENTS,
    QUESTION,
    VECTEURS_QUESTIONS,
    BaseApi,
    Doublure,
    entetes,
    inscrire_encodage,
    promouvoir,
    vectoriser,
)
from fastapi.testclient import TestClient

import api_modele
from api_modele import banc
from api_modele.application import creer_application
from api_modele.erreurs import ENTETE_IDENTIFIANT

PREFIXE = "task: search result | query: "


def chercher(api, **corps):
    return api.post(
        "/v1/recherche", json={"question": QUESTION} | corps, headers=entetes()
    )


def entites(reponse) -> list[str]:
    return [s["entite"] for s in reponse.json()["series"]]


def scores_attendus() -> dict[int, float]:
    q = np.asarray(VECTEURS_QUESTIONS[QUESTION])
    return {chunk_id: float(q[axe]) for chunk_id, axe in AXES.items()}


# --------------------------------------------------------------------------- #
#  Le classement
# --------------------------------------------------------------------------- #
def test_par_defaut_les_series_du_catalogue_avec_leur_meilleur_passage(api):
    r = chercher(api)

    assert r.status_code == 200
    s = scores_attendus()
    series = r.json()["series"]
    assert [x["entite"] for x in series] == ["serie:1", "serie:2"]
    un, deux = series
    assert (un["rang"], un["serie_id"], un["kitsu_id"], un["titre"]) == (
        1,
        1,
        None,
        "Série 1",
    )
    assert un["score"] == pytest.approx(s[702], abs=1e-6)
    assert un["passages"] == [
        {
            "fragment_id": 702,
            "doc_key": "ms_review:1",
            "type_document": "critique",
            "score": un["score"],
            "extrait": FRAGMENTS[702][1],
        }
    ]
    # La série 2, atteinte par un synopsis Kitsu : son titre reste le titre MS.
    assert (deux["rang"], deux["titre"]) == (2, "Série 2")
    assert deux["passages"][0]["type_document"] == "synopsis_kitsu"
    assert deux["passages"][0]["doc_key"] == "kitsu:50"


def test_hors_catalogue_une_oeuvre_kitsu_sans_serie(api):
    r = chercher(api, catalogue_seul=False)

    assert entites(r) == ["kitsu:60", "serie:1", "serie:2"]
    kitsu = r.json()["series"][0]
    assert (kitsu["serie_id"], kitsu["kitsu_id"], kitsu["titre"]) == (
        None,
        60,
        "Kitsu Soixante",
    )


def test_les_vecteurs_lus_sont_ceux_de_l_encodage_en_service(api):
    """Le leurre (non promu) classerait la série 2 devant la série 1."""
    assert entites(chercher(api)) == ["serie:1", "serie:2"]


def test_les_passages_par_score_decroissant(api):
    passages = chercher(api, passages_par_serie=2).json()["series"][0]["passages"]

    assert [p["fragment_id"] for p in passages] == [702, 701]
    assert passages[0]["score"] > passages[1]["score"]


def test_nombre_series_tronque_le_classement(api):
    assert entites(chercher(api, nombre_series=1)) == ["serie:1"]
    assert entites(chercher(api, nombre_series=50, catalogue_seul=False)) == [
        "kitsu:60",
        "serie:1",
        "serie:2",
    ]


def test_le_classement_est_celui_du_module_10(api, base_api: BaseApi):
    """Même code, même base : clés et scores égaux, à l'identique."""
    entites_ = banc.module("entites")
    recherche = banc.module("recherche")
    q = np.asarray(VECTEURS_QUESTIONS[QUESTION], dtype=np.float64)
    with psycopg.connect(base_api.dsn_admin) as cx:
        ent = entites_.charger(cx, CORPUS)
        sem = recherche.Semantique.charger(
            cx,
            ent,
            "bench.vecteurs_embeddinggemma",
            lambda _t: q,
            encodage_id=base_api.encodage_id,
        )
    question = banc.module("jeu").Question("q", QUESTION, "", "", "")
    for perimetre, catalogue in (("catalogue", True), ("toutes", False)):
        attendu = recherche.classer(ent, sem.scores(question), perimetre).top(ent, 10)
        series = chercher(api, catalogue_seul=catalogue).json()["series"]
        assert [(s["entite"], s["score"]) for s in series] == attendu


def test_la_question_part_au_service_prefixee_et_sans_troncature(api):
    chercher(api)

    (envoi,) = [r for r in Doublure.recus if r["route"] == "/embed"]
    assert envoi["inputs"] == [PREFIXE + QUESTION]
    assert (envoi["truncate"], envoi["normalize"]) == (False, True)


def test_les_extraits_sont_le_texte_meme_des_fragments(api, base_api: BaseApi):
    """Rien n'est réécrit : un extrait est le `chunk_text` du corpus en service.

    D'où la règle 3.1 de l'étape 1 pour l'API : les occurrences de pseudonymes d'un
    extrait sont celles du corpus, contrôlées à sa construction et dont l'empreinte
    est relue à chaque `verify-poste`."""
    series = chercher(
        api, catalogue_seul=False, passages_par_serie=5, nombre_series=50
    ).json()["series"]
    with psycopg.connect(base_api.dsn_admin) as cx:
        textes = dict(
            cx.execute(
                "SELECT chunk_id, chunk_text FROM bench.corpus_chunks"
                " WHERE corpus_id = %s",
                (CORPUS,),
            ).fetchall()
        )

    extraits = {p["fragment_id"]: p["extrait"] for s in series for p in s["passages"]}
    assert set(extraits) == set(FRAGMENTS)
    assert extraits == {c: textes[c] for c in extraits}


# --------------------------------------------------------------------------- #
#  Le contexte et le journal
# --------------------------------------------------------------------------- #
def test_le_contexte_dit_ce_qui_a_servi(api, base_api: BaseApi):
    r = chercher(api, nombre_series=3, passages_par_serie=2, catalogue_seul=False)

    contexte = r.json()["contexte"]
    assert contexte["reglages"] == {
        "nombre_series": 3,
        "passages_par_serie": 2,
        "catalogue_seul": False,
    }
    assert contexte["encodage_id"] == base_api.encodage_id
    assert contexte["corpus_id"] == CORPUS
    assert contexte["revision"] == Doublure.info["model_sha"]
    assert contexte["modele"] == Doublure.info["model_id"]
    assert contexte["precision"] == "float32"
    assert contexte["identifiant_requete"] == r.headers[ENTETE_IDENTIFIANT]
    assert contexte["duree_ms"] > 0
    assert contexte["version_api"] == api_modele.__version__


def test_le_journal_porte_identite_et_reglages_jamais_la_question(api, caplog):
    with caplog.at_level(logging.INFO, logger="api_modele"):
        chercher(api, identite="session-42")

    (ligne,) = [r.champs for r in caplog.records if r.getMessage() == "requete"]
    assert ligne["identite"] == "session-42"
    assert ligne["reglages"] == {
        "nombre_series": 10,
        "passages_par_serie": 1,
        "catalogue_seul": True,
    }
    assert ligne["route"] == "/v1/recherche"
    assert "question" not in ligne
    assert QUESTION not in str([r.__dict__ for r in caplog.records])


def test_le_journal_porte_la_question_sur_reglage(reglages, service_doublure, caplog):
    application = creer_application(
        replace(reglages, journal_question=True), instance=service_doublure.instance
    )
    with (
        TestClient(application) as api,
        caplog.at_level(logging.INFO, logger="api_modele"),
    ):
        chercher(api)

    (ligne,) = [r.champs for r in caplog.records if r.getMessage() == "requete"]
    assert ligne["question"] == QUESTION


# --------------------------------------------------------------------------- #
#  L'entrée : bornes, types, champs inconnus
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("champ", "valeur", "statut"),
    [
        ("nombre_series", 1, 200),
        ("nombre_series", 50, 200),
        ("nombre_series", 0, 422),
        ("nombre_series", 51, 422),
        ("passages_par_serie", 1, 200),
        ("passages_par_serie", 5, 200),
        ("passages_par_serie", 0, 422),
        ("passages_par_serie", 6, 422),
    ],
)
def test_les_bornes_des_reglages(api, champ, valeur, statut):
    r = chercher(api, **{champ: valeur})

    assert r.status_code == statut
    if statut == 422:
        corps = r.json()
        bornes = [1, 50] if champ == "nombre_series" else [1, 5]
        assert corps["code"] == "entree_invalide"
        assert corps["details"]["champs"] == [
            {"champ": champ, "raison": "hors_bornes", "bornes": bornes}
        ]


@pytest.mark.parametrize(
    ("corps", "champ", "raison"),
    [
        ({"question": ""}, "question", "hors_bornes"),
        ({"question": "   "}, "question", "vide"),
        ({"question": 123}, "question", "type"),
        ({}, "question", "manquant"),
        ({"question": QUESTION, "nombre_series": "10"}, "nombre_series", "type"),
        ({"question": QUESTION, "catalogue_seul": "vrai"}, "catalogue_seul", "type"),
        ({"question": QUESTION, "corpus_id": "v1"}, "corpus_id", "inconnu"),
        ({"question": QUESTION, "encodage_id": 2}, "encodage_id", "inconnu"),
        ({"question": QUESTION, "identite": "a b"}, "identite", "forme"),
    ],
)
def test_une_entree_invalide_nomme_son_champ(api, corps, champ, raison):
    r = api.post("/v1/recherche", json=corps, headers=entetes())

    assert r.status_code == 422
    assert r.json()["code"] == "entree_invalide"
    assert {"champ": champ, "raison": raison}.items() <= r.json()["details"]["champs"][
        0
    ].items()


def test_question_trop_longue_refusee_avant_tout_appel_au_service(api):
    r = api.post("/v1/recherche", json={"question": "é" * 501}, headers=entetes())

    assert r.status_code == 422
    assert r.json()["code"] == "question_trop_longue"
    assert r.json()["details"] == {
        "longueur": 501,
        "maximum": 500,
        "unite": "caracteres",
    }
    assert [x for x in Doublure.recus if x["route"] == "/embed"] == []


def test_question_de_500_caracteres_admise(api):
    r = api.post("/v1/recherche", json={"question": "é" * 500}, headers=entetes())

    assert r.status_code == 200


def test_la_cle_est_exigee_avant_l_entree(api):
    r = api.post("/v1/recherche", json={"nombre_series": 0})

    assert r.status_code == 401


# --------------------------------------------------------------------------- #
#  Le service et la base : codes et délais
# --------------------------------------------------------------------------- #
def test_un_refus_du_service_pour_sa_longueur_devient_422(api):
    Doublure.refus_embed = 413
    Doublure.jetons = 3000

    r = chercher(api)

    assert r.status_code == 422
    assert r.json()["code"] == "question_trop_longue"
    assert r.json()["details"] == {
        "longueur": 3000,
        "maximum": 2048,
        "unite": "jetons",
    }


def test_un_autre_refus_du_service_503(api):
    Doublure.refus_embed = 500

    r = chercher(api)

    assert r.status_code == 503
    assert r.json()["code"] == "service_embedding_indisponible"
    assert r.json()["details"] == {"cause": "refus"}


def test_service_arrete_503(api, service_doublure):
    service_doublure.arreter()

    r = chercher(api)

    assert r.status_code == 503
    assert r.json()["code"] == "service_embedding_indisponible"
    assert r.json()["details"] == {"cause": "injoignable"}


def test_service_incoherent_503(api):
    Doublure.refus_embed = 500
    Doublure.info["model_sha"] = "0" * 40

    r = chercher(api)

    assert r.status_code == 503
    assert r.json()["code"] == "service_embedding_incoherent"


def test_service_lent_503_au_bout_du_delai(reglages, service_doublure):
    Doublure.retard_s = 2.0
    application = creer_application(
        replace(reglages, delai_service_s=0.3), instance=service_doublure.instance
    )
    with TestClient(application) as api:
        r = chercher(api)

    assert r.status_code == 503
    assert r.json()["code"] == "service_embedding_indisponible"
    assert r.json()["details"] == {"cause": "delai_depasse", "delai_s": 0.3}


def test_base_injoignable_503(api):
    api.app.state.etat.pool.close()

    r = chercher(api)

    assert r.status_code == 503
    assert r.json()["code"] == "base_indisponible"
    assert r.json()["details"] == {"cause": "injoignable"}


def test_base_lente_503_au_bout_du_delai(base_api: BaseApi, reglages, service_doublure):
    """Une table verrouillée par une autre session : la requête attend, le délai de
    la transaction (statement_timeout) l'interrompt."""
    application = creer_application(
        replace(reglages, delai_base_s=0.5), instance=service_doublure.instance
    )
    verrou_pose = threading.Event()
    liberer = threading.Event()

    def verrouiller() -> None:
        with psycopg.connect(base_api.dsn_admin) as cx:
            cx.execute("LOCK TABLE manga.ms_series_enriched IN ACCESS EXCLUSIVE MODE")
            verrou_pose.set()
            liberer.wait(10)

    with TestClient(application) as api:
        fil = threading.Thread(target=verrouiller)
        fil.start()
        verrou_pose.wait(10)
        try:
            r = chercher(api)
        finally:
            liberer.set()
            fil.join()

    assert r.status_code == 503
    assert r.json()["code"] == "base_indisponible"
    assert r.json()["details"] == {"cause": "delai_depasse", "delai_s": 0.5}


def test_encodage_change_depuis_le_demarrage_503(
    api, base_api: BaseApi, service_doublure
):
    with psycopg.connect(base_api.dsn_admin) as cx:
        autre = inscrire_encodage(cx, service_doublure.instance, outil_version="1.9.5")
        vectoriser(cx, autre, AXES)
        promouvoir(cx, autre)

    r = chercher(api)

    assert r.status_code == 503
    assert r.json()["code"] == "encodage_en_service_change"
    assert r.json()["details"] == {
        "charge": base_api.encodage_id,
        "en_service": autre,
    }


def test_des_vecteurs_manquants_empechent_le_demarrage(
    base_api: BaseApi, reglages, service_doublure
):
    from api_modele.demarrage import DemarrageRefuse

    with psycopg.connect(base_api.dsn_admin) as cx:
        cx.execute(
            "DELETE FROM bench.vecteurs_embeddinggemma WHERE encodage_id = %s"
            " AND chunk_id = 704",
            (base_api.encodage_id,),
        )

    with pytest.raises(DemarrageRefuse, match="chargement du classement"):
        with TestClient(
            creer_application(reglages, instance=service_doublure.instance)
        ):
            pass
