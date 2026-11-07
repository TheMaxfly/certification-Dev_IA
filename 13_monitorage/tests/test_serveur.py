"""Le serveur de la sonde : `/metrics`, et le webhook d'essai des alertes."""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from monitorage.metriques import Metriques
from monitorage.serveur import CORPS_MAX_OCTETS, creer_serveur, titre_de


@pytest.fixture
def metriques() -> Metriques:
    return Metriques(lecture_base=False)


@pytest.fixture
def adresse(metriques: Metriques) -> Iterator[str]:
    serveur = creer_serveur("127.0.0.1", 0, metriques)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    yield f"http://127.0.0.1:{serveur.server_port}"
    serveur.shutdown()
    serveur.server_close()
    fil.join(timeout=5)


def poster(adresse: str, chemin: str, corps: bytes) -> int:
    requete = urllib.request.Request(f"{adresse}{chemin}", data=corps, method="POST")
    with urllib.request.urlopen(requete) as reponse:
        return reponse.status


def test_metrics_repond_au_format_prometheus(adresse: str):
    with urllib.request.urlopen(f"{adresse}/metrics") as reponse:
        assert reponse.status == 200
        assert reponse.headers["Content-Type"].startswith("text/plain")
        texte = reponse.read().decode("utf-8")
    assert "modele_exporteur_base_activee 0.0" in texte
    assert "monitorage_webhook_essai_recus_total 0.0" in texte


def test_une_autre_route_rend_404(adresse: str):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{adresse}/ailleurs")
    assert exc.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as exc:
        poster(adresse, "/ailleurs", b"{}")
    assert exc.value.code == 404


def test_le_webhook_compte_et_ne_journalise_que_le_titre(
    adresse: str, metriques: Metriques, capsys: Any
):
    corps = json.dumps(
        {"title": "[FIRING:1] A — service ou API indisponible", "message": "le corps"}
    ).encode("utf-8")

    assert poster(adresse, "/webhook-essai", corps) == 200

    texte = metriques.exposer().decode()
    assert "monitorage_webhook_essai_recus_total 1.0" in texte
    sortie = capsys.readouterr().err
    assert "A — service ou API indisponible" in sortie
    assert "webhook_essai_recu" in sortie
    assert "le corps" not in sortie


def test_le_webhook_refuse_un_corps_trop_long(adresse: str, metriques: Metriques):
    """Le refus se décide sur la longueur annoncée, avant toute lecture du corps : le
    client n'envoie que l'en-tête, et le test ne dépend d'aucune course. (Envoyer le
    million d'octets en faisait une : le serveur répond 413 et ferme pendant que le
    client écrit encore ; une connexion coupée a été relevée une fois, le 7
    novembre.)"""
    morceaux = urllib.parse.urlsplit(adresse)
    assert morceaux.hostname is not None and morceaux.port is not None
    connexion = http.client.HTTPConnection(morceaux.hostname, morceaux.port, timeout=5)
    try:
        connexion.putrequest("POST", "/webhook-essai")
        connexion.putheader("Content-Length", str(CORPS_MAX_OCTETS + 1))
        connexion.endheaders()
        assert connexion.getresponse().status == 413
    finally:
        connexion.close()
    time.sleep(0.2)  # un serveur qui lirait quand même le corps compterait ici
    assert "monitorage_webhook_essai_recus_total 0.0" in metriques.exposer().decode()


def test_un_corps_trop_long_vraiment_envoye_est_refuse(
    adresse: str, metriques: Metriques
):
    """Un client qui envoie vraiment le corps : le serveur ne le lit pas. Le client
    reçoit le 413, ou une connexion coupée s'il écrit encore quand le serveur ferme :
    les deux sont un refus, et rien n'est compté."""
    requete = urllib.request.Request(
        f"{adresse}/webhook-essai",
        data=b"x" * (CORPS_MAX_OCTETS + 1),
        method="POST",
    )
    with pytest.raises((urllib.error.URLError, ConnectionError)) as exc:
        urllib.request.urlopen(requete, timeout=5)
    if isinstance(exc.value, urllib.error.HTTPError):
        assert exc.value.code == 413
    elif isinstance(exc.value, urllib.error.URLError):
        assert isinstance(exc.value.reason, ConnectionError), exc.value.reason
    time.sleep(0.2)
    assert "monitorage_webhook_essai_recus_total 0.0" in metriques.exposer().decode()


def test_un_corps_de_la_taille_maximale_est_accepte(adresse: str, metriques: Metriques):
    assert poster(adresse, "/webhook-essai", b"x" * CORPS_MAX_OCTETS) == 200
    assert "monitorage_webhook_essai_recus_total 1.0" in metriques.exposer().decode()


@pytest.mark.parametrize(
    ("corps", "attendu"),
    [
        (b'{"title": "Alerte"}', "Alerte"),
        (b'{"title": 3}', None),
        (b'["liste"]', None),
        (b"pas du json", None),
        (('{"title": "' + "x" * 500 + '"}').encode(), "x" * 200),
    ],
)
def test_le_titre_seul_est_lu(corps: bytes, attendu: str | None):
    assert titre_de(corps) == attendu
