"""L'application : routes, cycle de vie, pages.

Au démarrage, `/v1/info` est lu une fois : un échec n'empêche pas de démarrer, l'écran
le dit. L'application ne parle jamais à un fournisseur ni au catalogue en base : à l'API
du modèle seule, par son contrat. Elle a sa base à elle (schéma `application`, rôle
`app_rw`, E4) pour les comptes : absente ou injoignable, le conseiller fonctionne, les
pages de compte le disent (503).

Les sessions (`sessions`) : un cookie signé, le compte relu en base à chaque requête, un
jeton CSRF sur chaque formulaire `POST` — celui du conseiller compris.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from python_multipart.exceptions import FormParserError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException as StarletteHTTPException

from application import (
    __version__,
    historique,
    journal,
    pages_catalogue,
    pages_comptes,
    presentation,
    sessions,
)
from application.base import Base, BaseIndisponible
from application.client_api import (
    ApiDelai,
    ApiInjoignable,
    ClientApi,
    ErreurApi,
    VueApi,
)
from application.client_catalogue import ClientCatalogue
from application.config import Reglages
from application.enveloppe import Enveloppe
from application.metriques import Metriques, autorisee
from application.motdepasse import Hacheur
from application.sessions import (
    AccesRefuse,
    ComptesIndisponibles,
    JetonRefuse,
    NonConnecte,
    Redirection,
)

ICI = Path(__file__).resolve().parent
GABARITS = ICI / "gabarits"
STATIQUE = ICI / "statique"
#: Le statut d'une page d'erreur → ses textes.
PAGES_ERREUR = {
    400: ("erreur_400_titre", "erreur_400_texte"),
    401: ("erreur_401_titre", "erreur_401_texte"),
    403: ("erreur_403_titre", "erreur_403_texte"),
    404: ("erreur_404_titre", "erreur_404_texte"),
    405: ("erreur_405_titre", "erreur_405_texte"),
    413: ("erreur_413_titre", "erreur_413_texte"),
    500: ("erreur_500_titre", "erreur_500_texte"),
}


@dataclass(frozen=True, slots=True)
class Formulaire:
    """Ce que le formulaire porte ; None : la valeur par défaut."""

    question: str = ""
    historique: str = ""
    profil: str | None = None
    temperature: float | None = None
    reformuler: bool | None = None
    mode_demonstration: bool | None = None
    mode: str | None = None
    champ_fautif: str | None = None
    erreur: str | None = None


def champ(donnees: FormData, nom: str) -> str:
    """Un champ texte du formulaire ; un fichier envoyé à sa place ne compte pas."""
    valeur = donnees.get(nom)
    return valeur if isinstance(valeur, str) else ""


def environnement_gabarits() -> Environment:
    """L'échappement, explicite ; une variable manquante est une erreur, pas un vide."""
    return Environment(
        loader=FileSystemLoader(str(GABARITS)),
        autoescape=True,
        undefined=StrictUndefined,
    )


