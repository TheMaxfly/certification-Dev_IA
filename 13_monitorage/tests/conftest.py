"""Harnais du module 13 : les chemins, et une doublure du service d'embedding.

Aucun service réel, aucune base réelle : la doublure répond sur le bouclage, et le code
du module 09 tourne tel quel contre elle.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from monitorage import service
from monitorage.configuration import Configuration, charger

MODULE = Path(__file__).resolve().parents[1]
RACINE = MODULE.parent
DIMENSION = 768
INFO = {
    "model_id": "modele-factice",
    "model_sha": "revision-factice",
    "model_dtype": "float32",
    "version": "1.9.4",
}


def vecteur(cosinus: float = 1.0) -> list[float]:
    """Un vecteur unitaire, de ce cosinus avec le vecteur de référence."""
    return [cosinus, (1 - cosinus * cosinus) ** 0.5] + [0.0] * (DIMENSION - 2)


@dataclass
class DoublureService:
    """Le service d'embedding : `/info`, `/health`, `/embed`, un vecteur par texte."""

    port: int = 0
    cosinus: dict[str, float] = field(default_factory=dict[str, float])
    appels: list[list[str]] = field(default_factory=list[list[str]])
    panne: bool = False
    prefixe: str = ""

    def repondre(self, textes: list[str]) -> list[list[float]]:
        self.appels.append(textes)
        rendus: list[list[float]] = []
        for texte in textes:
            nu = texte.removeprefix(self.prefixe)
            rendus.append(vecteur(self.cosinus.get(nu, 1.0)))
        return rendus


def creer_doublure(doublure: DoublureService) -> ThreadingHTTPServer:
    class Gestionnaire(BaseHTTPRequestHandler):
        def _json(self, corps: object, statut: int = 200) -> None:
            contenu = json.dumps(corps).encode("utf-8")
            self.send_response(statut)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(contenu)))
            self.end_headers()
            self.wfile.write(contenu)

        def do_GET(self) -> None:
            if doublure.panne:
                self._json({"erreur": "panne"}, 503)
            elif self.path == "/info":
                self._json(INFO)
            else:
                self._json({})

        def do_POST(self) -> None:
            longueur = int(self.headers.get("Content-Length") or 0)
            corps = json.loads(self.rfile.read(longueur))
            if doublure.panne:
                self._json({"erreur": "panne"}, 503)
            else:
                self._json(doublure.repondre(corps["inputs"]))

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer(("127.0.0.1", 0), Gestionnaire)


@pytest.fixture
def doublure(monkeypatch: pytest.MonkeyPatch) -> Iterator[DoublureService]:
    """La doublure, et le module 09 qui la vise : son port remplace l'instance."""
    conf = service.module("configuration")
    vraie = conf.charger
    instance = vraie(service.INSTANCE)
    etat = DoublureService(prefixe=instance.prefixe("document"))
    serveur = creer_doublure(etat)
    etat.port = serveur.server_port
    monkeypatch.setattr(
        conf, "charger", lambda nom: replace(vraie(nom), port=serveur.server_port)
    )
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    yield etat
    serveur.shutdown()
    serveur.server_close()
    fil.join(timeout=5)


def echantillon(prefixe: str, fragments: int = 4) -> dict[str, Any]:
    """Un échantillon exporté : deux tranches, des fragments inventés."""
    return {
        "encodage": {
            "encodage_id": 3,
            "corpus_id": "v2",
            "modele": INFO["model_id"],
            "revision": INFO["model_sha"],
            "precision_calcul": INFO["model_dtype"],
            "outil_version": INFO["version"],
            "prefixe_document": prefixe,
        },
        "echantillon": [
            {
                "chunk_id": 100 + i,
                "tranche": i % 2,
                "longueur": 50,
                "texte": f"texte secret {i}",
                "vecteur": vecteur(),
            }
            for i in range(fragments)
        ],
    }


@pytest.fixture
def configuration(doublure: DoublureService, tmp_path: Path) -> Configuration:
    """Une configuration vers la doublure : échantillon et journal temporaires."""
    chemin = tmp_path / "encodage_3.json"
    chemin.write_text(json.dumps(echantillon(doublure.prefixe)), encoding="utf-8")
    return charger(
        {
            "SONDE_HOTE_SERVICE": "127.0.0.1",
            "SONDE_ECHANTILLON": str(chemin),
            "SONDE_JOURNAL": str(tmp_path / "journal.jsonl"),
        }
    )
