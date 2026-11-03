"""Le journal : JSON, une ligne par requête et par événement.

Une requête : identifiant, méthode, route, statut, code d'erreur, durée, nom de la
clé (jamais sa valeur), corpus et encodage servis — et, pour la recherche, les
réglages appliqués et l'`identite` si le client la fournit. Le texte d'une question
n'y entre que sur réglage explicite (désactivé par défaut).

Un appel au modèle de langage : une ligne de plus (`appel_modele`), écrite par la
couche des fournisseurs — profil, modèle, réglages, jetons, coût, latence, statut, le
NOM de la variable de la clé. Jamais une clé, un message, un passage ni une sortie du
modèle.

`configurer` n'est appelé qu'au lancement du serveur (`__main__`) : en test, les
enregistrements remontent au journal racine, où pytest les recueille.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, cast

JOURNAL = logging.getLogger("api_modele")
REQUETES = logging.getLogger("api_modele.requetes")
APPELS = logging.getLogger("api_modele.appels")


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
    """Le journal de l'API, en JSON sur la sortie d'erreur ; pas de double écriture."""
    sortie = logging.StreamHandler(sys.stderr)
    sortie.setFormatter(FormatJson())
    JOURNAL.handlers[:] = [sortie]
    JOURNAL.setLevel(niveau)
    JOURNAL.propagate = False


def evenement(nom: str, champs: dict[str, object], niveau: int = logging.INFO) -> None:
    JOURNAL.log(niveau, nom, extra={"champs": champs})


def requete(champs: dict[str, object]) -> None:
    REQUETES.info("requete", extra={"champs": champs})


def appel(champs: dict[str, object]) -> None:
    APPELS.info("appel_modele", extra={"champs": champs})