def creer_application(
    reglages: Reglages,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    hacheur: Hacheur | None = None,
    transport_catalogue: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """L'application, prête à démarrer ; rien n'est ouvert avant son cycle de vie.

    `transport` : la doublure de l'API en mémoire (tests), qui sert aussi le catalogue
    sauf `transport_catalogue` ; `hacheur` : un moteur Argon2id aux paramètres réduits
    (tests) — la production prend les défauts."""
    metriques = Metriques()
    catalogue = (
        ClientCatalogue(
            reglages.catalogue.adresse,
            reglages.catalogue.cle,
            reglages.catalogue.delais,
            metriques,
            transport_catalogue if transport_catalogue is not None else transport,
        )
        if reglages.catalogue.cle is not None
        else None
    )
    if catalogue is None:
        metriques.etat_catalogue("non_configure")
    base = Base(reglages.base_dsn, reglages.comptes.delai_base_s, metriques)
    client = ClientApi(
        reglages.adresse_api, reglages.cle_api, reglages.delais, metriques, transport
    )
    vue = VueApi(client, reglages.limites, metriques)
    environnement = environnement_gabarits()
    gabarits = Jinja2Templates(env=environnement)
    t = reglages.textes

    @asynccontextmanager
    async def cycle(_app: FastAPI) -> AsyncGenerator[None]:
        await run_in_threadpool(app.state.hacheur.preparer)
        await vue.rafraichir()
        metriques.api_sante_ok.set(1 if await client.sante() else 0)
        vue.limites()  # un écart aux bornes publiées se journalise dès le démarrage
        journal.evenement(
            "demarrage",
            {
                "version": __version__,
                "etat_api": vue.etat,
                "ecoute": f"{reglages.hote}:{reglages.port}",
                "environnement": reglages.environnement,
                "base": "configuree" if base.configuree else "non_configuree",
            },
        )
        if reglages.cle_session_ephemere:
            # Développement seul (la configuration l'impose ailleurs) : la clé est tirée
            # au démarrage, les sessions se perdent quand l'application redémarre.
            journal.evenement("cle_session_ephemere", niveau=logging.WARNING)
        try:
            yield
        finally:
            await client.fermer()
            if catalogue is not None:
                await catalogue.fermer()
            journal.evenement("arret")

    def suffixe_menu(request: Request | None, retour: object) -> str:
        """Les liens « Connexion » et « Créer un compte » du menu ramènent à la page
        d'où l'on vient : la page affichée (en `GET`), ou le retour que porte déjà la
        page de connexion ou d'inscription ; l'accueil sinon."""
        if request is None:
            return ""
        if request.url.path in ("/connexion", "/inscription"):
            cible = retour if isinstance(retour, str) else "/"
        elif request.method == "GET":
            cible = request.url.path
        else:
            cible = "/"
        return sessions.suffixe_retour(sessions.retour_local(cible))

    def contexte(
        request: Request | None = None, **valeurs: object
    ) -> dict[str, object]:
        """Ce que toute page reçoit : les textes, le pied de page ; le compte courant
        (le menu par rôle) et le jeton de la session (le bouton de déconnexion). Une
        page d'erreur de l'enveloppe (413, 500) n'a pas de requête : le menu anonyme."""
        compte = getattr(request.state, "compte", None) if request else None
        return {
            "suffixe_menu": suffixe_menu(request, valeurs.get("retour")),
            "t": t,
            "pied": presentation.pied_de_page(vue.etat, vue.info, reglages),
            "compte": compte,
            "connecte": (
                presentation.dire(t, "menu_connecte", nom=compte.nom_affiche)
                if compte is not None
                else None
            ),
            "jeton": sessions.jeton_csrf(request) if request else None,
            **valeurs,
        }

    def rendre(
        request: Request, gabarit: str, statut: int = 200, **valeurs: object
    ) -> Response:
        return gabarits.TemplateResponse(
            request, gabarit, contexte(request, **valeurs), status_code=statut
        )

    def page_erreur(
        statut: int,
        identifiant: str,
        textes: tuple[str, str] | None = None,
        request: Request | None = None,
    ) -> Response:
        titre, texte = textes or PAGES_ERREUR.get(statut, PAGES_ERREUR[500])
        contenu = environnement.get_template("erreur.html").render(
            contexte(
                request,
                titre=t[titre],
                texte=t[texte],
                identifiant=presentation.dire(
                    t, "erreur_identifiant", identifiant=identifiant
                ),
            )
        )
        return HTMLResponse(contenu, status_code=statut)

    app = FastAPI(
        title=t["titre_application"],
        version=__version__,
        lifespan=cycle,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Les sessions d'abord : l'enveloppe, ajoutée ensuite, reste la plus extérieure
    # (ses en-têtes et ses pages 413 et 500 valent pour tout).
    sessions.installer(app, reglages)
    app.add_middleware(
        Enveloppe,
        metriques=metriques,
        corps_octets_max=reglages.corps_octets_max,
        page_erreur=page_erreur,
    )

    async def erreur_http(request: Request, exc: Exception) -> Response:
        statut = exc.status_code if isinstance(exc, StarletteHTTPException) else 500
        await sessions.compte_courant(request)  # le menu du compte connecté
        reponse = page_erreur(
            statut, str(request.state.identifiant_requete), request=request
        )
        entetes: Mapping[str, str] | None = (
            exc.headers if isinstance(exc, StarletteHTTPException) else None
        )
        for nom, valeur in (entetes or {}).items():
            reponse.headers[nom] = valeur
        return reponse

    app.add_exception_handler(StarletteHTTPException, erreur_http)

    def identifiant_de(request: Request) -> str:
        return str(request.state.identifiant_requete)

    async def redirection(request: Request, exc: Exception) -> Response:
        chemin = exc.chemin if isinstance(exc, Redirection) else "/"
        return RedirectResponse(chemin, status_code=303)

    async def acces_refuse(request: Request, exc: Exception) -> Response:
        request.state.code_erreur = "acces_refuse"
        return page_erreur(403, identifiant_de(request), request=request)

    async def jeton_refuse(request: Request, exc: Exception) -> Response:
        request.state.code_erreur = "csrf_refuse"
        await sessions.compte_courant(request)  # le menu du compte connecté
        return page_erreur(
            403,
            identifiant_de(request),
            ("erreur_csrf_titre", "erreur_csrf_texte"),
            request,
        )

    async def comptes_indisponibles(request: Request, exc: Exception) -> Response:
        request.state.code_erreur = "comptes_indisponibles"
        return page_erreur(
            503,
            identifiant_de(request),
            ("erreur_comptes_titre", "erreur_comptes_texte"),
            request,
        )

    async def non_connecte(request: Request, exc: Exception) -> Response:
        request.state.code_erreur = "non_connecte"
        return page_erreur(401, identifiant_de(request), request=request)

    app.add_exception_handler(Redirection, redirection)
    app.add_exception_handler(AccesRefuse, acces_refuse)
    app.add_exception_handler(JetonRefuse, jeton_refuse)
    app.add_exception_handler(NonConnecte, non_connecte)
    app.add_exception_handler(ComptesIndisponibles, comptes_indisponibles)
    # Une base perdue en cours de route, qu'une page n'a pas attrapée : 503, jamais 500.
    app.add_exception_handler(BaseIndisponible, comptes_indisponibles)
    app.mount("/statique", StaticFiles(directory=STATIQUE), name="statique")

    def page_principale(
        request: Request,
        statut: int = 200,
        *,
        formulaire: Formulaire | None = None,
        tours: list[historique.Tour] | None = None,
        affichage: presentation.Affichage | None = None,
        encart: presentation.Encart | None = None,
        cloture: str | None = None,
    ) -> Response:
        info = vue.info
        limites = vue.limites()
        f = formulaire or Formulaire()
        reponse = gabarits.TemplateResponse(
            request,
            "index.html",
            contexte(
                request,
                etat=presentation.etat_du_service(vue.etat, info, reglages),
                info_connue=info is not None,
                options=(
                    []
                    if info is None
                    else presentation.options_profils(info, reglages, f.profil)
                ),
                positions=presentation.positions_proposees(info, reglages),
                temperature=(
                    reglages.temperature_defaut
                    if f.temperature is None
                    else f.temperature
                ),
                reformuler=(
                    presentation.reformuler_par_defaut(info, reglages)
                    if f.reformuler is None
                    else f.reformuler
                ),
                mode_demonstration=(
                    reglages.mode_demonstration_defaut
                    if f.mode_demonstration is None
                    else f.mode_demonstration
                ),
                modes=presentation.modes_proposes(info),
                mode_choisi=f.mode or presentation.mode_par_defaut(info),
                question_max=limites.question_caracteres_max,
                question_aide=presentation.dire(
                    t, "question_aide", max=limites.question_caracteres_max
                ),
                formulaire=f,
                tours=[
                    {
                        "role": t[f"role_{tour.role}"],
                        "segments": presentation.segments_d_un_tour(tour.contenu),
                    }
                    for tour in tours or []
                ],
                affichage=affichage,
                encart=encart,
                cloture=cloture,
            ),
            status_code=statut,
        )
        if encart is not None and encart.retry_after is not None:
            reponse.headers["Retry-After"] = str(encart.retry_after)
        return reponse

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def accueil(request: Request) -> Response:
        await sessions.page(request)
        await vue.rafraichir()
        return page_principale(request)

    def lire_formulaire(
        donnees: FormData,
    ) -> tuple[Formulaire, str | None, str | None]:
        """Les champs, et l'erreur de formulaire : (formulaire, champ, message)."""
        limites = vue.limites()
        question = champ(donnees, "question").replace("\r\n", "\n").strip()
        profil = champ(donnees, "profil")
        brute = champ(donnees, "temperature")
        positions = presentation.positions_proposees(vue.info, reglages)
        try:
            temperature = float(brute) if brute else reglages.temperature_defaut
        except ValueError:
            temperature = float("nan")
        admise = temperature in [p.valeur for p in positions]
        lu = Formulaire(
            question=question,
            historique=champ(donnees, "historique"),
            profil=profil or None,
            temperature=temperature if admise else None,
            reformuler="reformuler" in donnees,
            mode_demonstration="mode_demonstration" in donnees,
            mode=champ(donnees, "mode") or None,
        )
        if not question:
            return lu, "question", t["erreur_question_vide"]
        if len(question) > limites.question_caracteres_max:
            message = presentation.dire(
                t,
                "erreur_question_longue",
                max=limites.question_caracteres_max,
                n=len(question),
            )
            return lu, "question", message
        if not admise:
            return lu, "temperature", t["erreur_temperature"]
        if vue.info is not None:
            noms = [p.nom for p in presentation.profils_proposes(vue.info, reglages)]
            # Sans champ (page rendue sans liste) : le défaut, jamais un profil exclu.
            profil = profil or presentation.profil_par_defaut(vue.info, reglages) or ""
            lu = replace(lu, profil=profil or None)
            if profil not in noms:
                return lu, "profil", t["erreur_profil"]
        return lu, None, None

    @app.post("/question", response_class=HTMLResponse, include_in_schema=False)
    async def question(request: Request) -> Response:
        identifiant = str(request.state.identifiant_requete)
        try:
            donnees = await request.form()
        except FormParserError:
            # Un corps qu'un navigateur n'écrit pas : pas une erreur de l'API.
            raise StarletteHTTPException(status_code=400) from None
        # Le jeton d'abord : un formulaire sans jeton valide n'atteint pas l'API.
        sessions.exiger_jeton(request, champ(donnees, sessions.CHAMP_JETON))
        await sessions.page(request)
        await vue.rafraichir()
        limites = vue.limites()
        try:
            tours = historique.lire(champ(donnees, "historique"), limites)
        except historique.HistoriqueInvalide:
            # Rien n'est envoyé ; le texte porté par la page n'est pas repris.
            request.state.code_erreur = "historique_invalide"
            encart = presentation.Encart(
                t["conversation_invalide"], 422, nouvelle_conversation=True
            )
            return page_principale(request, 422, encart=encart)
        request.state.nb_tours_historique = len(tours)
        request.state.taille_historique = historique.taille(tours)
        if len(tours) >= limites.tours_max:
            texte = presentation.dire(
                t, "conversation_terminee", echanges=limites.echanges
            )
            encart = presentation.Encart(texte, 200, nouvelle_conversation=True)
            return page_principale(request, encart=encart)

        lu, fautif, message = lire_formulaire(donnees)
        if fautif is not None:
            f = replace(lu, champ_fautif=fautif, erreur=message)
            return page_principale(request, 422, formulaire=f, tours=tours)
        info = vue.info
        if info is None:
            # Aucune liste connue : rien à valider, rien n'est envoyé.
            cle = "etat_cle" if vue.etat == "cle" else "degrade_injoignable"
            encart = presentation.Encart(t[cle], 503)
            return page_principale(
                request, 503, formulaire=lu, tours=tours, encart=encart
            )

        reglages_reponse = info.modele_langage.reglages_reponse
        mode = (
            lu.mode
            if lu.mode in reglages_reponse.mode.valeurs
            else reglages_reponse.mode.defaut
        )
        demande: dict[str, object] = {
            "question": lu.question,
            "historique": historique.en_liste(tours),
            "reformuler": lu.reformuler,
            "profil": lu.profil,
            "temperature": lu.temperature,
            "mode": mode,
            "identite": reglages.identite,
        }
        try:
            resultat = await client.reponse(demande)
        except (ApiInjoignable, ApiDelai) as exc:
            delai = isinstance(exc, ApiDelai)
            request.state.code_erreur = "api_delai" if delai else "api_injoignable"
            encart = presentation.encart_transport(delai, reglages)
            return page_principale(
                request, encart.statut, formulaire=lu, tours=tours, encart=encart
            )
        except ErreurApi as exc:
            request.state.code_erreur = exc.code
            if exc.identifiant_requete:
                request.state.identifiant_api = exc.identifiant_requete
            encart = presentation.encart_erreur(
                exc, str(lu.profil), identifiant, reglages
            )
            if encart.profil_a_revoir:
                await vue.rafraichir()
                lu = replace(lu, profil=None)
            return page_principale(
                request, encart.statut, formulaire=lu, tours=tours, encart=encart
            )

        corps = resultat.corps
        request.state.identifiant_api = (
            resultat.identifiant_api or corps.contexte.identifiant_requete
        )
        suivants = [
            *tours,
            historique.Tour("libraire", lu.question),
            historique.Tour("assistant", corps.reponse),
        ]
        cloture: str | None = None
        if len(suivants) // 2 >= limites.echanges:
            cloture = presentation.dire(
                t, "conversation_terminee", echanges=limites.echanges
            )
        elif historique.depasse(suivants, limites):
            cloture = t["conversation_longueur"]
        f = replace(
            lu,
            question="",
            historique="" if cloture else historique.ecrire(suivants),
        )
        return page_principale(
            request,
            formulaire=f,
            tours=tours,
            affichage=presentation.affichage_de(
                lu.question,
                corps,
                reglages,
                demonstration=bool(lu.mode_demonstration),
            ),
            cloture=cloture,
        )

    @app.get("/a-propos", response_class=HTMLResponse, include_in_schema=False)
    async def a_propos(request: Request) -> Response:
        await sessions.page(request)
        await vue.rafraichir()
        return gabarits.TemplateResponse(
            request,
            "a_propos.html",
            contexte(
                request,
                lignes=presentation.lignes_a_propos(vue.info, reglages),
                info_connue=vue.info is not None,
                apropos_comptes=presentation.dire(
                    t,
                    "apropos_comptes",
                    seuil=reglages.comptes.echecs_avant_verrou,
                    minutes=-(-reglages.comptes.duree_verrou_s // 60),
                ),
            ),
        )

    @app.get("/sante", include_in_schema=False)
    async def sante(request: Request) -> JSONResponse:
        """Toujours 200 : l'application est vivante, et elle sert sans l'API."""
        base: Base = request.app.state.base
        api_ok = await client.sante()
        metriques.api_sante_ok.set(1 if api_ok else 0)
        if catalogue is None:
            etat_catalogue = "non_configure"
        else:
            etat_catalogue = "ok" if await catalogue.sante() else "indisponible"
        return JSONResponse(
            {
                "application": "ok",
                "api": "ok" if api_ok else "indisponible",
                "base": await base.sonder() if base.configuree else base.etat,
                "catalogue": etat_catalogue,
                "version": __version__,
            }
        )

    @app.get("/metrics", include_in_schema=False)
    async def exposer_metriques(request: Request) -> Response:
        adresse = request.client.host if request.client else None
        if not autorisee(adresse, reglages.metriques_reseaux):
            raise StarletteHTTPException(status_code=404)
        contenu, type_ = metriques.exposer()
        return Response(content=contenu, media_type=type_)

    pages_comptes.installer(app, reglages, rendre)
    pages_catalogue.installer(app, reglages, rendre, catalogue)

    app.state.vue = vue
    app.state.metriques = metriques
    app.state.base = base
    app.state.hacheur = hacheur if hacheur is not None else Hacheur()
    app.state.reglages = reglages
    return app
