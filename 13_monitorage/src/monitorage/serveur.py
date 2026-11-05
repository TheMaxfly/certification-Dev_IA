"""Le serveur de la sonde : `/metrics`, et le webhook d'essai des alertes.

`POST /webhook-essai` prouve qu'une alerte part de Grafana sans dépendre d'un service
extérieur : un compteur, et une ligne de journal avec l'heure et le titre, jamais le
corps.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from prometheus_client import CONTENT_TYPE_LATEST

from monitorage import journal
from monitorage.metriques import Metriques

#: Une alerte de Grafana tient largement là-dedans ; au-delà, refus.
CORPS_MAX_OCTETS = 1_000_000
TITRE_MAX = 200


def titre_de(corps: bytes) -> str | None:
    """Le titre d'une alerte, et rien d'autre du corps."""
    try:
        contenu = json.loads(corps)
    except ValueError:
        return None
    if not isinstance(contenu, dict):
        return None
    titre = cast(dict[str, object], contenu).get("title")
    return titre[:TITRE_MAX] if isinstance(titre, str) else None


def creer_serveur(hote: str, port: int, metriques: Metriques) -> ThreadingHTTPServer:
    class Gestionnaire(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/metrics":
                self.send_error(404)
                return
            contenu = metriques.exposer()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(contenu)))
            self.end_headers()
            self.wfile.write(contenu)

        def do_POST(self) -> None:
            if self.path != "/webhook-essai":
                self.send_error(404)
                return
            longueur = int(self.headers.get("Content-Length") or 0)
            if longueur > CORPS_MAX_OCTETS:
                self.send_error(413)
                return
            titre = titre_de(self.rfile.read(longueur))
            metriques.webhook_recus.inc()
            journal.evenement(
                "webhook_essai_recu",
                heure=datetime.now(UTC).isoformat(timespec="seconds"),
                titre=titre,
            )
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            """Aucune ligne par requête : Prometheus relève toutes les 15 s."""

    return ThreadingHTTPServer((hote, port), Gestionnaire)
