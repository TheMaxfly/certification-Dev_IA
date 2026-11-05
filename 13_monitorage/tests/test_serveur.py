"""Le serveur de la sonde : `/metrics`, et rien d'autre en `GET`."""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from monitorage.metriques import Metriques
from monitorage.serveur import creer_serveur


@pytest.fixture
def adresse() -> Iterator[str]:
    serveur = creer_serveur("127.0.0.1", 0, Metriques(lecture_base=False))
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    yield f"http://127.0.0.1:{serveur.server_port}"
    serveur.shutdown()
    serveur.server_close()
    fil.join(timeout=5)


def test_metrics_repond_au_format_prometheus(adresse: str):
    with urllib.request.urlopen(f"{adresse}/metrics") as reponse:
        assert reponse.status == 200
        assert reponse.headers["Content-Type"].startswith("text/plain")
        texte = reponse.read().decode("utf-8")
    assert "modele_exporteur_base_activee 0.0" in texte


def test_une_autre_route_rend_404(adresse: str):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{adresse}/ailleurs")
    assert exc.value.code == 404
