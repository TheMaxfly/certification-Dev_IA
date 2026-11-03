"""L'application : routes, cycle de vie, pages.

Au démarrage, `/v1/info` est lu une fois : un échec n'empêche pas de démarrer, l'écran
le dit. L'application ne parle jamais à la base ni à un fournisseur : l'API seule, par
son contrat.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from starlette.exceptions import HTTPException as StarletteHTTPException

from application import __version__, journal, presentation
from application.client_api import ClientApi, VueApi
from application.config import Reglages
from application.enveloppe import Enveloppe
from application.metriques import Metriques, autorisee

ICI = Path(__file__).resolve().parent
GABARITS = ICI / "gabarits"
STATIQUE = ICI / "statique"
#: Le statut d'une page d'erreur → ses textes.
PAGES_ERREUR = {
    404: ("erreur_404_titre", "erreur_404_texte"),
    405: ("erreur_405_titre", "erreur_405_texte"),
    413: ("erreur_413_titre", "erreur_413_texte"),
    500: ("erreur_500_titre", "erreur_500_texte"),
}


def environnement_gabarits() -> Environment:
    """L'échappement, explicite ; une variable manquante est une erreur, pas un vide."""
    return Environment(
        loader=FileSystemLoader(str(GABARITS)),
        autoescape=True,
        undefined=StrictUndefined,
    )


def creer_application(
    reglages: Reglages, *, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    """L'application, prête à démarrer ; rien n'est ouvert avant son cycle de vie.

    `transport` : la doublure de l'API en mémoire (tests)."""
    metriques = Metriques()
    client = ClientApi(
        reglages.adresse_api, reglages.cle_api, reglages.delais, metriques, transport
    )
    vue = VueApi(client, reglages.limites, metriques)
    environnement = environnement_gabarits()
    gabarits = Jinja2Templates(env=environnement)
    t = reglages.textes

    @asynccontextmanager
    async def cycle(_app: FastAPI) -> AsyncGenerator[None]:
        await vue.rafraichir()
        journal.evenement(
            "demarrage",
            {
                "version": __version__,
                "etat_api": vue.etat,
                "ecoute": f"{reglages.hote}:{reglages.port}",
            },
        )
        try:
            yield
        finally:
            await client.fermer()
            journal.evenement("arret")

    def contexte(**valeurs: object) -> dict[str, object]:
        """Ce que toute page reçoit : les textes, le pied de page."""
        return {
            "t": t,
            "pied": presentation.pied_de_page(vue.etat, vue.info, reglages),
            **valeurs,
        }

    def page_erreur(statut: int, identifiant: str) -> Response:
        titre, texte = PAGES_ERREUR.get(statut, PAGES_ERREUR[500])
        contenu = environnement.get_template("erreur.html").render(
            contexte(
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
    app.add_middleware(
        Enveloppe,
        metriques=metriques,
        corps_octets_max=reglages.corps_octets_max,
        page_erreur=page_erreur,
    )

    async def erreur_http(request: Request, exc: Exception) -> Response:
        statut = exc.status_code if isinstance(exc, StarletteHTTPException) else 500
        reponse = page_erreur(statut, str(request.state.identifiant_requete))
        entetes: Mapping[str, str] | None = (
            exc.headers if isinstance(exc, StarletteHTTPException) else None
        )
        for nom, valeur in (entetes or {}).items():
            reponse.headers[nom] = valeur
        return reponse

    app.add_exception_handler(StarletteHTTPException, erreur_http)
    app.mount("/statique", StaticFiles(directory=STATIQUE), name="statique")

    def page_principale(request: Request, statut: int = 200) -> Response:
        info = vue.info
        limites = vue.limites()
        choisi = (
            None if info is None else presentation.profil_par_defaut(info, reglages)
        )
        return gabarits.TemplateResponse(
            request,
            "index.html",
            contexte(
                etat=presentation.etat_du_service(vue.etat, info, reglages),
                info_connue=info is not None,
                options=(
                    []
                    if info is None
                    else presentation.options_profils(info, reglages, choisi)
                ),
                positions=presentation.positions_proposees(info, reglages),
                temperature=reglages.temperature_defaut,
                reformuler=reglages.reformuler_defaut,
                modes=presentation.modes_proposes(info),
                question_max=limites.question_caracteres_max,
                question_aide=presentation.dire(
                    t, "question_aide", max=limites.question_caracteres_max
                ),
            ),
            status_code=statut,
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def accueil(request: Request) -> Response:
        await vue.rafraichir()
        return page_principale(request)

    @app.post("/question", response_class=HTMLResponse, include_in_schema=False)
    async def question(request: Request) -> Response:
        # Provisoire : le traitement arrive avec l'écran de la réponse.
        await vue.rafraichir()
        return gabarits.TemplateResponse(
            request,
            "erreur.html",
            contexte(
                titre=t["en_construction"],
                texte=t["en_construction"],
                identifiant=presentation.dire(
                    t,
                    "erreur_identifiant",
                    identifiant=str(request.state.identifiant_requete),
                ),
            ),
            status_code=501,
        )

    @app.get("/a-propos", response_class=HTMLResponse, include_in_schema=False)
    async def a_propos(request: Request) -> Response:
        await vue.rafraichir()
        return gabarits.TemplateResponse(
            request,
            "a_propos.html",
            contexte(
                lignes=presentation.lignes_a_propos(vue.info, reglages),
                info_connue=vue.info is not None,
            ),
        )

    @app.get("/sante", include_in_schema=False)
    async def sante() -> JSONResponse:
        """Toujours 200 : l'application est vivante, et elle sert sans l'API."""
        api_ok = await client.sante()
        metriques.api_disponible.set(1 if api_ok else 0)
        return JSONResponse(
            {
                "application": "ok",
                "api": "ok" if api_ok else "indisponible",
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

    app.state.vue = vue
    app.state.metriques = metriques
    return app
