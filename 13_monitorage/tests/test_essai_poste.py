"""L'essai réel, sur le poste seulement : la pile levée, cinq cibles `up`.

    MONITORAGE_ESSAI_POSTE=1 uv run --extra dev pytest tests/test_essai_poste.py

Lance `make monitorage`, attend les cibles, vérifie que `/metrics` de l'API refuse un
conteneur hors du réseau de collecte et répond à un conteneur de ce réseau, puis lance
`make monitorage-arret`. Aucun appel à un fournisseur : aucune question n'est posée, et
le Compose coupe le contrôle d'exploitation au démarrage de l'API. Sauté ailleurs,
compté au plafond de `sauts_attendus.tsv`.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request

import pytest
from conftest import RACINE

JOBS = {"embedding", "api", "application", "machine", "qualite"}
LIRE_METRICS = (
    "import urllib.error, urllib.request\n"
    "try:\n"
    "    print(urllib.request.urlopen('http://api:8010/metrics', timeout=5).status)\n"
    "except urllib.error.HTTPError as e:\n"
    "    print(e.code)\n"
)

poste = pytest.mark.skipif(
    os.environ.get("MONITORAGE_ESSAI_POSTE") != "1",
    reason="essai du Compose réel : poste seulement (MONITORAGE_ESSAI_POSTE=1)",
)


def faire(cible: str) -> None:
    subprocess.run(["make", cible], cwd=RACINE, check=True, capture_output=True)


def etat_des_cibles() -> dict[str, str]:
    with urllib.request.urlopen(
        "http://127.0.0.1:9090/api/v1/targets", timeout=5
    ) as reponse:
        cibles = json.load(reponse)["data"]["activeTargets"]
    return {c["labels"]["job"]: c["health"] for c in cibles}


def statut_metrics_depuis(reseau: str) -> str:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            reseau,
            "--entrypoint",
            "python",
            "monitorage-sonde:0.1.0",
            "-c",
            LIRE_METRICS,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def attendre_les_cibles() -> dict[str, str]:
    limite = time.monotonic() + 300
    etat: dict[str, str] = {}
    while time.monotonic() < limite:
        try:
            etat = etat_des_cibles()
        except OSError:
            etat = {}
        if set(etat) == JOBS and set(etat.values()) == {"up"}:
            break
        time.sleep(5)
    return etat


@poste
def test_la_pile_reelle_cinq_cibles_et_la_regle_de_reseau():
    faire("monitorage")
    try:
        assert attendre_les_cibles() == dict.fromkeys(JOBS, "up")
        assert statut_metrics_depuis("api-modele_interne") == "404"
        assert statut_metrics_depuis("api-modele_collecte") == "200"
    finally:
        faire("monitorage-arret")
