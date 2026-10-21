"""Le lancement : configuration validée d'abord ; fautive, rien n'est lancé."""

from __future__ import annotations

import pytest
from conftest import CLE_APPLICATION

from api_modele import __main__ as principal
from api_modele import journal


@pytest.fixture(autouse=True)
def journal_rendu():
    """`principal` configure le journal : il est rendu tel quel après le test."""
    avant = (list(journal.JOURNAL.handlers), journal.JOURNAL.propagate)
    yield
    journal.JOURNAL.handlers[:] = avant[0]
    journal.JOURNAL.propagate = avant[1]


def test_une_configuration_fautive_ne_lance_rien(monkeypatch, capsys):
    lances: list[object] = []
    monkeypatch.delenv("API_MODELE_CLES", raising=False)
    monkeypatch.delenv("API_MODELE_DSN", raising=False)
    monkeypatch.setattr(principal.uvicorn, "run", lambda *a, **k: lances.append(a))

    assert principal.principal() == 2
    assert lances == []
    assert "configuration_invalide" in capsys.readouterr().err


def test_une_configuration_valide_lance_le_serveur_sur_le_bouclage(monkeypatch):
    lances: list[dict[str, object]] = []
    monkeypatch.setenv("API_MODELE_CLES", f"application:{CLE_APPLICATION}")
    monkeypatch.setenv("API_MODELE_DSN", "postgresql://modele_api@localhost/apimanga")
    monkeypatch.setattr(principal.uvicorn, "run", lambda app, **k: lances.append(k))

    assert principal.principal() == 0
    assert lances == [
        {"host": "127.0.0.1", "port": 8010, "access_log": False, "log_level": "warning"}
    ]
