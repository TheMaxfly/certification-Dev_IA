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
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
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
    couts,
    historique,
    journal,
    pages_admin,
    pages_catalogue,
    pages_comptes,
    pages_critiques,
    pages_listes,
    presentation,
    quotas,
    reglages_app,
    sessions,
)
from application.base import Base, BaseIndisponible
from application.client_api import (
    ApiDelai,
    ApiInjoignable,
    ClientApi,
    ErreurApi,
    ReglagesReponseAppliques,
    Reponse,
    VueApi,
)
from application.client_catalogue import (
    CatalogueIndisponible,
    ClientCatalogue,
    CorpsInattendu,
)
from application.comptes import Compte
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
#: Un identifiant de série dans `/?serie=` : des chiffres, assez peu pour un `bigint`.
CHIFFRES_SERIE = 18
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
class Droits:
    """Ce que l'écran du conseiller propose selon le rôle (E4, lot 2b, décisions 1 et
    2) : le public — visiteur, client — n'a ni réglages, ni case, ni liste des modes ;
    le libraire a les réglages et la liste ; l'administrateur, en plus, la case du mode
    démonstration."""

    regler: bool = False
    demontrer: bool = False
    choisir_mode: bool = False


#: Le rôle → ses droits sur l'écran ; un rôle absent (visiteur, client) : aucun.
DROITS = {
    "libraire": Droits(regler=True, choisir_mode=True),
    "admin": Droits(regler=True, demontrer=True, choisir_mode=True),
}


