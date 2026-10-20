"""Doublure du service d'embedding, pour l'essai d'intégration du Compose.

Le modèle servi (EmbeddingGemma, à accès contrôlé, 1,2 Go de poids, une carte
graphique ou plusieurs minutes de processeur) ne tourne pas sur GitHub. Dans l'essai,
ce serveur prend la place du service sous le même nom, avec la même configuration
(`09_service_embedding/config/embeddinggemma.env`, lue dans l'environnement par
`env_file`) : `/info` rend ce que le registre des encodages attend, `/embed` un
vecteur fixe, `/tokenize` un jeton par caractère. L'API, elle, est la vraie : image
construite, contrôle de cohérence au démarrage, classement du banc.

Bibliothèque standard seule (image `python:3.12-slim`).
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIMENSION = int(os.environ["CLIENT_DIMENSION"])
PREFIXE = os.environ["CLIENT_PREFIXE_REQUETE"]
INFO = {
    "model_id": os.environ["MODEL_ID"],
    "model_sha": os.environ["REVISION"],
    "model_dtype": os.environ["DTYPE"],
    # La version de l'outil de l'image épinglée (registre : `outil_version`).
    "version": "1.9.4",
    "max_input_length": int(os.environ["CLIENT_LONGUEUR_MAXIMALE"]),
}
#: Toute question est encodée sur le deuxième axe : le fragment 702 du jeu d'essai.
VECTEUR = [0.0] * DIMENSION
VECTEUR[1] = 1.0


class Doublure(BaseHTTPRequestHandler):
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
        if self.path == "/health":
            self._repondre(200, {})
        elif self.path == "/info":
            self._repondre(200, INFO)
        else:
            self._repondre(404, {"error": "route inconnue"})

    def do_POST(self) -> None:  # noqa: N802
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        entrees = corps["inputs"]
        if self.path == "/embed":
            if not all(e.startswith(PREFIXE) for e in entrees):
                self._repondre(422, {"error": "préfixe de question absent"})
                return
            self._repondre(200, [VECTEUR for _ in entrees])
        elif self.path == "/tokenize":
            self._repondre(200, [[0] * len(e) for e in entrees])
        else:
            self._repondre(404, {"error": "route inconnue"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ["PORT"])), Doublure).serve_forever()  # noqa: S104
