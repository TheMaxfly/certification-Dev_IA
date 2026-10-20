"""Le journal : JSON, une ligne par requête ; le nom de la clé, jamais sa valeur."""

from __future__ import annotations

import json
import logging

from conftest import CLE_APPLICATION, entetes

from api_modele import journal
from api_modele.erreurs import ENTETE_IDENTIFIANT


def lignes(caplog) -> list[dict]:
    return [r.champs for r in caplog.records if r.getMessage() == "requete"]


def test_une_ligne_par_requete_avec_ses_champs(api, base_api, caplog):
    with caplog.at_level(logging.INFO, logger="api_modele"):
        r = api.get("/v1/info", headers=entetes())

    (ligne,) = lignes(caplog)
    assert ligne["identifiant_requete"] == r.headers[ENTETE_IDENTIFIANT]
    assert ligne["methode"] == "GET"
    assert ligne["route"] == "/v1/info"
    assert ligne["statut"] == 200
    assert ligne["code"] is None
    assert ligne["cle"] == "application"
    assert ligne["corpus_id"] == "v7"
    assert ligne["encodage_id"] == base_api.encodage_id
    assert ligne["duree_ms"] >= 0


def test_une_erreur_porte_son_code(api, caplog):
    with caplog.at_level(logging.INFO, logger="api_modele"):
        api.get("/v1/info")

    (ligne,) = lignes(caplog)
    assert (ligne["statut"], ligne["code"]) == (401, "cle_invalide")


def test_le_demarrage_est_journalise(reglages, service_doublure, caplog):
    from fastapi.testclient import TestClient

    from api_modele.application import creer_application

    with caplog.at_level(logging.INFO, logger="api_modele"):
        with TestClient(
            creer_application(reglages, instance=service_doublure.instance)
        ):
            pass

    evenements = [r.getMessage() for r in caplog.records]
    assert evenements[0] == "demarrage"
    assert evenements[-1] == "arret"
    assert caplog.records[0].champs["corpus_id"] == "v7"


def test_le_format_json_met_les_champs_a_plat():
    enregistrement = logging.LogRecord(
        "api_modele.requetes", logging.INFO, __file__, 1, "requete", None, None
    )
    enregistrement.champs = {"route": "/v1/info", "cle": "application"}

    ligne = json.loads(journal.FormatJson().format(enregistrement))

    assert ligne["evenement"] == "requete"
    assert ligne["niveau"] == "INFO"
    assert ligne["route"] == "/v1/info"
    assert ligne["horodatage"].endswith("+00:00")


def test_le_format_json_porte_la_trace_d_une_exception():
    try:
        raise ValueError("essai")
    except ValueError:
        import sys

        enregistrement = logging.LogRecord(
            "api_modele", logging.ERROR, __file__, 1, "erreur", None, sys.exc_info()
        )

    ligne = json.loads(journal.FormatJson().format(enregistrement))

    assert "ValueError: essai" in ligne["trace"]


def test_configurer_ecrit_en_json_sans_double_ecriture(capsys):
    avant = (list(journal.JOURNAL.handlers), journal.JOURNAL.propagate)
    try:
        journal.configurer()
        journal.requete({"route": "/v1/sante", "cle": "application"})
        sortie = capsys.readouterr().err.strip().splitlines()
    finally:
        journal.JOURNAL.handlers[:] = avant[0]
        journal.JOURNAL.propagate = avant[1]

    assert len(sortie) == 1
    assert json.loads(sortie[0])["route"] == "/v1/sante"
    assert CLE_APPLICATION not in sortie[0]
