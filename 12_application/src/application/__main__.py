"""Lancer l'application : `uv run --env-file .env python -m application`."""

from __future__ import annotations

import os
import sys

import uvicorn

from application import journal
from application.app import creer_application
from application.config import ConfigurationInvalide, charger


def principal() -> int:
    journal.configurer()
    try:
        reglages = charger(os.environ)
    except ConfigurationInvalide as exc:
        # Le message nomme la variable ou le réglage, jamais une valeur.
        print(exc, file=sys.stderr)
        return 2
    uvicorn.run(
        creer_application(reglages),
        host=reglages.hote,
        port=reglages.port,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(principal())
