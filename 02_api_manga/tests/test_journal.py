"""Le journal du catalogue en JSON (E5, lot 1, spec 04 — E5-12), prouvé sur des lignes
réelles : uvicorn lancé comme le déploiement le lance (`--log-config
app/journalisation.json`), sur un port du bouclage, la base injoignable ; trois
requêtes ; chaque ligne écrite est un objet JSON de la forme des deux autres services.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from app.journal import CONFIGURATION, FormatJson

MODULE = Path(__file__).resolve().parents[1]
CLES = {"horodatage", "niveau", "evenement", "trace"}
#: Une clé jetable : elle n'ouvre rien, le serveur de l'essai n'a pas de base.
CLE_ESSAI = "cle-jetable-du-journal-0123456789abcdef"


def port_libre() -> int:
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        return int(prise.getsockname()[1])


def lire(url: str, cle: str | None = None) -> int:
    requete = Request(url, headers={"X-API-Key": cle} if cle else {})
    try:
        with urlopen(requete, timeout=10) as reponse:  # le bouclage seul
            return int(reponse.status)
    except HTTPError as erreur:
        return int(erreur.code)


@pytest.fixture(scope="module")
def lignes() -> list[str]:
    """Le journal d'un vrai uvicorn, base injoignable, après trois requêtes."""
    port, base = port_libre(), port_libre()
    environnement = {
        "PATH": os.environ.get("PATH", ""),
        "API_KEYS": f"essai:{CLE_ESSAI}",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": str(base),
        "DB_CONNECT_TIMEOUT": "1",
        "DB_POOL_TIMEOUT": "1",
    }
    commande = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host=127.0.0.1",
        f"--port={port}",
        "--log-config=app/journalisation.json",
    ]
    serveur = subprocess.Popen(  # une commande fixe, sans shell
        commande,
        cwd=MODULE,
        env=environnement,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    adresse = f"http://127.0.0.1:{port}"
    try:
        for _ in range(300):
            try:
                if lire(f"{adresse}/live") == 200:
                    break
            except URLError:
                pass
            time.sleep(0.05)
        else:
            pytest.fail("uvicorn n'a pas démarré")
        assert lire(f"{adresse}/health") == 503
        assert lire(f"{adresse}/series?q=essai", CLE_ESSAI) == 503
    finally:
        serveur.terminate()
        sortie, _ = serveur.communicate(timeout=15)
    return [ligne for ligne in sortie.splitlines() if ligne.strip()]


def test_chaque_ligne_est_un_objet_json_de_la_forme_commune(lignes: list[str]):
    assert len(lignes) >= 5
    for ligne in lignes:
        objet = json.loads(ligne)
        assert set(objet) <= CLES, ligne
        assert {"horodatage", "niveau", "evenement"} <= set(objet), ligne
        assert objet["horodatage"].endswith("+00:00")


def evenements(lignes: list[str]) -> list[dict[str, str]]:
    return [json.loads(ligne) for ligne in lignes]


def test_le_journal_d_acces_d_uvicorn_est_au_meme_format(lignes: list[str]):
    acces = [e["evenement"] for e in evenements(lignes)]
    assert any('"GET /live HTTP/1.1" 200' in a for a in acces)
    assert any('"GET /health HTTP/1.1" 503' in a for a in acces)
    assert any(e.startswith("Uvicorn running on") for e in acces)


def test_le_journal_applicatif_et_ses_traces_tiennent_sur_une_ligne(
    lignes: list[str],
):
    tous = evenements(lignes)
    (autorise,) = [
        e for e in tous if e["evenement"].startswith("appel autorisé sur /series")
    ]
    assert autorise["niveau"] == "INFO"
    assert "consommateur « essai »" in autorise["evenement"]
    sante = [e for e in tous if e["evenement"] == "PostgreSQL health check failed"]
    assert sante and sante[0]["niveau"] == "WARNING"
    assert "Traceback" in sante[0]["trace"]


def test_aucune_cle_ne_passe_au_journal(lignes: list[str]):
    assert not any(CLE_ESSAI in ligne for ligne in lignes)


def test_le_formateur_ne_rend_que_les_champs_communs():
    enregistrement = logging.LogRecord(
        "app.main", logging.WARNING, __file__, 1, "un %s", ("message",), None
    )
    enregistrement.champ_libre = "jamais écrit"
    objet = json.loads(FormatJson().format(enregistrement))
    assert set(objet) == {"horodatage", "niveau", "evenement"}
    assert objet["evenement"] == "un message"
    try:
        raise RuntimeError("panne")
    except RuntimeError:
        enregistrement.exc_info = sys.exc_info()
    ligne = FormatJson().format(enregistrement)
    assert "\n" not in ligne
    assert "RuntimeError: panne" in json.loads(ligne)["trace"]


def test_la_configuration_du_deploiement_passe_tout_par_le_formateur():
    configuration = json.loads(CONFIGURATION.read_text(encoding="utf-8"))
    assert configuration["formatters"]["json"]["()"] == "app.journal.FormatJson"
    assert configuration["root"]["handlers"] == ["sortie"]
    for nom in ("uvicorn", "uvicorn.access"):
        assert configuration["loggers"][nom]["handlers"] == ["sortie"], nom
        assert configuration["loggers"][nom]["propagate"] is False, nom
    dockerfile = (MODULE / "Dockerfile").read_text(encoding="utf-8")
    assert '"--log-config=app/journalisation.json"' in dockerfile
