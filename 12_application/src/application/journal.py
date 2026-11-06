"""Le journal : JSON, une ligne par requête et par événement (forme du module 11).

Une requête : route, méthode, statut, durée, identifiant de la requête de page,
identifiant de la requête de l'API, code d'erreur, nombre de tours et taille de
l'historique. Jamais la question, l'historique ni la réponse ; jamais la clé.

`configurer` n'est appelé qu'au lancement du serveur (`__main__`) : en test, les
enregistrements remontent au journal racine, où pytest les recueille.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

JOURNAL = logging.getLogger("application")
REQUETES = logging.getLogger("application.requetes")


class FormatJson(logging.Formatter):
    """Un enregistrement → une ligne JSON ; ses `champs` à plat."""

    def format(self, record: logging.LogRecord) -> str:
        ligne: dict[str, Any] = {
            "horodatage": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "niveau": record.levelname,
            "evenement": record.getMessage(),
        }
        champs: object = getattr(record, "champs", None)
        if isinstance(champs, dict):
            ligne.update(cast(dict[str, Any], champs))
        if record.exc_info:
            ligne["trace"] = self.formatException(record.exc_info)
        return json.dumps(ligne, ensure_ascii=False, default=str)


def configurer(niveau: int = logging.INFO) -> None:
    """Une sortie : l'erreur standard, en JSON."""
    sortie = logging.StreamHandler(sys.stderr)
    sortie.setFormatter(FormatJson())
    JOURNAL.handlers = [sortie]
    JOURNAL.setLevel(niveau)
    JOURNAL.propagate = False


def evenement(
    nom: str, champs: Mapping[str, object] | None = None, niveau: int = logging.INFO
) -> None:
    JOURNAL.log(niveau, nom, extra={"champs": dict(champs or {})})


def requete(champs: Mapping[str, object]) -> None:
    REQUETES.info("requete", extra={"champs": dict(champs)})
