"""`/metrics` : réseau interne seulement ; requêtes, latence, erreurs, encodage."""

from __future__ import annotations

import ipaddress

from conftest import entetes
from fastapi.testclient import TestClient

from api_modele.metriques import autorisee


def test_metriques_du_bouclage(api, base_api):
    api.get("/v1/sante")
    api.get("/v1/info")
    api.get("/v1/inconnue")
    r = api.get("/metrics")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    texte = r.text
    assert (
        'api_modele_requetes_total{methode="GET",route="/v1/sante",statut="200"} 1.0'
        in texte
    )
    assert (
        'api_modele_requetes_total{methode="GET",route="/v1/info",statut="401"} 1.0'
        in texte
    )
    assert 'route="inconnue",statut="404"' in texte
    assert 'api_modele_erreurs_total{code="cle_invalide"} 1.0' in texte
    assert 'api_modele_erreurs_total{code="route_inconnue"} 1.0' in texte
    assert 'api_modele_duree_requete_secondes_count{route="/v1/sante"} 1.0' in texte
    assert (
        "api_modele_encodage_en_service"
        f'{{corpus_id="v7",encodage_id="{base_api.encodage_id}"}} 1.0' in texte
    )


def test_metriques_refusees_hors_du_reseau_interne_comme_une_route_inconnue(api):
    exterieur = TestClient(api.app, client=("10.1.2.3", 50000))
    r = exterieur.get("/metrics")

    assert r.status_code == 404
    assert r.json()["code"] == "route_inconnue"
    assert exterieur.get("/v1/info", headers=entetes()).status_code == 200


def test_metriques_hors_du_contrat_public(api):
    chemins = api.get("/v1/openapi.json").json()["paths"]

    assert "/metrics" not in chemins


def test_autorisee():
    reseaux = (ipaddress.ip_network("127.0.0.1/32"), ipaddress.ip_network("::1/128"))

    assert autorisee("127.0.0.1", reseaux)
    assert autorisee("::1", reseaux)
    assert not autorisee("127.0.0.2", reseaux)
    assert not autorisee("testclient", reseaux)
    assert not autorisee(None, reseaux)
