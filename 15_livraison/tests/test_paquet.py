"""Le paquet : sa version, sa seule dépendance."""

from __future__ import annotations

import tomllib

from conftest import MODULE

import livraison


def test_la_version_est_celle_du_projet():
    projet = tomllib.loads((MODULE / "pyproject.toml").read_text(encoding="utf-8"))
    assert livraison.__version__ == projet["project"]["version"] == "0.1.1"


def test_httpx_seule_dependance():
    projet = tomllib.loads((MODULE / "pyproject.toml").read_text(encoding="utf-8"))
    assert projet["project"]["dependencies"] == ["httpx==0.28.1"]


def test_le_journal_et_son_index_sont_versionnes():
    index = (MODULE / "journal" / "index.md").read_text(encoding="utf-8")
    from livraison.journal import EN_TETE_INDEX

    assert index.startswith(EN_TETE_INDEX)
