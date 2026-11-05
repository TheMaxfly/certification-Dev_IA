"""`python -m monitorage` : le serveur de la sonde."""

from __future__ import annotations

import os

from monitorage.metriques import Metriques
from monitorage.serveur import creer_serveur


def principal() -> None:
    hote, _, port = os.environ.get("SONDE_ECOUTE", "127.0.0.1:9108").partition(":")
    creer_serveur(hote, int(port), Metriques(lecture_base=False)).serve_forever()


if __name__ == "__main__":
    principal()
