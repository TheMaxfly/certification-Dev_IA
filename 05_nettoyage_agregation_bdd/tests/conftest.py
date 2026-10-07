"""Harnais partagé : une base PostgreSQL JETABLE, migrée depuis le dépôt.

Garde-fou central, repris du harnais de `database/` : le DSN est fabriqué ici à
partir du conteneur lancé par le harnais ; une DATABASE_URL présente dans
l'environnement est ignorée, et l'absence de Docker provoque un skip explicite.
Les tests ne se rabattent JAMAIS sur une base réelle, et `apimanga` n'est jamais
atteignable par ce harnais. Une seule exception, explicite et hors harnais : le
contrôle permanent des têtes du catalogue (`test_controle_tetes.py`), qui ne
tourne que si `APIMANGA_DSN` est défini, et en lecture seule.

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
# Celle du harnais de `database/` : PostgreSQL 16 + pgvector 0.6.0, par digest
# (la migration 020 crée l'extension `vector`).
IMAGE_POSTGRES = (
    "pgvector/pgvector:0.6.0-pg16"
    "@sha256:b740286128ce8e232fe0de3c8db2267d91aedc598dfbeaefb7ffb0b79ceef1b3"
)
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


def ecrire_run_kitsu(dossier, oeuvres, staff=None):
    """Un run Kitsu miniature : `manga.ndjson`, `relations/staff.ndjson`, et le
    `manifest.json` qui déclare leurs sha256 — la forme exacte du run réel.

    oeuvres : (kitsu_id, sous_type, canonique, titres, synopsis, genres, catégories)
    staff   : {kitsu_id: [(nom, rôle Kitsu), …]}
    """
    import hashlib

    (dossier / "relations").mkdir(parents=True, exist_ok=True)
    lignes = []
    for kid, sous_type, canonique, titres, synopsis, genres, categories in oeuvres:
        inclus = [{"type": "genres", "attributes": {"name": g}} for g in genres] + [
            {"type": "categories", "attributes": {"title": c}} for c in categories
        ]
        lignes.append(
            json.dumps(
                {
                    "data": {
                        "id": str(kid),
                        "type": "manga",
                        "attributes": {
                            "subtype": sous_type,
                            "canonicalTitle": canonique,
                            "titles": titres,
                            "synopsis": synopsis,
                            "popularityRank": kid * 10,
                            "ratingRank": kid * 20,
                        },
                    },
                    "included": inclus,
                }
            )
        )
    (dossier / "manga.ndjson").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    staff_lignes = []
    for kid, personnes in (staff or {}).items():
        staff_lignes.append(
            json.dumps(
                {
                    "manga_id": str(kid),
                    "data": [
                        {
                            "type": "mediaStaff",
                            "attributes": {"role": role},
                            "relationships": {
                                "person": {"data": {"type": "people", "id": f"p{i}"}}
                            },
                        }
                        for i, (_, role) in enumerate(personnes)
                    ],
                    "included": [
                        {"type": "people", "id": f"p{i}", "attributes": {"name": nom}}
                        for i, (nom, _) in enumerate(personnes)
                    ],
                }
            )
        )
    (dossier / "relations/staff.ndjson").write_text(
        "".join(ligne + "\n" for ligne in staff_lignes), encoding="utf-8"
    )
    fichiers = [
        {
            "path": nom,
            "sha256": hashlib.sha256((dossier / nom).read_bytes()).hexdigest(),
        }
        for nom in ("manga.ndjson", "relations/staff.ndjson")
    ]
    (dossier / "manifest.json").write_text(
        json.dumps({"files": fichiers}), encoding="utf-8"
    )
    return dossier
