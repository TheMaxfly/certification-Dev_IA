"""Harnais de test : une base PostgreSQL JETABLE, dans un conteneur.

Garde-fou central : les tests ne doivent JAMAIS pointer sur `apimanga`. Le DSN
est donc toujours fabriqué par le harnais partagé (`database/harnais_tests/`), à
partir du conteneur qu'il lance ; une DATABASE_URL présente dans l'environnement
est ignorée (et l'absence de Docker provoque un skip explicite, jamais un repli
sur une base réelle).

Deux bases par test, selon le besoin :
  - `base_migree` : une copie de la base gabarit, migrée une fois par session ;
  - `base` : une base vide, pour les tests qui jouent les migrations eux-mêmes
    (pas à pas, cible intermédiaire, rejeu).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]


def _charger_harnais():
    spec = importlib.util.spec_from_file_location(
        "harnais_tests", RACINE / "harnais_tests" / "__init__.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["harnais_tests"] = module
    spec.loader.exec_module(module)
    return module


harnais = _charger_harnais()
migrate = harnais.charger_migrate()


@pytest.fixture(scope="session")
def conteneur_postgres() -> str:
    """Lance un PostgreSQL jetable et rend le DSN de la base d'administration."""
    raison = harnais.raison_de_sauter()
    if raison:
        pytest.skip(raison)
    with harnais.conteneur("migrations-test") as dsn:
        yield dsn


@pytest.fixture(scope="session")
def gabarit(conteneur_postgres) -> str:
    """La base gabarit : migrée une fois par session, copiée pour chaque test."""
    return harnais.creer_gabarit(conteneur_postgres)


@pytest.fixture
def base(conteneur_postgres, monkeypatch) -> str:
    """Une base neuve et vide par test, et DATABASE_URL pointée dessus."""
    with harnais.base_vide(conteneur_postgres) as dsn:
        monkeypatch.setitem(os.environ, "DATABASE_URL", dsn)
        yield dsn


@pytest.fixture
def base_migree(conteneur_postgres, gabarit, monkeypatch) -> str:
    """Une base neuve par test, copiée du gabarit migré ; DATABASE_URL y pointe."""
    with harnais.base_depuis_gabarit(conteneur_postgres, gabarit) as dsn:
        monkeypatch.setitem(os.environ, "DATABASE_URL", dsn)
        yield dsn
