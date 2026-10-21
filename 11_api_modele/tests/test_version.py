"""La version de l'API : celle du `pyproject.toml`, majeure = segment de l'adresse."""

from __future__ import annotations

import tomllib

from conftest import RACINE_MODULE

import api_modele


def test_la_version_est_celle_du_pyproject():
    projet = tomllib.loads((RACINE_MODULE / "pyproject.toml").read_text("utf-8"))

    assert api_modele.__version__ == projet["project"]["version"]


def test_la_majeure_est_celle_de_v1():
    assert api_modele.__version__.split(".")[0] == "1"
