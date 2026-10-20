"""Le contrat OpenAPI de l'API, versionné dans `openapi.json`.

    uv run python -m api_modele.contrat     # réécrit openapi.json

Motif repris du module 02 (`outils/exporter_openapi.py`) : sérialisation stable, et
un test (`tests/test_openapi.py`) qui compare le fichier versionné au contrat généré,
contenu puis octets. L'application est construite sans démarrer : ni base, ni
service, ni lecture de l'environnement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from api_modele.application import creer_application
from api_modele.configuration import RACINE_MODULE, charger

DESTINATION = RACINE_MODULE / "openapi.json"
#: Des réglages de contrat : aucune valeur réelle.
ENVIRONNEMENT_DU_CONTRAT = {
    "API_MODELE_CLES": "contrat:" + "c" * 32,
    "API_MODELE_DSN": "postgresql://contrat@localhost/contrat",
}


def contrat() -> dict[str, Any]:
    return creer_application(charger(ENVIRONNEMENT_DU_CONTRAT)).openapi()


def serialiser(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def exporter(destination: Path = DESTINATION) -> Path:
    destination.write_text(serialiser(contrat()), encoding="utf-8")
    return destination


if __name__ == "__main__":
    print(f"contrat écrit : {exporter()}")
    sys.exit(0)
