"""Le journal : JSON, une ligne par requête et par événement (forme du module 11).

Une requête : route, méthode, statut, durée, identifiant de la requête de page,
identifiant de la requête de l'API, code d'erreur, nombre de tours et taille de
l'historique, identifiant interne du compte. Jamais la question, l'historique ni la
réponse ; jamais la clé.

Un événement de compte (E4, lot 1) : son nom, l'identifiant de la requête (celui de
l'enveloppe), l'identifiant interne du compte s'il y en a un, et des champs bornés.
**Jamais** : mot de passe, empreinte, pseudo, cookie, jeton, texte saisi.

`configurer` n'est appelé qu'au lancement du serveur (`__main__`) : en test, les
enregistrements remontent au journal racine, où pytest les recueille.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, cast

JOURNAL = logging.getLogger("application")
REQUETES = logging.getLogger("application.requetes")
#: L'identifiant de la requête en cours, posé par l'enveloppe : tout événement
#: journalisé pendant une requête le porte, même loin d'elle (l'accès à la base).
IDENTIFIANT_REQUETE: ContextVar[str | None] = ContextVar(
    "identifiant_requete", default=None
)


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
    lignes = dict(champs or {})
    identifiant = IDENTIFIANT_REQUETE.get()
    if identifiant is not None:
        lignes.setdefault("identifiant_requete", identifiant)
    JOURNAL.log(niveau, nom, extra={"champs": lignes})


def requete(champs: Mapping[str, object]) -> None:
    REQUETES.info("requete", extra={"champs": dict(champs)})


def evenement_requete(
    request: object,
    nom: str,
    champs: Mapping[str, object] | None = None,
    niveau: int = logging.INFO,
) -> None:
    """Un événement rattaché à sa requête : son identifiant, et le compte courant."""
    etat = getattr(request, "state", None)
    lignes: dict[str, object] = {
        "identifiant_requete": getattr(etat, "identifiant_requete", None)
    }
    compte = getattr(etat, "compte", None)
    if compte is not None:
        lignes["compte_id"] = getattr(compte, "id", None)
    evenement(nom, {**lignes, **(champs or {})}, niveau)
