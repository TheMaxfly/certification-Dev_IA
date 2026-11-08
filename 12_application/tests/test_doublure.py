"""La doublure ne ment pas : chaque scénario tient le contrat du module 11."""

from __future__ import annotations

import doublure_api as d
import pytest
from fastapi.testclient import TestClient


def test_le_contrat_est_celui_de_la_version_livree():
    assert d.CONTRAT["info"]["version"] == "1.4.0"


@pytest.mark.parametrize("nom", sorted(d.SCENARIOS_REPONSE))
def test_chaque_scenario_de_reponse_tient_le_contrat(nom):
    doublure = d.Doublure()
    rendu = d.SCENARIOS_REPONSE[nom]()

    assert doublure._verifier("/v1/reponse", "post", nom, rendu) is None


@pytest.mark.parametrize("nom", sorted(d.SCENARIOS_INFO))
def test_chaque_scenario_d_info_tient_le_contrat(nom):
    doublure = d.Doublure()

    assert doublure._verifier("/v1/info", "get", nom, d.SCENARIOS_INFO[nom]()) is None


@pytest.mark.parametrize("nom", sorted(d.SCENARIOS_SANTE))
def test_chaque_scenario_de_sante_tient_le_contrat(nom):
    doublure = d.Doublure()

    assert doublure._verifier("/v1/sante", "get", nom, d.SCENARIOS_SANTE[nom]()) is None


def test_un_corps_hors_contrat_est_vu():
    doublure = d.Doublure()
    faux = d.Rendu(200, {"refus": "peut-etre"})

    assert doublure._verifier("/v1/reponse", "post", "essai", faux) is not None


def test_un_code_que_le_contrat_n_enumere_pas_est_vu():
    doublure = d.Doublure()
    faux = d.rendu_503("service_indisponible")

    assert "absent du contrat" in str(
        doublure._verifier("/v1/reponse", "post", "essai", faux)
    )


def test_un_statut_que_le_contrat_ne_declare_pas_est_vu():
    doublure = d.Doublure()
    faux = d.Rendu(418, d.erreur("theiere"))

    assert "absent du contrat" in str(
        doublure._verifier("/v1/reponse", "post", "essai", faux)
    )


def test_la_doublure_refuse_une_requete_hors_contrat():
    doublure = d.Doublure()
    client = TestClient(doublure.app)

    r = client.post("/v1/reponse", json={"question": "x", "inconnu": 1})

    assert r.status_code == 422
    assert doublure.ecarts and "hors contrat" in doublure.ecarts[0]


def test_le_scenario_se_choisit_par_la_route_d_essai():
    doublure = d.Doublure()
    client = TestClient(doublure.app)

    r = client.post(
        "/_essai/scenario", json={"reponse": "refus_inconnu", "info": "deux_modes"}
    )
    client.post("/_essai/scenario", json={"sante": "degrade"})

    assert r.json() == {
        "info": "deux_modes",
        "reponse": "refus_inconnu",
        "catalogue": "nominal",
    }
    assert client.get("/v1/sante").status_code == 503
    corps = client.post("/v1/reponse", json={"question": "x"}).json()
    assert corps["motif_refus"] == "inconnu"
    langage = client.get("/v1/info").json()["modele_langage"]
    assert len(langage["reglages_reponse"]["mode"]["valeurs"]) == 2


def test_chaque_identifiant_de_serie_citee_est_dans_les_series():
    corps = d.reponse_nominale()
    identifiants = {s["serie_id"] for s in corps["series"]}

    assert len(corps["series"]) == 5
    assert len(corps["series_citees"]) == 2
    assert {c["serie_id"] for c in corps["series_citees"]} <= identifiants
    assert sum(len(c["fragment_ids"]) for c in corps["series_citees"]) == 3
