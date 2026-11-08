"""Harnais du module 12 : la doublure de l'API, en mémoire et sur un port du bouclage ;
la base de l'application, jetable.

La garde réseau, sur toute la suite : une requête de httpx vers un hôte hors du bouclage
n'est pas envoyée et fait échouer le test ; aucun test n'atteint l'API réelle ni un
fournisseur.

La base (E4, lot 1) : le harnais partagé des bases jetables (`database/harnais_tests/`),
chargé par son chemin comme dans les modules 05, 08, 09, 10 et 11. Le DSN est fabriqué
par lui, à partir du conteneur qu'il lance ; aucune variable d'environnement n'est lue,
et l'absence de Docker fait sauter les tests à base — jamais de repli sur `apimanga`.
Les fixtures sont paresseuses : un test qui n'en demande aucune ne démarre pas de
conteneur. L'application y tourne sous un rôle de connexion de test, membre de `app_rw`
seul (migration 027), sans mot de passe dans le DSN (un `.pgpass` jetable) — jamais
sous `postgres`.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import secrets
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import psycopg
import pytest
import uvicorn
from argon2 import PasswordHasher
from doublure_api import CLE_ATTENDUE, Doublure
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import conninfo, sql

from application.app import creer_application
from application.config import Reglages, charger
from application.journal import FormatJson
from application.motdepasse import Hacheur
from application.sessions import NOM_COOKIE

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


#: Argon2id aux paramètres réduits : les tests hachent vite ; la production prend les
#: défauts de la bibliothèque (`Hacheur()`, éprouvé par `test_motdepasse.py`).
HACHEUR_RAPIDE = Hacheur(PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))


def jeton_du_cookie(client: TestClient) -> str | None:
    """Le jeton CSRF de la session du client, lu dans son cookie (signé, en clair)."""
    valeur = client.cookies.get(NOM_COOKIE)
    if not valeur:
        return None
    contenu = json.loads(base64.b64decode(valeur.split(".")[0]))
    jeton = contenu.get("csrf")
    return jeton if isinstance(jeton, str) else None


class Navigateur(TestClient):
    """Un client qui se comporte comme un navigateur pour le jeton CSRF : un formulaire
    envoyé porte le jeton de sa session, tiré à la première page s'il le faut. Les
    tests du jeton lui-même passent `avec_jeton=False`, ou un `jeton` explicite."""

    avec_jeton = True

    def post(  # type: ignore[override]
        self, url: Any, *args: Any, **kwargs: Any
    ) -> httpx.Response:
        donnees = kwargs.get("data")
        if self.avec_jeton and isinstance(donnees, dict) and "jeton" not in donnees:
            if jeton_du_cookie(self) is None:
                self.get("/")
            kwargs["data"] = {**donnees, "jeton": jeton_du_cookie(self) or ""}
        return super().post(url, *args, **kwargs)


def application_sur(
    doublure: Doublure, reglages: Reglages
) -> tuple[FastAPI, httpx.ASGITransport]:
    transport = httpx.ASGITransport(app=doublure.app)
    return (
        creer_application(reglages, transport=transport, hacheur=HACHEUR_RAPIDE),
        transport,
    )


@pytest.fixture
def navigateur(doublure: Doublure, reglages: Reglages) -> Iterator[TestClient]:
    """L'application contre la doublure en mémoire, vue du bouclage, démarrée."""
    application, _ = application_sur(doublure, reglages)
    with Navigateur(
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
            replace(reglages, adresse_api=adresse, delais=reduits),
            hacheur=HACHEUR_RAPIDE,
        )
        client = Navigateur(
            application, client=("127.0.0.1", 50000), raise_server_exceptions=False
        )
        client.__enter__()
        ouverts.append(client)
        return client

    yield lancer
    for client in ouverts:
        client.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
#  La base de l'application : jetable, migrée par le runner du dépôt
# --------------------------------------------------------------------------- #
RACINE_DEPOT = Path(__file__).resolve().parents[2]
ROLE_APPLICATION = "essai_app_conseiller"


def _charger_harnais() -> ModuleType:
    """Le harnais partagé des bases jetables (`database/harnais_tests/`), par chemin."""
    spec = importlib.util.spec_from_file_location(
        "harnais_tests", RACINE_DEPOT / "database" / "harnais_tests" / "__init__.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["harnais_tests"] = module
    spec.loader.exec_module(module)
    return module


harnais = _charger_harnais()


@pytest.fixture(scope="session")
def conteneur() -> Iterator[str]:
    """Un PostgreSQL jetable pour la session, lancé au premier test qui le demande."""
    raison = harnais.raison_de_sauter()
    if raison:
        pytest.skip(raison)
    with harnais.conteneur("application-test") as dsn:
        yield dsn


@pytest.fixture(scope="session")
def gabarit(conteneur: str) -> str:
    """La base gabarit, migrée une fois par session par le runner du dépôt."""
    return harnais.creer_gabarit(conteneur)


@pytest.fixture
def base(conteneur: str, gabarit: str) -> Iterator[str]:
    """Une base neuve par test, copiée du gabarit migré (DSN d'administration)."""
    with harnais.base_depuis_gabarit(conteneur, gabarit) as dsn:
        yield dsn


@pytest.fixture(scope="session")
def fichier_pgpass(
    conteneur: str, gabarit: str, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """Le rôle de connexion de test, membre de app_rw seul, et son `.pgpass` jetable."""
    del gabarit  # app_rw existe dès que le gabarit est migré
    mot_de_passe = secrets.token_urlsafe(24)
    with psycopg.connect(conteneur, autocommit=True) as cx:
        cx.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE app_rw").format(
                sql.Identifier(ROLE_APPLICATION), sql.Literal(mot_de_passe)
            )
        )
    chemin = tmp_path_factory.mktemp("pgpass") / "pgpass"
    chemin.write_text(f"*:*:*:{ROLE_APPLICATION}:{mot_de_passe}\n", encoding="utf-8")
    chemin.chmod(0o600)
    return chemin


@pytest.fixture
def pgpass(fichier_pgpass: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PGPASSFILE", str(fichier_pgpass))
    return fichier_pgpass


def dsn_du_role(dsn_admin: str) -> str:
    """Le DSN de la base du test, sous le rôle de l'application, sans mot de passe."""
    parametres = conninfo.conninfo_to_dict(dsn_admin)
    parametres.pop("password", None)
    parametres["user"] = ROLE_APPLICATION
    return conninfo.make_conninfo(**parametres)


@pytest.fixture
def base_app(base: str, pgpass: Path) -> str:
    """La base du test vue par l'application : son rôle, sans mot de passe au DSN."""
    del pgpass
    return dsn_du_role(base)
