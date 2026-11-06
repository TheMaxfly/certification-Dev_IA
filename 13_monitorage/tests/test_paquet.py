"""Le paquet se charge et porte la version de son `pyproject.toml`."""

from __future__ import annotations

import tomllib
from pathlib import Path

import monitorage

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_la_version_est_celle_du_projet():
    projet = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert monitorage.__version__ == projet["version"]
