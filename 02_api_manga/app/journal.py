"""Le journal du catalogue : JSON, une ligne par événement (E5, lot 1, spec 04 — E5-12).

La forme des deux autres services (l'API du modèle, l'application) : `horodatage` (UTC,
à la milliseconde), `niveau`, `evenement` (le message), et `trace` quand une exception
l'accompagne — sur la même ligne. Aucun autre champ : ce qui était écrit en texte libre
l'est en JSON, rien de plus (ni compte, ni adresse nouvelle, ni clé).

Deux voies, une forme :

- **le déploiement** (`Dockerfile`) passe `app/journalisation.json` à uvicorn
  (`--log-config`) : la racine, les journaux d'uvicorn (démarrage, erreurs, accès) et
  ceux de l'application sortent tous par le même formateur ;
- **sans configuration de l'hôte** (un lancement local), `configure_logging` de
  `app.main` pose ce formateur sur la racine ; il n'écrase jamais une configuration déjà
  posée.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: La configuration du journal du déploiement, lue par uvicorn (`--log-config`).
CONFIGURATION = Path(__file__).resolve().parent / "journalisation.json"


class FormatJson(logging.Formatter):
    """Un enregistrement → une ligne JSON."""

    def format(self, record: logging.LogRecord) -> str:
        ligne: dict[str, Any] = {
            "horodatage": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "niveau": record.levelname,
            "evenement": record.getMessage(),
        }
        if record.exc_info:
            ligne["trace"] = self.formatException(record.exc_info)
        return json.dumps(ligne, ensure_ascii=False, default=str)
