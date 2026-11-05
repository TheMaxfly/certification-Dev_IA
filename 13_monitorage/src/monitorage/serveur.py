"""Le serveur de la sonde : `/metrics`, sur le réseau de collecte seulement."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST

from monitorage.metriques import Metriques


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

        def log_message(self, format: str, *args: object) -> None:
            """Aucune ligne par requête : Prometheus relève toutes les 15 s."""

    return ThreadingHTTPServer((hote, port), Gestionnaire)
