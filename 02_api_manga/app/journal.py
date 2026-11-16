"""Le journal du catalogue : JSON, une ligne par événement (E5, lot 1, 04 et 04 bis).

La forme des deux autres services (l'API du modèle, l'application) : `horodatage` (UTC,
à la milliseconde), `niveau`, `evenement` (le message), les champs bornés d'un événement
(`champs`), et `trace` quand une exception l'accompagne — sur la même ligne.

**Ce que le journal garde, et ce qu'il ne garde pas.** Une ligne par requête servie —
la méthode, le chemin **sans sa chaîne de requête**, le statut, la durée ; jamais le
texte d'une recherche, jamais l'adresse de l'appelant, jamais une clé. Les lectures de
santé (`/health`, `/live`) qui répondent normalement ne s'écrivent pas ; une réponse en
erreur, si. Une base perdue s'écrit quand elle tombe et quand elle revient, pas à chaque
lecture. Les autorisations disent le libellé du consommateur, jamais sa clé.

Deux voies, une forme :

- **le déploiement** (`Dockerfile`) passe `app/journalisation.json` à uvicorn
  (`--log-config`) et coupe son journal d'accès (`--no-access-log`), que la ligne de
  requête remplace : la racine, le démarrage et les erreurs d'uvicorn sortent par le
  même formateur ;
- **sans configuration de l'hôte** (un lancement local), `configure_logging` de
  `app.main` pose ce formateur sur la racine ; il n'écrase jamais une configuration déjà
  posée.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

#: La configuration du journal du déploiement, lue par uvicorn (`--log-config`).
CONFIGURATION = Path(__file__).resolve().parent / "journalisation.json"
REQUETES = logging.getLogger("app.requetes")
#: Les routes de santé : une réponse normale n'y est pas journalisée.
ROUTES_DE_SANTE = frozenset({"/health", "/live"})

type Message = MutableMapping[str, Any]
type Recevoir = Callable[[], Awaitable[Message]]
type Envoyer = Callable[[Message], Awaitable[None]]
type Asgi = Callable[[Message, Recevoir, Envoyer], Awaitable[None]]


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


class JournalDesRequetes:
    """L'intergiciel de la ligne de requête : la méthode, le chemin (sans la chaîne de
    requête), le statut, la durée — rien de l'appelant. Une lecture de santé qui répond
    sous 400 n'est pas écrite."""

    def __init__(self, app: Asgi) -> None:
        self.app = app

    async def __call__(self, scope: Message, receive: Recevoir, send: Envoyer) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        debut = time.perf_counter()
        statut = 500

        async def envoyer(message: Message) -> None:
            nonlocal statut
            if message["type"] == "http.response.start":
                statut = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, envoyer)
        finally:
            chemin = str(scope.get("path", ""))
            if not (chemin in ROUTES_DE_SANTE and statut < 400):
                REQUETES.info(
                    "requete",
                    extra={
                        "champs": {
                            "methode": str(scope.get("method", "")),
                            "chemin": chemin,
                            "statut": statut,
                            "duree_ms": round((time.perf_counter() - debut) * 1000),
                        }
                    },
                )


class SuiviDeLaBase:
    """L'état de la base vu par `/health` : ce qui change s'écrit, pas chaque lecture.
    `echec()` est vrai la première fois que la base tombe, `retour()` quand elle
    revient."""

    def __init__(self) -> None:
        self._en_panne = False
        self._verrou = threading.Lock()

    def echec(self) -> bool:
        with self._verrou:
            neuf, self._en_panne = not self._en_panne, True
            return neuf

    def retour(self) -> bool:
        with self._verrou:
            revenue, self._en_panne = self._en_panne, False
            return revenue
