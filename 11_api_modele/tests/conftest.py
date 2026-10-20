"""Harnais : une base PostgreSQL + pgvector JETABLE, migrée par le runner du dépôt.

Le harnais partagé (`database/harnais_tests/`) est chargé par son chemin, comme dans
les modules 05, 08, 09 et 10. Le DSN est fabriqué par lui, à partir du conteneur
qu'il lance ; une variable d'environnement n'est jamais lue, et l'absence de Docker
fait sauter les tests à base — jamais de repli sur `apimanga`.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

RACINE_MODULE = Path(__file__).resolve().parents[1]
RACINE_DEPOT = RACINE_MODULE.parent


def _charger_harnais():
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
    """Un PostgreSQL jetable pour toute la session."""
    raison = harnais.raison_de_sauter()
    if raison:
        pytest.skip(raison)
    with harnais.conteneur("api-modele-test") as dsn:
        yield dsn


@pytest.fixture(scope="session")
def gabarit(conteneur: str) -> str:
    """La base gabarit, migrée une fois par session par le runner du dépôt."""
    return harnais.creer_gabarit(conteneur)


@pytest.fixture
def base(conteneur: str, gabarit: str) -> Iterator[str]:
    """Une base neuve par test, copiée du gabarit migré."""
    with harnais.base_depuis_gabarit(conteneur, gabarit) as dsn:
        yield dsn
