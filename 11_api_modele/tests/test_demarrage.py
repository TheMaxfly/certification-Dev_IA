"""Le contrôle de cohérence au démarrage : un écart, et l'API ne démarre pas.

La confrontation `/info` ↔ registre est celle du module 09 (`confronter`), pas une
copie ; ces tests la traversent par la doublure HTTP du service.
"""

from __future__ import annotations

import logging
from dataclasses import replace

import psycopg
import pytest
from conftest import (
    AXES,
    BaseApi,
    Doublure,
    dsn_du_role,
    environnement,
    garnir,
    inscrire_encodage,
    promouvoir,
    vectoriser,
)
from fastapi.testclient import TestClient

from api_modele import service
from api_modele.application import creer_application
from api_modele.configuration import charger
from api_modele.demarrage import DemarrageRefuse


def demarrer(reglages, instance) -> None:
    with TestClient(creer_application(reglages, instance=instance)):
        pass


def test_un_service_conforme_et_un_encodage_promu_demarrent(api, base_api):
    info = api.get("/v1/info", headers={"X-API-Key": "a" * 40}).json()

    assert info["encodage_id"] == base_api.encodage_id
    assert info["corpus_id"] == "v7"


def test_le_client_est_celui_du_09_avec_l_hote_et_le_delai_de_l_api(api):
    client = api.app.state.etat.client

    assert type(client) is service.ClientService
    assert client.delai == 10.0
    assert client.hote == "127.0.0.1"


def test_une_revision_differente_refuse(reglages, service_doublure):
    Doublure.info["model_sha"] = "0" * 40

    with pytest.raises(DemarrageRefuse, match="non conforme.*revision"):
        demarrer(reglages, service_doublure.instance)


def test_une_version_de_l_outil_differente_refuse(reglages, service_doublure):
    Doublure.info["version"] = "1.9.5"

    with pytest.raises(DemarrageRefuse, match="non conforme.*outil_version"):
        demarrer(reglages, service_doublure.instance)


def test_un_prefixe_de_question_different_refuse(reglages, service_doublure):
    instance = replace(service_doublure.instance, prefixe_requete="query: ")

    with pytest.raises(DemarrageRefuse, match="non conforme.*prefixe_requete"):
        demarrer(reglages, instance)


def test_un_service_injoignable_refuse(reglages, service_doublure):
    service_doublure.arreter()

    with pytest.raises(DemarrageRefuse, match="service d'embedding injoignable"):
        demarrer(reglages, service_doublure.instance)


def test_une_instance_d_un_autre_modele_refuse(reglages, service_doublure):
    instance = replace(service_doublure.instance, model_id="BAAI/bge-m3")

    with pytest.raises(DemarrageRefuse, match="l'instance .* sert BAAI/bge-m3"):
        demarrer(reglages, instance)


def test_une_dimension_differente_refuse(reglages, service_doublure):
    instance = replace(service_doublure.instance, dimension=1024)

    with pytest.raises(DemarrageRefuse, match="dimension 768, l'instance en rend 1024"):
        demarrer(reglages, instance)


def test_une_limite_de_jetons_trop_courte_pour_la_question_refuse(
    reglages, service_doublure
):
    """39 + 4 × 500 + 2 = 2 041 jetons au pire : 2 040 ne suffit pas."""
    Doublure.info["max_input_length"] = 2040

    with pytest.raises(DemarrageRefuse, match="2041 jetons ; le service en admet 2040"):
        demarrer(reglages, service_doublure.instance)


def test_la_borne_de_la_question_tient_juste_pour_2041_jetons(
    reglages, service_doublure
):
    Doublure.info["max_input_length"] = 2041

    demarrer(reglages, service_doublure.instance)


def test_la_borne_suit_le_tokenizer_servi():
    """Préfixe EmbeddingGemma : 29 caractères ASCII, dont 5 espaces (« ▁ », 3 octets
    chacun) → 39 octets. N = 500 : 2 041 jetons au pire ; 501 au plus pour 2 048."""
    prefixe = "task: search result | query: "

    assert service.jetons_au_plus(prefixe, 0) == 39 + 2
    assert service.jetons_au_plus(prefixe, 500) == 2041
    assert (
        service.jetons_au_plus(prefixe, 501)
        <= 2048
        < service.jetons_au_plus(prefixe, 502)
    )


def test_aucun_encodage_en_service_refuse(base, pgpass, service_doublure):
    garnir(base, service_doublure.instance)
    reglages = charger(environnement(dsn_du_role(base)))

    with pytest.raises(DemarrageRefuse, match="aucun encodage en service"):
        demarrer(reglages, service_doublure.instance)


def test_un_corpus_non_clos_refuse(base, pgpass, service_doublure):
    encodage = garnir(base, service_doublure.instance, clore=False)
    with psycopg.connect(base) as cx:
        promouvoir(cx, encodage)
    reglages = charger(environnement(dsn_du_role(base)))

    with pytest.raises(DemarrageRefuse, match="le corpus n'est pas clos"):
        demarrer(reglages, service_doublure.instance)


def test_un_encodage_non_termine_refuse(base_api: BaseApi, reglages, service_doublure):
    with psycopg.connect(base_api.dsn_admin) as cx:
        cx.execute(
            "UPDATE bench.encodages SET termine_le = NULL, nb_fragments = NULL"
            " WHERE encodage_id = %s",
            (base_api.encodage_id,),
        )

    with pytest.raises(DemarrageRefuse, match="non terminé"):
        demarrer(reglages, service_doublure.instance)


def test_la_vue_designe_le_dernier_promu(base_api: BaseApi, reglages, service_doublure):
    with psycopg.connect(base_api.dsn_admin) as cx:
        autre = inscrire_encodage(cx, service_doublure.instance, outil_version="1.9.5")
        vectoriser(cx, autre, AXES)
        promouvoir(cx, autre)
    Doublure.info["version"] = "1.9.5"

    with TestClient(
        creer_application(reglages, instance=service_doublure.instance)
    ) as client:
        assert client.app.state.etat.coherence.encodage.encodage_id == autre


def test_une_base_injoignable_refuse(pgpass, service_doublure):
    reglages = replace(
        charger(environnement("postgresql://personne@127.0.0.1:1/aucune")),
        pool_attente_s=0.5,
    )

    with pytest.raises(DemarrageRefuse, match="base injoignable"):
        demarrer(reglages, service_doublure.instance)


def test_le_refus_est_journalise(reglages, service_doublure, caplog):
    service_doublure.arreter()

    with (
        caplog.at_level(logging.INFO, logger="api_modele"),
        pytest.raises(DemarrageRefuse),
    ):
        demarrer(reglages, service_doublure.instance)

    refus = [r for r in caplog.records if r.getMessage() == "demarrage_refuse"]
    assert refus and "injoignable" in refus[0].champs["raison"]
    assert refus[0].levelno == logging.ERROR
