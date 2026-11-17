"""Les compteurs que le tableau « application » trace existent à zéro dès le démarrage
(E5, lot 1, spec 04 — E5-24).

Le 13 novembre, la panne du catalogue était invisible sur le tableau : une série créée à
son premier événement naît à 1, et `increase()` ne dessine pas ce premier pas. Les
compteurs des questions et des refus étaient déjà pré-créés ; ceux-ci le sont aussi :
les réponses 503 par route, les appels au catalogue par route et issue."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from application.metriques import ROUTES_CATALOGUE, STATUTS_CATALOGUE, Metriques

SOURCE_CATALOGUE = (
    Path(__file__).resolve().parents[1] / "src" / "application" / "client_catalogue.py"
)


def test_chaque_route_a_sa_serie_de_reponses_503_a_zero_au_demarrage(
    navigateur: TestClient,
):
    application = navigateur.app
    assert isinstance(application, FastAPI)
    routes = [r.path for r in application.routes if isinstance(r, APIRoute)]
    assert {"/", "/question", "/connexion", "/admin/couts", "/catalogue"} <= set(routes)
    sortie = navigateur.get("/metrics").text
    for route in routes:
        ligne = f'application_requetes_total{{route="{route}",statut="503"}} 0.0'
        assert ligne in sortie, route


def test_les_appels_au_catalogue_existent_a_zero_pour_chaque_route_et_issue(
    navigateur: TestClient,
):
    sortie = navigateur.get("/metrics").text
    lues = re.findall(
        r'^application_catalogue_appels_total\{route_catalogue="(\w+)",statut="(\w+)"\}'
        r" 0\.0$",
        sortie,
        re.M,
    )
    attendues = {(r, s) for r in ROUTES_CATALOGUE for s in STATUTS_CATALOGUE}
    assert set(lues) == attendues
    assert len(attendues) == 15


def test_une_premiere_503_part_de_zero():
    """Le premier événement est un pas de 0 à 1, que `increase()` dessine."""
    metriques = Metriques()
    metriques.routes_servies(["/connexion"])
    nom = "application_requetes_total"
    etiquettes = {"route": "/connexion", "statut": "503"}
    assert metriques.registre.get_sample_value(nom, etiquettes) == 0
    metriques.requetes.labels(**etiquettes).inc()
    assert metriques.registre.get_sample_value(nom, etiquettes) == 1


def test_les_listes_du_catalogue_sont_celles_du_client():
    """Une route ou une issue nouvelle du client sans sa place dans la liste fermée :
    elle naîtrait à 1, invisible au tableau."""
    texte = SOURCE_CATALOGUE.read_text(encoding="utf-8")
    routes = set(re.findall(r'self\._noter\("(\w+)"', texte))
    routes |= set(re.findall(r'_hors_contrat\("(\w+)"\)', texte))
    statuts = set(re.findall(r'self\._noter\(\s*\w+, "(\w+)"', texte))
    statuts |= set(re.findall(r'self\._noter\("\w+", "(\w+)"', texte))
    assert routes == set(ROUTES_CATALOGUE)
    assert statuts == set(STATUTS_CATALOGUE)
