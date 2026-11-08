"""Les sessions, le jeton CSRF, le compte courant et les gardes de route.

**Le cookie** (`SessionMiddleware` de Starlette, signé par `itsdangerous`, expiration
vérifiée côté serveur) ne porte que l'identifiant du compte, sa génération et le jeton
CSRF — ni rôle, ni pseudo (E4, lot 1, décision 5). `HttpOnly`, `SameSite=Lax`, `Secure`
hors développement. Les visiteurs anonymes le reçoivent aussi : il porte leur jeton.

**Le compte courant** est relu en base à chaque requête d'un visiteur connecté : rôle,
état, génération, mot de passe à changer. Une génération différente ou un compte inactif
vident la session (journal `session_refusee`, identifiant interne seul) ; la base
injoignable laisse la session intacte et traite le visiteur en anonyme le temps de la
requête : le conseiller fonctionne sans elle.

**Le jeton CSRF** : un par session, tiré à la première page, renouvelé à l'ouverture ;
champ caché `jeton` de chaque formulaire `POST`, comparé en temps constant ; absent ou
faux, un 403 et sa page nommée.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import quote

from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware

from application import comptes, journal
from application.base import Base, BaseIndisponible
from application.comptes import Compte
from application.config import Reglages

NOM_COOKIE = "conseiller_session"
CHAMP_JETON = "jeton"
#: Les chemins qu'un compte au mot de passe à changer peut encore ouvrir.
CHEMINS_AVANT_CHANGEMENT = frozenset({"/compte/mot-de-passe", "/deconnexion"})


class NonConnecte(Exception):
    """Un formulaire (`POST`) d'une page réservée, envoyé sans session : 401 et sa page
    (une page lue en `GET` renvoie, elle, vers la connexion)."""


class Redirection(Exception):
    """Une page qui renvoie ailleurs (303), vers un chemin local seulement."""

    def __init__(self, chemin: str) -> None:
        super().__init__(chemin)
        self.chemin = chemin


class AccesRefuse(Exception):
    """Connecté, sans le rôle : 403 et sa page."""


class JetonRefuse(Exception):
    """Un formulaire sans jeton valide : 403 et la page « formulaire expiré »."""


class ComptesIndisponibles(Exception):
    """La base des comptes est absente ou injoignable : 503 et son encart."""


def nom_cookie(environnement: str) -> str:
    """Un cookie par environnement : celui de la préproduction n'est pas lu en
    production, même sur le même poste."""
    if environnement == "developpement":
        return NOM_COOKIE
    return f"{NOM_COOKIE}_{environnement}"


def installer(app: FastAPI, reglages: Reglages) -> None:
    """La signature est liée à l'environnement : avec la même clé, une session de
    préproduction ne vaut pas en production (les identifiants des comptes s'y
    recouvrent). Une clé par environnement reste la règle (README)."""
    app.add_middleware(
        SessionMiddleware,
        secret_key=f"{reglages.environnement}:{reglages.cle_session}",
        session_cookie=nom_cookie(reglages.environnement),
        max_age=reglages.comptes.duree_session_s,
        same_site="lax",
        https_only=reglages.environnement != "developpement",
    )


def jeton_csrf(request: Request) -> str:
    """Le jeton de la session ; tiré s'il manque (première page d'un visiteur). Réécrit
    à chaque page qui le porte : le cookie est renvoyé, son expiration glisse — une
    session vit `duree_session_s` après la dernière page, pas après la première."""
    jeton = request.session.get("csrf")
    if not isinstance(jeton, str):
        jeton = secrets.token_urlsafe(32)
    request.session["csrf"] = jeton
    return jeton


def jeton_valide(request: Request, valeur: object) -> bool:
    attendu = request.session.get("csrf")
    if not isinstance(attendu, str) or not isinstance(valeur, str) or not valeur:
        return False
    return hmac.compare_digest(attendu.encode(), valeur.encode())


def exiger_jeton(request: Request, valeur: object) -> None:
    if not jeton_valide(request, valeur):
        # Le compte de la session, s'il y en a un (son identifiant seul, signé).
        ident = request.session.get("compte")
        champs = {"compte_id": ident} if isinstance(ident, int) else {}
        journal.evenement_requete(request, "csrf_refuse", champs)
        raise JetonRefuse


def ouvrir(request: Request, compte: Compte) -> None:
    """Une session neuve : nouveau contenu, nouveau jeton (pas de fixation)."""
    request.session.clear()
    request.session.update(
        {
            "compte": compte.id,
            "generation": compte.generation,
            "csrf": secrets.token_urlsafe(32),
        }
    )
    request.state.compte = compte


def fermer(request: Request) -> None:
    request.session.clear()
    request.state.compte = None


def retour_local(valeur: object, defaut: str = "/") -> str:
    """Un chemin local, ou le défaut : jamais une adresse d'un autre site.

    Un chemin commence par « / » ; « // » (adresse sans schéma), la barre inverse (que
    les navigateurs lisent comme « / ») et les caractères de contrôle sont refusés : ce
    qui reste n'a ni schéma ni hôte."""
    if not isinstance(valeur, str) or not valeur.startswith("/"):
        return defaut
    if valeur.startswith("//") or "\\" in valeur or any(ord(c) < 32 for c in valeur):
        return defaut
    return valeur


def chemin_de(request: Request) -> str:
    chemin = request.url.path
    return f"{chemin}?{request.url.query}" if request.url.query else chemin


async def compte_courant(request: Request) -> Compte | None:
    """Le compte de la session, relu en base ; None pour un anonyme."""
    if getattr(request.state, "compte_relu", False):  # déjà relu pour cette requête
        return request.state.compte
    request.state.compte_relu = True
    base: Base = request.app.state.base
    ident = request.session.get("compte")
    compte: Compte | None = None
    if isinstance(ident, int):
        try:
            lu = await comptes.lire(base, ident)
        except BaseIndisponible:
            request.state.compte = None
            return None
        generation = request.session.get("generation")
        if lu is None or not lu.actif or lu.generation != generation:
            journal.evenement_requete(request, "session_refusee", {"compte_id": ident})
            request.session.clear()
        else:
            compte = lu
    request.state.compte = compte
    return compte


async def page(request: Request) -> Compte | None:
    """Toute page : le compte courant ; un mot de passe à changer y renvoie."""
    compte = await compte_courant(request)
    if (
        compte is not None
        and compte.doit_changer_mdp
        and request.url.path not in CHEMINS_AVANT_CHANGEMENT
    ):
        raise Redirection("/compte/mot-de-passe")
    return compte


async def exiger_connexion(request: Request) -> Compte:
    base: Base = request.app.state.base
    if await base.sonder() != "ok":
        raise ComptesIndisponibles
    compte = await page(request)
    if compte is None:
        if request.method != "GET":
            raise NonConnecte
        raise Redirection("/connexion?retour=" + quote(chemin_de(request), safe="/"))
    return compte


def exiger_role(*roles: str) -> Callable[[Request], Awaitable[Compte]]:
    async def garde(request: Request) -> Compte:
        compte = await exiger_connexion(request)
        if compte.role not in roles:
            # Le gabarit de la route, jamais le chemin brut (il peut porter une saisie).
            route = getattr(request.scope.get("route"), "path", None)
            journal.evenement_requete(request, "acces_refuse", {"route": route})
            raise AccesRefuse
        return compte

    return garde