def droits_de(compte: Compte | None) -> Droits:
    return DROITS.get(compte.role, Droits()) if compte is not None else Droits()


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
    horloge: Callable[[], float] | None = None,
) -> FastAPI:
    """L'application, prête à démarrer ; rien n'est ouvert avant son cycle de vie.

    `transport` : la doublure de l'API en mémoire (tests), qui sert aussi le catalogue
    sauf `transport_catalogue` ; `hacheur` : un moteur Argon2id aux paramètres réduits
    (tests) — la production prend les défauts ; `horloge` : l'instant, en secondes
    depuis l'époque (tests : une horloge qu'on avance) — `time.time` sinon."""
    maintenant = horloge if horloge is not None else time.time
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
    bornes = reglages.bornes
    #: Le plafond global de débit de la clé publique (un processus : README).
    limiteur = quotas.Limiteur(bornes.debit_public_par_minute, maintenant)
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
        if reglages.cle_api_comptes is None:
            # Repli (décision 5) : la clé publique sert aussi aux comptes, qui passent
            # alors sous le plafond de débit des visiteurs.
            journal.evenement("cle_comptes_absente", niveau=logging.WARNING)
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
            # La barre de l'administration (E4, lot 5) : ses pages seulement.
            "onglets": (
                pages_admin.onglets(request.app, request.url.path, t)
                if request is not None
                and compte is not None
                and compte.role == "admin"
                and request.url.path.startswith("/admin/")
                else []
            ),
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

    async def profil_regle(request: Request) -> str | None:
        """Le profil actif réglé par l'administrateur (E4, lot 5, D12), relu en base à
        chaque requête — une fois par requête (la page qui suit le reprend de
        `request.state`), aucun cache au-delà. None : aucun réglage, ou la base ne
        répond pas (la question part avec le défaut de l'API : décision 11 du lot 1).
        Un réglage qui n'est plus proposé se journalise ; la jauge du profil actif suit
        ce qui part."""
        regle: str | None = None
        base: Base = request.app.state.base
        if base.configuree:
            try:
                lu = await reglages_app.lire(base, "profil_actif")
            except BaseIndisponible:
                journal.evenement_requete(request, "profil_actif_non_lu")
            else:
                regle = None if lu is None else lu.valeur
        request.state.profil_regle = regle
        info = vue.info
        if info is not None:
            if presentation.regle_indisponible(info, reglages, regle):
                journal.evenement_requete(
                    request, "profil_actif_indisponible", {"profil": regle}
                )
            metriques.profil_actif(
                presentation.profil_par_defaut(info, reglages, regle),
                [p.nom for p in info.modele_langage.profils],
            )
        return regle

    def page_principale(
        request: Request,
        statut: int = 200,
        *,
        formulaire: Formulaire | None = None,
        tours: list[historique.Tour] | None = None,
        affichage: presentation.Affichage | None = None,
        encart: presentation.Encart | None = None,
        cloture: str | None = None,
        avis: str | None = None,
        zones_cartes: Mapping[int, pages_listes.Zone] | None = None,
    ) -> Response:
        info = vue.info
        limites = vue.limites()
        f = formulaire or Formulaire()
        compte = getattr(request.state, "compte", None)
        droits = droits_de(compte)
        regle: str | None = getattr(request.state, "profil_regle", None)
        # L'encart du profil actif devenu indisponible : une session admin seulement.
        indisponible = (
            compte is not None
            and compte.role == "admin"
            and info is not None
            and presentation.regle_indisponible(info, reglages, regle)
        )
        reponse = gabarits.TemplateResponse(
            request,
            "index.html",
            contexte(
                request,
                peut_regler=droits.regler,
                peut_demontrer=droits.demontrer,
                peut_choisir_mode=droits.choisir_mode,
                etat=presentation.etat_du_service(vue.etat, info, reglages, regle),
                info_connue=info is not None,
                options=(
                    []
                    if info is None
                    else presentation.options_profils(info, reglages, f.profil, regle)
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
                avis=avis,
                zones_cartes=zones_cartes or {},
                profil_actif_indisponible=(
                    presentation.dire(
                        t,
                        "profil_actif_indisponible",
                        profil=regle,
                        defaut=presentation.profil_par_defaut(info, reglages),
                    )
                    if indisponible and info is not None
                    else None
                ),
            ),
            status_code=statut,
        )
        if encart is not None and encart.retry_after is not None:
            reponse.headers["Retry-After"] = str(encart.retry_after)
        return reponse

    async def question_pre_remplie(request: Request) -> str:
        """`/?serie=<id>` (le pont depuis la fiche, E4, lot 2b, décision 6) : la
        question porte le titre exact du catalogue, lu dans le 02 — jamais un texte de
        l'adresse.
        Un identifiant illisible, une série inconnue, un catalogue absent ou en panne :
        la page sans pré-remplissage, sans erreur (le conseiller ne dépend pas du
        catalogue)."""
        brut = request.query_params.get("serie", "")
        if catalogue is None or not (
            brut.isascii() and brut.isdigit() and len(brut) <= CHIFFRES_SERIE
        ):
            return ""
        try:
            fiche = await catalogue.serie(int(brut))
        except (CatalogueIndisponible, CorpsInattendu):
            return ""
        if fiche is None or not fiche.title:
            return ""
        return presentation.dire(t, "question_pre_remplie", titre=fiche.title)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def accueil(request: Request) -> Response:
        await sessions.page(request)
        await vue.rafraichir()
        await profil_regle(request)
        question = await question_pre_remplie(request)
        # E4, lot 4 : après la suppression du compte, l'avis à l'accueil (PRG).
        supprime = request.query_params.get("etat") == "compte_supprime"
        return page_principale(
            request,
            formulaire=Formulaire(question=question),
            avis=t["effacement_avis"] if supprime else None,
        )

    def lire_formulaire(
        donnees: FormData, compte: Compte | None, regle: str | None = None
    ) -> tuple[Formulaire, str | None, str | None]:
        """Les champs, et l'erreur de formulaire : (formulaire, champ, message).

        Ce que le rôle n'a pas sur l'écran n'existe pas pour lui (E4, lot 2b) : un
        visiteur ou un client ne règle ni le profil, ni la température, ni la
        reformulation, ni le mode, ni le mode démonstration — un champ forgé dans son
        `POST` est ignoré, sans erreur ; la demande part avec les défauts, en strict. Un
        libraire n'a pas le mode démonstration. Le profil par défaut est le profil actif
        de l'application (`regle`, E4, lot 5) s'il est proposé, celui de l'API sinon."""
        limites = vue.limites()
        droits = droits_de(compte)
        question = champ(donnees, "question").replace("\r\n", "\n").strip()
        profil = champ(donnees, "profil") if droits.regler else ""
        brute = champ(donnees, "temperature") if droits.regler else ""
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
            reformuler=(
                "reformuler" in donnees
                if droits.regler
                else presentation.reformuler_par_defaut(vue.info, reglages)
            ),
            mode_demonstration=droits.demontrer and "mode_demonstration" in donnees,
            mode=(champ(donnees, "mode") or None) if droits.choisir_mode else None,
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
            profil = (
                profil
                or presentation.profil_par_defaut(vue.info, reglages, regle)
                or ""
            )
            lu = replace(lu, profil=profil or None)
            if profil not in noms:
                return lu, "profil", t["erreur_profil"]
        return lu, None, None

    def cle_de(compte: Compte | None) -> str | None:
        """La clé de l'appel : celle des comptes pour un compte connecté, si elle
        existe ; None, la publique (celle du client)."""
        if compte is not None and reglages.cle_api_comptes is not None:
            return reglages.cle_api_comptes
        return None

    def borne(motif: str, encart: presentation.Encart) -> presentation.Encart:
        """Un refus par une borne : sa métrique, son code au journal des requêtes."""
        metriques.quota_refus.labels(motif=motif).inc()
        return encart

    async def borne_atteinte(
        request: Request, compte: Compte | None, instant: float
    ) -> tuple[presentation.Encart, str] | None:
        """Les bornes, dans l'ordre : quota de la session (visiteur) ou du compte
        (client), débit de la clé publique, budget du jour. Le premier dépassement :
        son encart et son issue ; None, la question part."""
        base: Base = request.app.state.base
        jour = quotas.jour_utc(instant)
        if compte is None:
            plafond = bornes.quota_visiteur_questions_jour
            gardes = quotas.horodatages_du_jour(
                request.session, quotas.CLE_QUOTA, instant, plafond
            )
            if len(gardes) >= plafond:
                minuit = quotas.minuit_suivant(instant)
                texte = presentation.dire(
                    t,
                    "quota_visiteur",
                    n=plafond,
                    heure=quotas.heure_locale(minuit),
                    client=bornes.quota_client_questions_jour,
                )
                encart = presentation.Encart(
                    texte,
                    429,
                    quotas.secondes_jusqu_a(minuit, instant),
                    titre=t["quota_titre"],
                )
                return borne("session", encart), "quota"
        elif compte.role == "client":
            posees = await quotas.questions_posees(base, compte.id, jour)
            if posees >= bornes.quota_client_questions_jour:
                minuit = quotas.minuit_suivant(instant)
                texte = presentation.dire(
                    t,
                    "quota_client",
                    n=bornes.quota_client_questions_jour,
                    heure=quotas.heure_locale(minuit),
                )
                encart = presentation.Encart(
                    texte,
                    429,
                    quotas.secondes_jusqu_a(minuit, instant),
                    titre=t["quota_titre"],
                )
                return borne("client", encart), "quota"
        if cle_de(compte) is None:
            attente = limiteur.admettre()
            if attente is not None:
                encart = presentation.Encart(
                    t["debit_public"], 429, attente, titre=t["debit_titre"]
                )
                return borne("debit", encart), "debit"
        try:
            cumul = await couts.cumul_du_jour(base, jour)
        except BaseIndisponible:
            # Non vérifiable : le conseiller répond quand même (décision 11 du lot 1).
            metriques.etat_plafond("inconnu")
            journal.evenement_requete(request, "plafond_non_verifiable")
            return None
        metriques.cout_jour.set(float(cumul))
        if cumul >= Decimal(str(bornes.cout_jour_max_usd)):
            metriques.etat_plafond("atteint")
            minuit = quotas.minuit_suivant(instant)
            encart = presentation.Encart(
                t["budget_atteint"],
                503,
                quotas.secondes_jusqu_a(minuit, instant),
                titre=t["budget_titre"],
            )
            return borne("cout", encart), "plafond_cout"
        metriques.etat_plafond("ok")
        return None

    async def compter(
        request: Request,
        compte: Compte | None,
        instant: float,
        corps: Reponse,
        mode: str,
    ) -> None:
        """Après une réponse (200) seulement : la question au quota de la session ou du
        compte, le coût reçu au jour, sous le profil et le mode rendus par l'API."""
        base: Base = request.app.state.base
        jour = quotas.jour_utc(instant)
        if compte is None:
            plafond = bornes.quota_visiteur_questions_jour
            quotas.noter_du_jour(request.session, quotas.CLE_QUOTA, instant, plafond)
        elif compte.role == "client":
            try:
                await quotas.compter_question(base, compte.id, jour)
            except BaseIndisponible:
                journal.evenement_requete(request, "quota_non_compte")
        contexte_api = corps.contexte
        cout = contexte_api.cout_estime_usd
        if cout is None:
            journal.evenement_requete(request, "cout_absent")
        rendu = (contexte_api.reglages_appliques or ReglagesReponseAppliques()).mode
        if rendu not in couts.MODES:
            rendu = mode
        try:
            await couts.ajouter(base, jour, contexte_api.profil, rendu, cout or 0.0)
        except BaseIndisponible:
            journal.evenement_requete(request, "cout_non_compte")

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
        compte = await sessions.page(request)
        await vue.rafraichir()
        regle = await profil_regle(request)
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

        lu, fautif, message = lire_formulaire(donnees, compte, regle)
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
        # Le public n'a pas de mode (`lire_formulaire`) : le défaut du contrat, strict.
        mode = (
            lu.mode
            if lu.mode in reglages_reponse.mode.valeurs
            else reglages_reponse.mode.defaut
        )
        instant = maintenant()
        atteinte = await borne_atteinte(request, compte, instant)
        if atteinte is not None:
            encart, issue = atteinte
            request.state.code_erreur = f"borne_{issue}"
            metriques.question(mode, issue)
            return page_principale(
                request, encart.statut, formulaire=lu, tours=tours, encart=encart
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
            resultat = await client.reponse(demande, cle=cle_de(compte))
        except (ApiInjoignable, ApiDelai) as exc:
            metriques.question(mode, "api_indisponible")
            delai = isinstance(exc, ApiDelai)
            request.state.code_erreur = "api_delai" if delai else "api_injoignable"
            encart = presentation.encart_transport(delai, reglages)
            return page_principale(
                request, encart.statut, formulaire=lu, tours=tours, encart=encart
            )
        except ErreurApi as exc:
            metriques.question(mode, "api_erreur")
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
        metriques.question(mode, "refus" if corps.refus else "reponse")
        await compter(request, compte, instant, corps, mode)
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
        affichage = presentation.affichage_de(
            lu.question,
            corps,
            reglages,
            demonstration=bool(lu.mode_demonstration),
            complement=droits_de(compte).choisir_mode,
        )
        # E4, lot 4 (CL4) : le geste sur les listes, sur chaque carte, pour un client.
        zones = await pages_listes.zones_des_cartes(
            request.app.state.base,
            t,
            compte,
            ((carte.serie_id, carte.titre) for carte in affichage.cartes),
        )
        return page_principale(
            request,
            formulaire=f,
            tours=tours,
            affichage=affichage,
            cloture=cloture,
            zones_cartes=zones,
        )

    @app.get("/a-propos", response_class=HTMLResponse, include_in_schema=False)
    async def a_propos(request: Request) -> Response:
        await sessions.page(request)
        await vue.rafraichir()
        regle = await profil_regle(request)
        return gabarits.TemplateResponse(
            request,
            "a_propos.html",
            contexte(
                request,
                lignes=presentation.lignes_a_propos(vue.info, reglages, regle),
                info_connue=vue.info is not None,
                apropos_comptes=presentation.dire(
                    t,
                    "apropos_comptes",
                    seuil=reglages.comptes.echecs_avant_verrou,
                    minutes=-(-reglages.comptes.duree_verrou_s // 60),
                ),
                apropos_quotas=presentation.dire(
                    t,
                    "apropos_quotas",
                    visiteur=bornes.quota_visiteur_questions_jour,
                    client=bornes.quota_client_questions_jour,
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
        etat_base = await base.sonder() if base.configuree else base.etat
        # Le profil actif de l'application (E4, lot 5) : celui que l'administrateur a
        # réglé, s'il s'applique ; `defaut` sinon (aucun réglage, base absente, ou un
        # profil que le dernier `/v1/info` ne propose plus).
        regle = await profil_regle(request) if etat_base == "ok" else None
        applique = regle is not None and not presentation.regle_indisponible(
            vue.info, reglages, regle
        )
        return JSONResponse(
            {
                "application": "ok",
                "api": "ok" if api_ok else "indisponible",
                "base": etat_base,
                "catalogue": etat_catalogue,
                "cles_api": "une" if reglages.cle_api_comptes is None else "deux",
                "profil_actif": regle if applique else "defaut",
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
    pages_critiques.installer(app, reglages, rendre, catalogue, metriques)
    pages_listes.installer(app, reglages, rendre, catalogue, metriques)
    pages_admin.installer(app, reglages, rendre, vue, metriques, catalogue)

    app.state.vue = vue
    app.state.metriques = metriques
    app.state.base = base
    app.state.hacheur = hacheur if hacheur is not None else Hacheur()
    app.state.reglages = reglages
    app.state.horloge = maintenant
    return app
