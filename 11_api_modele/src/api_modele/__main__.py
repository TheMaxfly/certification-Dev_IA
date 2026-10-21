"""Lancer l'API : `uv run --env-file .env python -m api_modele`.

La configuration est lue et validée d'abord : fautive, rien n'est lancé (code 2).
Le contrôle de cohérence se joue ensuite au démarrage du serveur : un écart, et
Uvicorn s'arrête (code 3), le motif dans le journal.
"""

from __future__ import annotations

import os
import sys

import uvicorn

from api_modele import journal
from api_modele.application import creer_application
from api_modele.configuration import ConfigurationInvalide, charger


def principal() -> int:
    journal.configurer()
    try:
        reglages = charger(os.environ)
    except ConfigurationInvalide as exc:
        journal.evenement("configuration_invalide", {"ecarts": exc.ecarts}, 40)
        return 2
    uvicorn.run(
        creer_application(reglages),
        host=reglages.hote,
        port=reglages.port,
        access_log=False,  # le journal de l'API le remplace, une ligne par requête
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    sys.exit(principal())
