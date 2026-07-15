"""Harnais de test : une base PostgreSQL JETABLE, dans un conteneur.

Garde-fou central : les tests ne doivent JAMAIS pointer sur `apimanga`. Le DSN
est donc toujours fabriqué ici, à partir du conteneur lancé par le harnais ;
une DATABASE_URL présente dans l'environnement est ignorée (et l'absence de
Docker provoque un skip explicite, jamais un repli sur une base réelle).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

# Image déjà présente en local : `postgres:16` déclencherait un pull réseau.
IMAGE_POSTGRES = "postgres:16-alpine"
MOT_DE_PASSE = "postgres"
DELAI_DEMARRAGE = 60

RACINE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("migrate", RACINE / "migrate.py")
migrate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
# Enregistré avant exec_module : @dataclass résout ses annotations via
# sys.modules[cls.__module__], absent sinon.
sys.modules["migrate"] = migrate
SPEC.loader.exec_module(migrate)


def docker_utilisable() -> bool:
    if shutil.which("docker") is None:
        return False
    return (
        subprocess.run(["docker", "info"], capture_output=True, check=False).returncode
        == 0
    )


def image_presente() -> bool:
    resultat = subprocess.run(
        ["docker", "image", "inspect", IMAGE_POSTGRES],
        capture_output=True,
        check=False,
    )
    return resultat.returncode == 0


@pytest.fixture(scope="session")
def conteneur_postgres() -> str:
    """Lance un PostgreSQL jetable et rend le DSN de la base d'administration."""
    if not docker_utilisable():
        pytest.skip(
            "Docker est indisponible : les tests de migration ont besoin d'une "
            "base jetable. Ils ne se rabattront jamais sur une base réelle."
        )
    if not image_presente():
        pytest.skip(
            f"Image {IMAGE_POSTGRES} absente en local. La récupérer avec "
            f"`docker pull {IMAGE_POSTGRES}` (accès réseau requis)."
        )

    nom = f"migrations-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            nom,
            "-e",
            f"POSTGRES_PASSWORD={MOT_DE_PASSE}",
            "-p",
            "0:5432",
            IMAGE_POSTGRES,
        ],
        capture_output=True,
        check=True,
    )
    try:
        brut = subprocess.run(
            ["docker", "inspect", nom],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        liaison = json.loads(brut)[0]["NetworkSettings"]["Ports"]["5432/tcp"][0]
        port = liaison["HostPort"]
        dsn = f"postgresql://postgres:{MOT_DE_PASSE}@127.0.0.1:{port}/postgres"

        limite = time.monotonic() + DELAI_DEMARRAGE
        while True:
            pret = subprocess.run(
                ["docker", "exec", nom, "pg_isready", "-U", "postgres"],
                capture_output=True,
                check=False,
            )
            if pret.returncode == 0:
                break
            if time.monotonic() > limite:
                raise RuntimeError("PostgreSQL n'a pas démarré à temps")
            time.sleep(0.3)

        # pg_isready répond avant que les connexions soient réellement servies.
        import psycopg

        while True:
            try:
                with psycopg.connect(dsn, connect_timeout=2):
                    break
            except psycopg.OperationalError:
                if time.monotonic() > limite:
                    raise
                time.sleep(0.3)

        yield dsn
    finally:
        subprocess.run(["docker", "rm", "-f", nom], capture_output=True, check=False)


@pytest.fixture
def base(conteneur_postgres, monkeypatch) -> str:
    """Une base neuve par test, et DATABASE_URL pointée dessus."""
    import psycopg

    nom = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(conteneur_postgres, autocommit=True) as connexion:
        connexion.execute(f'CREATE DATABASE "{nom}"')

    dsn = conteneur_postgres.rsplit("/", 1)[0] + f"/{nom}"
    monkeypatch.setitem(os.environ, "DATABASE_URL", dsn)
    try:
        yield dsn
    finally:
        with psycopg.connect(conteneur_postgres, autocommit=True) as connexion:
            connexion.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
