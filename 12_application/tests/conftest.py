"""Harnais du module 12 : la doublure de l'API, en mémoire et sur un port du bouclage.

Pas de base, donc pas de harnais de bases jetables. La garde réseau, sur toute la
suite : une requête de httpx vers un hôte hors du bouclage n'est pas envoyée et fait
échouer le test ; aucun test n'atteint l'API réelle ni un fournisseur.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any

import httpx
import pytest
import uvicorn
from doublure_api import CLE_ATTENDUE, Doublure
from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.app import creer_application
from application.config import Reglages, charger
from application.journal import FormatJson

BOUCLAGE = ("127.0.0.1", "localhost", "::1")
ADRESSE_MEMOIRE = "http://127.0.0.1:8010"
ENVIRONNEMENT = {"APPLICATION_CLE_API": CLE_ATTENDUE}


@pytest.fixture(autouse=True)
def aucun_appel_hors_du_bouclage(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[str]]:
    hors: list[str] = []
    envoyer = httpx.AsyncClient.send

    async def garder(
        client: httpx.AsyncClient, requete: httpx.Request, **options: Any
    ) -> httpx.Response:
        if requete.url.host not in BOUCLAGE:
            hors.append(requete.url.host)
            raise AssertionError(f"appel hors du bouclage : {requete.url.host}")
        return await envoyer(client, requete, **options)

    monkeypatch.setattr(httpx.AsyncClient, "send", garder)
    yield hors
    assert not hors, f"appels hors du bouclage : {hors}"


def journal_complet(caplog: pytest.LogCaptureFixture) -> str:
    """Ce que le journal écrirait, champs compris : `caplog.text` n'a que le message."""
    forme = FormatJson()
    return "\n".join(forme.format(r) for r in caplog.records)


@pytest.fixture
def reglages() -> Reglages:
    return replace(charger(ENVIRONNEMENT), adresse_api=ADRESSE_MEMOIRE)


@pytest.fixture
def doublure() -> Iterator[Doublure]:
    """La doublure ; aucun écart au contrat toléré, dans aucun test."""
    instance = Doublure()
    yield instance
    assert not instance.ecarts, instance.ecarts


def application_sur(
    doublure: Doublure, reglages: Reglages
) -> tuple[FastAPI, httpx.ASGITransport]:
    transport = httpx.ASGITransport(app=doublure.app)
    return creer_application(reglages, transport=transport), transport


@pytest.fixture
def navigateur(doublure: Doublure, reglages: Reglages) -> Iterator[TestClient]:
    """L'application contre la doublure en mémoire, vue du bouclage, démarrée."""
    application, _ = application_sur(doublure, reglages)
    with TestClient(
        application, client=("127.0.0.1", 50000), raise_server_exceptions=False
    ) as client:
        yield client


class ServeurLocal:
    """Une application servie sur un port libre du bouclage, le temps d'un test."""

    def __init__(self, application: Any) -> None:
        self.serveur = uvicorn.Server(
            uvicorn.Config(
                application, host="127.0.0.1", port=0, log_config=None, lifespan="off"
            )
        )
        self.fil = threading.Thread(target=self.serveur.run, daemon=True)

    def __enter__(self) -> str:
        self.fil.start()
        for _ in range(500):
            if self.serveur.started:
                break
            time.sleep(0.01)
        assert self.serveur.started
        port = self.serveur.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def __exit__(self, *_: object) -> None:
        self.serveur.should_exit = True
        self.fil.join(timeout=5)


@pytest.fixture
def doublure_sur_port(doublure: Doublure) -> Iterator[str]:
    """La doublure sur un port du bouclage : le seul moyen d'éprouver un vrai délai."""
    with ServeurLocal(doublure.app) as adresse:
        yield adresse


@pytest.fixture
def port_ferme() -> str:
    """Une adresse du bouclage où rien n'écoute."""
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        port = prise.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture
def demarrer(
    reglages: Reglages,
) -> Iterator[Callable[..., TestClient]]:
    """Démarre l'application contre une adresse réelle du bouclage (délais réduits)."""
    ouverts: list[TestClient] = []

    def lancer(adresse: str, **delais: float) -> TestClient:
        reduits = replace(reglages.delais, **delais) if delais else reglages.delais
        application = creer_application(
            replace(reglages, adresse_api=adresse, delais=reduits)
        )
        client = TestClient(
            application, client=("127.0.0.1", 50000), raise_server_exceptions=False
        )
        client.__enter__()
        ouverts.append(client)
        return client

    yield lancer
    for client in ouverts:
        client.__exit__(None, None, None)
