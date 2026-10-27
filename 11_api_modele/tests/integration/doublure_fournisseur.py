"""Doublure d'un fournisseur du modèle de langage, pour l'essai d'intégration du
Compose.

Aucun fournisseur réel n'est joint par l'essai : ce serveur répond à la place, sur le
bouclage du conteneur de l'API (il partage son espace réseau : `network_mode:
"service:api"`), à l'adresse du profil d'essai (`profil_essai.toml`). Il rend une
sortie conforme au schéma de la route — la question autonome pour une reformulation,
une réponse qui cite la série 1 et son fragment 702 du jeu d'essai sinon — et un
`usage`. `/health` sert au contrôle de santé du Compose.

Bibliothèque standard seule (image `python:3.12-slim`).
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("FOURNISSEUR_PORT", "8099"))
REPONSE = {
    "refus": False,
    "motif_refus": None,
    "reponse": "Une aventure de pirates, d'après une critique [S:1].",
    "series_citees": [{"serie_id": 1, "fragment_ids": [702]}],
}
REFORMULATION = {"question": "une aventure de pirates"}


class Fournisseur(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _repondre(self, statut: int, corps: object) -> None:
        donnees = json.dumps(corps).encode()
        self.send_response(statut)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def do_GET(self) -> None:  # noqa: N802
        self._repondre(200 if self.path == "/health" else 404, {})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._repondre(404, {})
            return
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        schema = corps["response_format"]["json_schema"]["schema"]
        sortie = REFORMULATION if schema.get("required") == ["question"] else REPONSE
        self._repondre(
            200,
            {
                "id": "cmpl-essai",
                "model": corps["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(sortie)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            },
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Fournisseur).serve_forever()
