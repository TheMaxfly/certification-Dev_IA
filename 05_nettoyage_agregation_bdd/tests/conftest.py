"""Harnais partagé : une base PostgreSQL JETABLE, migrée depuis le dépôt.

Garde-fou central, repris du harnais de `database/` : le DSN est fabriqué ici à
partir du conteneur lancé par le harnais ; une DATABASE_URL présente dans
l'environnement est ignorée, et l'absence de Docker provoque un skip explicite.
Les tests ne se rabattent JAMAIS sur une base réelle, et `apimanga` n'est jamais
atteignable depuis la suite.

Les migrations sont jouées par le vrai runner (`database/migrate.py`) : la base
de test est celle du dépôt, pas une approximation écrite pour les tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
MIGRATIONS = RACINE.parents[0] / "database"
IMAGE_POSTGRES = "postgres:16-alpine"
DELAI_DEMARRAGE = 60

sys.path.insert(0, str(RACINE / "src"))


def docker_utilisable() -> bool:
    if subprocess.run(["which", "docker"], capture_output=True).returncode != 0:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.fixture(scope="session")
def conteneur() -> str:
    """Un PostgreSQL jetable pour toute la session."""
    if not docker_utilisable():
        pytest.skip(
            "Docker est indisponible : les tests de chargement ont besoin d'une "
            "base jetable. Ils ne se rabattront jamais sur une base réelle."
        )
    nom = f"referentiels-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            nom,
            "-e",
            "POSTGRES_PASSWORD=postgres",
            "-p",
            "0:5432",
            IMAGE_POSTGRES,
        ],
        capture_output=True,
        check=True,
    )
    try:
        liaison = json.loads(
            subprocess.run(
                ["docker", "inspect", nom], capture_output=True, text=True, check=True
            ).stdout
        )[0]["NetworkSettings"]["Ports"]["5432/tcp"][0]
        dsn = f"postgresql://postgres:postgres@127.0.0.1:{liaison['HostPort']}/postgres"

        import psycopg

        limite = time.monotonic() + DELAI_DEMARRAGE
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
def base(conteneur, monkeypatch) -> str:
    """Une base neuve par test, migrée par le runner du dépôt."""
    import psycopg

    nom = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(conteneur, autocommit=True) as connexion:
        connexion.execute(f'CREATE DATABASE "{nom}"')
    dsn = conteneur.rsplit("/", 1)[0] + f"/{nom}"
    monkeypatch.setitem(os.environ, "DATABASE_URL", dsn)

    sortie = subprocess.run(
        ["uv", "run", "python", "migrate.py", "up"],
        cwd=MIGRATIONS,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": dsn},
    )
    assert sortie.returncode == 0, f"migrations en échec : {sortie.stderr}"
    try:
        yield dsn
    finally:
        with psycopg.connect(conteneur, autocommit=True) as connexion:
            connexion.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')


def lire(dsn: str, sql: str, params=None):
    """Raccourci de lecture pour les assertions."""
    import psycopg

    with psycopg.connect(dsn) as connexion:
        return connexion.execute(sql, params).fetchall()
