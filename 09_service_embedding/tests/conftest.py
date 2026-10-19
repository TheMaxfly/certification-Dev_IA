"""Harnais : une base PostgreSQL + pgvector JETABLE, migrée par le runner du dépôt.

Garde-fou repris de `database/` et du module 05 : le DSN est fabriqué ici, à
partir du conteneur lancé par le harnais ; une DATABASE_URL de l'environnement
est ignorée, et l'absence de Docker fait sauter les tests qui ont besoin d'une
base — jamais de repli sur `apimanga`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RACINE_DEPOT = Path(__file__).resolve().parents[2]
MIGRATIONS = RACINE_DEPOT / "database"


def _charger_harnais():
    """Le harnais partagé des bases jetables (`database/harnais_tests/`), par chemin."""
    spec = importlib.util.spec_from_file_location(
        "harnais_tests", MIGRATIONS / "harnais_tests" / "__init__.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["harnais_tests"] = module
    spec.loader.exec_module(module)
    return module


harnais = _charger_harnais()


@pytest.fixture(scope="session")
def conteneur() -> str:
    """Un PostgreSQL jetable pour toute la session."""
    raison = harnais.raison_de_sauter()
    if raison:
        pytest.skip(raison)
    with harnais.conteneur("encodage-test") as dsn:
        yield dsn


@pytest.fixture(scope="session")
def gabarit(conteneur) -> str:
    """La base gabarit, migrée une fois par session par le runner du dépôt."""
    return harnais.creer_gabarit(conteneur)


@pytest.fixture
def base(conteneur, gabarit) -> str:
    """Une base neuve par test, copiée du gabarit migré, avec trois fragments."""
    import psycopg

    with harnais.base_depuis_gabarit(conteneur, gabarit) as dsn:
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
        yield dsn
