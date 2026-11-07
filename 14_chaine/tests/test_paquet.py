"""Le paquet se charge et porte la version de son `pyproject.toml`."""

from __future__ import annotations

import tomllib

from conftest import MODULE

import chaine


def test_la_version_est_celle_du_projet():
    projet = tomllib.loads((MODULE / "pyproject.toml").read_text(encoding="utf-8"))
    assert chaine.__version__ == projet["project"]["version"]


def test_aucune_dependance_a_l_execution():
    projet = tomllib.loads((MODULE / "pyproject.toml").read_text(encoding="utf-8"))
    assert projet["project"]["dependencies"] == [], "bibliothèque standard seule"
