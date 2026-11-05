"""L'enveloppe de chaque requête HTTP (ASGI pur ; forme du module 11, réécrite) :

- un identifiant de requête (UUID v4), dans l'état de la requête et en en-tête ;
- les en-têtes de sécurité sur toute réponse, `Cache-Control: no-store` sur les pages
  qui portent une conversation ;
- le corps borné : au-delà du maximum, la page 413, avant que l'application ne le lise ;
- l'erreur imprévue rattrapée : la page 500, sans trace de pile, la trace au journal ;
- une ligne de journal et les métriques de la requête.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from application import journal
from application.metriques import Metriques

ENTETE_IDENTIFIANT = "X-Identifiant-Requete"
ROUTE_INCONNUE = "inconnue"
#: Aucun script, aucune ressource externe : les deux choses que la politique protège.
#: `data:` pour les images seules : Pico dessine ses coches et ses chevrons ainsi (14
#: occurrences, toutes `image/svg+xml`), et une image n'exécute rien.
POLITIQUE = (
    "default-src 'none'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
ENTETES_SECURITE = {
    "Content-Security-Policy": POLITIQUE,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
#: Les pages qui portent une question ou une conversation : jamais en cache.
SANS_CACHE = ("/", "/question")

type PageErreur = Callable[[int, str], Response]


class Enveloppe:
    def __init__(
        self,
        app: ASGIApp,
        metriques: Metriques,
        corps_octets_max: int,
        page_erreur: PageErreur,
    ) -> None:
        self.app = app
        self.metriques = metriques
        self.corps_max = corps_octets_max
        self.page_erreur = page_erreur

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
                for nom, valeur in ENTETES_SECURITE.items():
                    entetes[nom] = valeur
                if scope.get("path") in SANS_CACHE:
                    entetes["Cache-Control"] = "no-store"
            await send(message)

        try:
            corps, trop = await self._lire_corps(scope, receive)
            if trop:
                etat["code_erreur"] = "corps_trop_volumineux"
                await self.page_erreur(413, ident)(scope, receive, envoyer)
                return
            await self.app(scope, _rejouer(corps, receive), envoyer)
        except Exception:
            journal.JOURNAL.exception(
                "erreur_interne", extra={"champs": {"identifiant_requete": ident}}
            )
            if reponse["commencee"]:
                raise
            etat["code_erreur"] = "erreur_interne"
            await self.page_erreur(500, ident)(scope, receive, envoyer)
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
        self.metriques.requetes.labels(route=route, statut=str(statut)).inc()
        self.metriques.latence.labels(route=route).observe(duree_s)
        champs: dict[str, object] = {
            "identifiant_requete": etat.get("identifiant_requete"),
            "methode": scope.get("method", ""),
            "route": route,
            "statut": statut,
            "duree_ms": round(duree_s * 1000, 3),
            "code": etat.get("code_erreur"),
        }
        # Des nombres et des identifiants : jamais la question, l'historique, le texte.
        for cle in ("identifiant_api", "nb_tours_historique", "taille_historique"):
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
