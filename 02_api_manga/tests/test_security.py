"""Autorisation par clé d'API : refus, acceptation, et silence des journaux."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.database import get_pool
from app.main import create_app
from app.security import (
    API_KEY_HEADER_NAME,
    UNAUTHORIZED_DETAIL,
    resolve_consumer,
)
from app.settings import ApiKey, Settings
from tests.faux_pool import FakePool

# Clé de test, longue de 40 caractères. Elle ne vaut que dans ce processus.
CLE_VALIDE = "cle-valide-de-test-0123456789abcdefghij"
LIBELLE = "app_backend"

# Les six routes de données. Cette liste est le contrat : si une septième route
# de données apparaissait sans y figurer, `test_openapi.py` le signalerait.
ROUTES_PROTEGEES = [
    "/kitsu/38",
    "/rag/preview",
    "/rag/export",
    "/rag/export/composition",
    "/rag/doc/kitsu:38",
    "/search?q=one%20piece",
]

ROUTES_OUVERTES = ["/live", "/health", "/docs", "/redoc", "/openapi.json"]


def construire_client(resultats: list[Any] | None = None) -> Iterator[TestClient]:
    settings = Settings(
        api_keys=(ApiKey(label=LIBELLE, secret=CLE_VALIDE),),
    )
    api = create_app(settings)
    # Le pool est simulé : ces tests portent sur l'autorisation, pas sur SQL.
    api.dependency_overrides[get_pool] = lambda: FakePool(resultats or [])
    return TestClient(api)


@pytest.fixture
def client() -> TestClient:
    return construire_client()


@pytest.mark.parametrize("route", ROUTES_PROTEGEES)
def test_sans_en_tete_une_route_de_donnees_repond_401(
    client: TestClient, route: str
) -> None:
    reponse = client.get(route)

    assert reponse.status_code == 401
    assert reponse.json()["detail"] == UNAUTHORIZED_DETAIL


@pytest.mark.parametrize("route", ROUTES_PROTEGEES)
def test_une_cle_invalide_repond_401(client: TestClient, route: str) -> None:
    reponse = client.get(route, headers={API_KEY_HEADER_NAME: "cle-inventee"})

    assert reponse.status_code == 401


@pytest.mark.parametrize("route", ROUTES_PROTEGEES)
def test_une_cle_valide_a_un_caractere_pres_repond_401(
    client: TestClient, route: str
) -> None:
    """Le test qui distingue `compare_digest` d'un `startswith` déguisé."""
    presque = CLE_VALIDE[:-1] + ("z" if CLE_VALIDE[-1] != "z" else "a")
    assert presque != CLE_VALIDE and len(presque) == len(CLE_VALIDE)

    reponse = client.get(route, headers={API_KEY_HEADER_NAME: presque})

    assert reponse.status_code == 401


def test_le_message_de_refus_n_est_pas_un_oracle(client: TestClient) -> None:
    """En-tête absent et clé invalide doivent être indiscernables du dehors.

    Deux messages différents diraient à un appelant que sa clé a bien été lue
    puis rejetée — donc qu'elle a la bonne forme. Le journal, lui, distingue.
    """
    sans = client.get("/rag/export")
    invalide = client.get("/rag/export", headers={API_KEY_HEADER_NAME: "cle-inventee"})

    assert sans.status_code == invalide.status_code == 401
    assert sans.json() == invalide.json()


@pytest.mark.parametrize("route", ROUTES_OUVERTES)
def test_les_sondes_et_la_documentation_restent_ouvertes(route: str) -> None:
    """C'est une RÈGLE, pas un oubli : la doc est ouverte, les données fermées.

    Un `/docs` qui deviendrait protégé par inadvertance casserait ce test —
    comme un `/live` qui le deviendrait, et rendrait l'API impilotable par un
    orchestrateur.
    """
    client = construire_client([(1,)])

    reponse = client.get(route)

    assert reponse.status_code == 200, route


def test_une_cle_valide_ouvre_la_route() -> None:
    rows = [("kitsu_synopsis", 43085), ("ms_hybrid", 5608), ("ms_review", 3187)]
    client = construire_client([rows])

    reponse = client.get(
        "/rag/export/composition", headers={API_KEY_HEADER_NAME: CLE_VALIDE}
    )

    assert reponse.status_code == 200
    assert reponse.json()["total"] == 51880


def test_un_en_tete_non_ascii_ne_provoque_pas_de_500(client: TestClient) -> None:
    """`compare_digest` refuse les `str` non-ASCII : sans encodage, ce serait 500.

    Les en-têtes HTTP se transportent en ISO-8859-1, et Starlette les décode
    comme tels : un appelant peut donc faire parvenir « é » au serveur, même si
    un client haut niveau refuse de le composer. On envoie les octets bruts
    pour reproduire ce que ferait une socket, et l'on vérifie au passage la
    fonction de comparaison elle-même, hors de tout client HTTP.
    """
    accentuee = "clé-accentuée-éèà-0123456789abcdef"

    assert (
        resolve_consumer((ApiKey(label=LIBELLE, secret=CLE_VALIDE),), accentuee) is None
    )

    reponse = client.get(
        "/rag/export",
        headers={API_KEY_HEADER_NAME: accentuee.encode("latin-1")},
    )

    assert reponse.status_code == 401


def test_aucune_cle_ne_transite_par_les_journaux(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contrôle 7. Une clé rejetée reste une clé : la journaliser la publierait.

    On capture un appel autorisé ET un appel refusé, puis on cherche les deux
    secrets dans la totalité des journaux — message formaté compris.
    """
    rows = [("kitsu_synopsis", 1)]
    client = construire_client([rows])
    cle_refusee = "cle-refusee-mais-bien-formee-0123456789"

    with caplog.at_level(logging.DEBUG):
        autorise = client.get(
            "/rag/export/composition",
            headers={API_KEY_HEADER_NAME: CLE_VALIDE},
        )
        refuse = client.get(
            "/rag/export/composition",
            headers={API_KEY_HEADER_NAME: cle_refusee},
        )

    assert autorise.status_code == 200
    assert refuse.status_code == 401

    journaux = "\n".join(
        [record.getMessage() for record in caplog.records]
        + [record.message for record in caplog.records if hasattr(record, "message")]
        + caplog.text.splitlines()
    )
    assert CLE_VALIDE not in journaux
    assert cle_refusee not in journaux
    # Ce qui DOIT y figurer : le libellé du consommateur autorisé.
    assert LIBELLE in journaux
