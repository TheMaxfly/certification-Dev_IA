"""L'enveloppe de chaque requête HTTP (ASGI pur) :

- un identifiant de requête (UUID v4), dans l'état de la requête et en en-tête
  `X-Identifiant-Requete` de toute réponse ;
- `Cache-Control: no-store` sur toute réponse (contrat, B.1) : une réponse porte un
  classement, des extraits ou un état qui ne valent qu'au moment de la requête ;
- le corps borné : au-delà du maximum, 413 `corps_trop_volumineux`, avant que
  l'application ne le lise ;
- l'erreur imprévue rattrapée : 500 `erreur_interne`, sans trace de pile dans la
  réponse, avec la trace dans le journal ;
- une ligne de journal et les métriques de la requête.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api_modele import journal
from api_modele.erreurs import ENTETE_IDENTIFIANT, Erreur
from api_modele.metriques import Metriques

ROUTE_INCONNUE = "inconnue"


def _erreur(
    statut: int, code: str, message: str, ident: str, details: dict[str, Any] | None
) -> JSONResponse:
    corps = Erreur(
        code=code, message=message, details=details, identifiant_requete=ident
    )
    return JSONResponse(corps.model_dump(), status_code=statut)


class Enveloppe:
    def __init__(
        self,
        app: ASGIApp,
        metriques: Metriques,
        corps_octets_max: int,
        contexte: dict[str, object],
    ) -> None:
        self.app = app
        self.metriques = metriques
        self.corps_max = corps_octets_max
        # Champs communs du journal (corpus et encodage servis), posés au démarrage.
        self.contexte = contexte

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ident = str(uuid.uuid4())
        etat: dict[str, Any] = scope.setdefault("state", {})
        etat["identifiant_requete"] = ident
        debut = time.perf_counter()
        reponse: dict[str, Any] = {"statut": 500, "commencee": False}

        async def envoyer(message: Message) -> None:
            if message["type"] == "http.response.start":
                reponse["statut"] = message["status"]
                reponse["commencee"] = True
                entetes = MutableHeaders(scope=message)
                entetes[ENTETE_IDENTIFIANT] = ident
                entetes.setdefault("Cache-Control", "no-store")
            await send(message)

        try:
            corps, trop = await self._lire_corps(scope, receive)
            if trop:
                etat["code_erreur"] = "corps_trop_volumineux"
                await _erreur(
                    413,
                    "corps_trop_volumineux",
                    "Corps de requête trop volumineux.",
                    ident,
                    {"maximum_octets": self.corps_max},
                )(scope, receive, envoyer)
                return
            await self.app(scope, _rejouer(corps, receive), envoyer)
        except Exception:
            journal.JOURNAL.exception(
                "erreur_interne", extra={"champs": {"identifiant_requete": ident}}
            )
            if reponse["commencee"]:
                raise
            etat["code_erreur"] = "erreur_interne"
            await _erreur(500, "erreur_interne", "Erreur interne.", ident, None)(
                scope, receive, envoyer
            )
        finally:
            self._consigner(scope, etat, reponse["statut"], time.perf_counter() - debut)

    async def _lire_corps(self, scope: Scope, receive: Receive) -> tuple[bytes, bool]:
        """Le corps entier, ou (b"", True) dès qu'il dépasse le maximum."""
        for nom, valeur in scope.get("headers", []):
            if nom == b"content-length":
                try:
                    if int(valeur) > self.corps_max:
                        return b"", True
                except ValueError:
                    return b"", True
        morceaux: list[bytes] = []
        taille = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            morceau: bytes = message.get("body", b"")
            taille += len(morceau)
            if taille > self.corps_max:
                return b"", True
            morceaux.append(morceau)
            if not message.get("more_body", False):
                break
        return b"".join(morceaux), False

    def _consigner(
        self, scope: Scope, etat: dict[str, Any], statut: int, duree_s: float
    ) -> None:
        # La route trouvée par le routeur (son gabarit), sinon une seule étiquette.
        chemin: object = getattr(scope.get("route"), "path", None)
        route = chemin if isinstance(chemin, str) else ROUTE_INCONNUE
        methode = str(scope.get("method", ""))
        code = etat.get("code_erreur")
        self.metriques.requetes.labels(
            route=route, methode=methode, statut=str(statut)
        ).inc()
        self.metriques.duree.labels(route=route).observe(duree_s)
        if code is not None:
            self.metriques.erreurs.labels(code=str(code)).inc()
        champs: dict[str, object] = {
            "identifiant_requete": etat.get("identifiant_requete"),
            "methode": methode,
            "route": route,
            "statut": statut,
            "code": code,
            "duree_ms": round(duree_s * 1000, 3),
            "cle": etat.get("cle"),
        }
        champs.update(self.contexte)
        for cle in ("identite", "reglages", "question"):
            if cle in etat:
                champs[cle] = etat[cle]
        journal.requete(champs)


def _rejouer(corps: bytes, receive: Receive) -> Receive:
    """Rend le corps déjà lu, en un message, puis laisse passer la suite."""
    rendu = False

    async def recevoir() -> Message:
        nonlocal rendu
        if not rendu:
            rendu = True
            return {"type": "http.request", "body": corps, "more_body": False}
        return await receive()

    return recevoir
