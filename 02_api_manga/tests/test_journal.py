"""Le journal du catalogue (E5, lot 1, specs 04 et 04 bis), prouvé sur des lignes
réelles : uvicorn lancé comme le déploiement le lance (`--log-config
app/journalisation.json --no-access-log`), sur un port du bouclage, la base injoignable.

Ce qu'il garde : une ligne JSON par événement, de la forme des deux autres services ;
une ligne de requête — méthode, chemin sans sa chaîne de requête, statut, durée. Ce
qu'il ne garde pas : le texte d'une recherche, l'adresse de l'appelant, une clé ; les
lectures de
santé normales ; une base perdue à chaque lecture (seulement quand elle tombe et quand
elle revient).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient
from psycopg import OperationalError

from app.database import get_pool
from app.journal import CONFIGURATION, FormatJson
from app.main import create_app
from app.settings import ApiKey, Settings

MODULE = Path(__file__).resolve().parents[1]
CLES = {"horodatage", "niveau", "evenement", "trace"}
CHAMPS_DE_REQUETE = {"methode", "chemin", "statut", "duree_ms"}
#: Une clé jetable : elle n'ouvre rien, le serveur de l'essai n'a pas de base.
CLE_ESSAI = "cle-jetable-du-journal-0123456789abcdef"
#: Ce qu'un visiteur aurait cherché : il ne doit jamais paraître au journal.
RECHERCHE = "texte-cherche-par-un-visiteur"


def port_libre() -> int:
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        return int(prise.getsockname()[1])


def lire(url: str, cle: str | None = None) -> int:
    requete = Request(url, headers={"X-API-Key": cle} if cle else {})
    try:
        with urlopen(requete, timeout=10) as reponse:  # le bouclage seul
            return int(reponse.status)
    except HTTPError as erreur:
        return int(erreur.code)


@pytest.fixture(scope="module")
def lignes() -> list[str]:
    """Le journal d'un vrai uvicorn, base injoignable : trois lectures de `/live`, trois
    de `/health` (503), une recherche avec la clé (503)."""
    port, base = port_libre(), port_libre()
    environnement = {
        "PATH": os.environ.get("PATH", ""),
        "API_KEYS": f"essai:{CLE_ESSAI}",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": str(base),
        "DB_CONNECT_TIMEOUT": "1",
        "DB_POOL_TIMEOUT": "1",
    }
    commande = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host=127.0.0.1",
        f"--port={port}",
        "--log-config=app/journalisation.json",
        "--no-access-log",
    ]
    serveur = subprocess.Popen(  # une commande fixe, sans shell
        commande,
        cwd=MODULE,
        env=environnement,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    adresse = f"http://127.0.0.1:{port}"
    try:
        for _ in range(300):
            try:
                if lire(f"{adresse}/live") == 200:
                    break
            except URLError:
                pass
            time.sleep(0.05)
        else:
            pytest.fail("uvicorn n'a pas démarré")
        for _ in range(2):
            assert lire(f"{adresse}/live") == 200
        for _ in range(3):
            assert lire(f"{adresse}/health") == 503
        assert lire(f"{adresse}/series?q={RECHERCHE}&limit=5", CLE_ESSAI) == 503
    finally:
        serveur.terminate()
        sortie, _ = serveur.communicate(timeout=15)
    return [ligne for ligne in sortie.splitlines() if ligne.strip()]


def evenements(lignes: list[str]) -> list[dict[str, Any]]:
    return [json.loads(ligne) for ligne in lignes]


def test_chaque_ligne_est_un_objet_json_de_la_forme_commune(lignes: list[str]):
    assert len(lignes) >= 5
    for ligne in lignes:
        objet = json.loads(ligne)
        assert set(objet) <= CLES | CHAMPS_DE_REQUETE, ligne
        assert {"horodatage", "niveau", "evenement"} <= set(objet), ligne
        assert objet["horodatage"].endswith("+00:00")


def test_une_ligne_reelle_ne_porte_ni_le_texte_cherche_ni_l_adresse(
    lignes: list[str],
):
    assert not any(RECHERCHE in ligne or "q=" in ligne for ligne in lignes)
    requetes = [e for e in evenements(lignes) if e["evenement"] == "requete"]
    (recherche,) = [r for r in requetes if r["chemin"] == "/series"]
    assert set(recherche) == {"horodatage", "niveau", "evenement"} | CHAMPS_DE_REQUETE
    assert (recherche["methode"], recherche["statut"]) == ("GET", 503)
    for requete in requetes:
        assert "127.0.0.1" not in json.dumps(requete)
    # Le journal d'accès d'uvicorn est coupé : la ligne de requête le remplace.
    assert not any('HTTP/1.1"' in ligne for ligne in lignes)


def test_les_lectures_de_sante_normales_ne_s_ecrivent_pas_les_erreurs_si(
    lignes: list[str],
):
    requetes = [e for e in evenements(lignes) if e["evenement"] == "requete"]
    assert not [r for r in requetes if r["chemin"] == "/live"]
    sante = [r for r in requetes if r["chemin"] == "/health"]
    assert [r["statut"] for r in sante] == [503, 503, 503]


def test_la_base_perdue_s_ecrit_une_fois_avec_sa_trace(lignes: list[str]):
    tous = evenements(lignes)
    perdue = [e for e in tous if e["evenement"] == "PostgreSQL health check failed"]
    assert len(perdue) == 1
    assert perdue[0]["niveau"] == "WARNING"
    assert "Traceback" in perdue[0]["trace"]
    (autorise,) = [
        e for e in tous if e["evenement"].startswith("appel autorisé sur /series")
    ]
    assert "consommateur « essai »" in autorise["evenement"]


def test_aucune_cle_ne_passe_au_journal(lignes: list[str]):
    assert not any(CLE_ESSAI in ligne for ligne in lignes)


# --------------------------------------------------------------------------- #
#  Sur l'application en mémoire : une base qu'on fait tomber et revenir
# --------------------------------------------------------------------------- #


class PoolBasculable:
    """Un pool simulé dont la base tombe et revient : `en_panne`."""

    def __init__(self) -> None:
        self.en_panne = False

    @contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[Any]:
        del timeout
        pool = self

        class Curseur:
            def __enter__(self) -> Curseur:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

            def execute(self, _sql: str, _params: Any = None) -> None:
                if pool.en_panne:
                    raise OperationalError("hote injoignable")

            def fetchone(self) -> Any:
                return (True,)

            def fetchall(self) -> Any:
                return []

        class Connexion:
            def __enter__(self) -> Connexion:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

            def cursor(self) -> Curseur:
                return Curseur()

        yield Connexion()


@pytest.fixture
def application() -> Iterator[tuple[TestClient, PoolBasculable]]:
    api = create_app(Settings(api_keys=(ApiKey(label="essai", secret=CLE_ESSAI),)))
    pool = PoolBasculable()
    api.dependency_overrides[get_pool] = lambda: pool
    yield TestClient(api, raise_server_exceptions=False), pool


def journal(caplog: pytest.LogCaptureFixture, nom: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == nom]


def test_cent_lectures_de_sante_normales_n_ecrivent_aucune_ligne(
    application: tuple[TestClient, PoolBasculable], caplog: pytest.LogCaptureFixture
):
    client, _ = application
    assert client.get("/health").status_code == 200  # la normalisation lue, dite
    with caplog.at_level(logging.INFO):
        for _ in range(50):
            assert client.get("/live").status_code == 200
            assert client.get("/health").status_code == 200
    assert journal(caplog, "app.requetes") == []
    assert journal(caplog, "app.main") == []


def test_la_base_tombee_puis_revenue_ecrit_deux_lignes(
    application: tuple[TestClient, PoolBasculable], caplog: pytest.LogCaptureFixture
):
    client, pool = application
    assert client.get("/health").status_code == 200  # la normalisation lue
    with caplog.at_level(logging.INFO):
        pool.en_panne = True
        for _ in range(10):
            assert client.get("/health").status_code == 503
        pool.en_panne = False
        for _ in range(10):
            assert client.get("/health").status_code == 200
    base = journal(caplog, "app.main")
    assert [(r.levelname, r.getMessage()) for r in base] == [
        ("WARNING", "PostgreSQL health check failed"),
        ("INFO", "PostgreSQL health check: database reachable again"),
    ]
    assert base[0].exc_info is not None
    # Les réponses en erreur, elles, ont chacune leur ligne de requête.
    assert len(journal(caplog, "app.requetes")) == 10


def test_la_ligne_de_requete_ne_garde_ni_la_recherche_ni_l_appelant(
    application: tuple[TestClient, PoolBasculable], caplog: pytest.LogCaptureFixture
):
    client, _ = application
    with caplog.at_level(logging.INFO):
        client.get("/series", params={"q": RECHERCHE}, headers={"X-API-Key": CLE_ESSAI})
    (ligne,) = journal(caplog, "app.requetes")
    champs = ligne.champs  # type: ignore[attr-defined]
    assert set(champs) == CHAMPS_DE_REQUETE
    assert champs["chemin"] == "/series"
    ecrit = FormatJson().format(ligne)
    assert RECHERCHE not in ecrit and "q=" not in ecrit
    assert "testclient" not in ecrit  # l'adresse du client de test


def test_le_formateur_ne_rend_que_les_champs_communs_et_ceux_de_l_evenement():
    enregistrement = logging.LogRecord(
        "app.main", logging.WARNING, __file__, 1, "un %s", ("message",), None
    )
    enregistrement.champ_libre = "jamais écrit"
    objet = json.loads(FormatJson().format(enregistrement))
    assert set(objet) == {"horodatage", "niveau", "evenement"}
    assert objet["evenement"] == "un message"
    enregistrement.champs = {"statut": 200}
    assert json.loads(FormatJson().format(enregistrement))["statut"] == 200
    try:
        raise RuntimeError("panne")
    except RuntimeError:
        enregistrement.exc_info = sys.exc_info()
    ligne = FormatJson().format(enregistrement)
    assert "\n" not in ligne
    assert "RuntimeError: panne" in json.loads(ligne)["trace"]


def test_la_configuration_du_deploiement_passe_tout_par_le_formateur():
    configuration = json.loads(CONFIGURATION.read_text(encoding="utf-8"))
    assert configuration["formatters"]["json"]["()"] == "app.journal.FormatJson"
    assert configuration["root"]["handlers"] == ["sortie"]
    assert configuration["loggers"]["uvicorn"]["handlers"] == ["sortie"]
    acces = configuration["loggers"]["uvicorn.access"]
    assert (acces["handlers"], acces["propagate"]) == ([], False)
    dockerfile = (MODULE / "Dockerfile").read_text(encoding="utf-8")
    assert '"--log-config=app/journalisation.json", "--no-access-log"' in dockerfile
