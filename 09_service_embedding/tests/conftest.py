"""Harnais : une base PostgreSQL + pgvector JETABLE, migrée par le runner du dépôt.

Garde-fou repris de `database/` et du module 05 : le DSN est fabriqué ici, à
partir du conteneur lancé par le harnais ; une DATABASE_URL de l'environnement
est ignorée, et l'absence de Docker fait sauter les tests qui ont besoin d'une
base — jamais de repli sur `apimanga`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

RACINE_DEPOT = Path(__file__).resolve().parents[2]
MIGRATIONS = RACINE_DEPOT / "database"
# Celle de database/tests : PostgreSQL 16 + pgvector 0.6.0, par digest.
IMAGE_POSTGRES = (
    "pgvector/pgvector:0.6.0-pg16"
    "@sha256:b740286128ce8e232fe0de3c8db2267d91aedc598dfbeaefb7ffb0b79ceef1b3"
)
DELAI_DEMARRAGE = 60


def docker_utilisable() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.fixture(scope="session")
def conteneur() -> str:
    if not docker_utilisable():
        pytest.skip("Docker indisponible : pas de base jetable, pas de repli réel.")
    nom = f"encodage-test-{uuid.uuid4().hex[:8]}"
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
def base(conteneur) -> str:
    """Une base neuve par test, migrée (000 → dernière), avec trois fragments."""
    import psycopg

    nom = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(conteneur, autocommit=True) as cx:
        cx.execute(f'CREATE DATABASE "{nom}"')
    dsn = conteneur.rsplit("/", 1)[0] + f"/{nom}"
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    sortie = subprocess.run(
        ["uv", "run", "python", "migrate.py", "up"],
        cwd=MIGRATIONS,
        capture_output=True,
        text=True,
        env=env | {"DATABASE_URL": dsn},
    )
    assert sortie.returncode == 0, f"migrations en échec : {sortie.stderr}"
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.corpus_docs (doc_key, source, doc_text) VALUES"
            " ('ms:1', 'ms_review', 'x'), ('kitsu:2', 'kitsu_synopsis', 'y')"
        )
        cx.execute(
            "INSERT INTO bench.corpus_chunks (chunk_id, doc_key, chunk_index,"
            " chunk_text) VALUES"
            " (101, 'ms:1', 0, 'Une critique enthousiaste du premier tome.'),"
            " (102, 'ms:1', 1, 'Le dessin reste inégal, mais le rythme tient.'),"
            " (103, 'kitsu:2', 0, 'A young pirate sets sail to find a treasure.')"
        )
    try:
        yield dsn
    finally:
        with psycopg.connect(conteneur, autocommit=True) as cx:
            cx.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
