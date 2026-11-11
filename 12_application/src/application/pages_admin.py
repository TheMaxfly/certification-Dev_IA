"""Les écrans de l'administration (E4, lot 5) : le profil IA actif (A2), l'état des clés
et leur test (A3), les coûts et la jauge du budget (A4), l'état des services (A6), et la
barre d'onglets commune à toutes les pages `/admin/*`.

**Contrôle d'accès** (OWASP A01) : chaque route exige le rôle `admin`
(`sessions.exiger_role`) ; un autre rôle reçoit 403, un visiteur la connexion. Chaque
`POST` vérifie son jeton CSRF avant tout. Aucune valeur de secret n'atteint un gabarit :
les clés de l'application restent dans `Reglages` (champs hors `repr`), et rien ici ne
les lit.

**La barre d'onglets** est construite à partir des routes enregistrées : un onglet
n'apparaît que si sa page existe dans l'application (`onglets`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, cast

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.routing import APIRoute
from python_multipart.exceptions import FormParserError
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import BaseRoute

from application import couts, journal, presentation, quotas, reglages_app, sessions
from application.base import Base, BaseIndisponible
from application.client_api import (
    Info,
    ProfilDecrit,
    RouteAbsente,
    TropTot,
    Verification,
    VueApi,
)
from application.client_catalogue import ClientCatalogue, EtatCatalogue
from application.comptes import Compte
from application.config import Reglages
from application.metriques import Metriques

type Rendre = Callable[..., Response]

Admin = Annotated[Compte, Depends(sessions.exiger_role("admin"))]

#: Les pages de l'administration, dans l'ordre de la barre : chemin → texte de l'onglet.
ONGLETS = (
    ("/admin/comptes", "onglet_comptes"),
    ("/admin/critiques", "onglet_moderation"),
    ("/admin/reglages", "onglet_reglages"),
    ("/admin/cles", "onglet_cles"),
    ("/admin/couts", "onglet_couts"),
    ("/admin/services", "onglet_services"),
    ("/admin/apparence", "onglet_apparence"),
)
#: Le dernier test des clés, gardé dans la session (le cookie signé) : un geste de
#: l'instant, jamais en base ; et l'attente annoncée par un refus de la borne.
SESSION_TEST = "verification_cles"
SESSION_ATTENTE = "verification_attente"
#: Les avis de la page des réglages, après un `POST` (motif PRG : `?etat=`).
AVIS_REGLAGES = {
    "applique": "admin_reglages_avis_applique",
    "inchange": "admin_reglages_avis_inchange",
}


@dataclass(frozen=True, slots=True)
class Onglet:
    chemin: str
    libelle: str
    courant: bool


def chemins_en_get(routes: list[BaseRoute]) -> set[str]:
    return {
        route.path
        for route in routes
        if isinstance(route, APIRoute) and "GET" in (route.methods or set[str]())
    }


def onglets(app: FastAPI, chemin: str, textes: Mapping[str, str]) -> list[Onglet]:
    """Les onglets des pages qui existent, la page courante marquée."""
    presents = chemins_en_get(app.routes)
    return [
        Onglet(cible, textes[cle], cible == chemin)
        for cible, cle in ONGLETS
        if cible in presents
    ]


def date_et_heure(moment: datetime) -> str:
    """« 9 novembre 2026 à 21:40 UTC » : un réglage, une vérification."""
    utc = moment.astimezone(UTC)
    return f"{presentation.date_en_lettres(utc)} à {utc:%H:%M} UTC"


@dataclass(frozen=True, slots=True)
class LigneProfil:
    """Un profil de `/v1/info` sur la page des réglages."""

    nom: str
    etiquette: str
    coche: bool


@dataclass(frozen=True, slots=True)
class LigneCle:
    """Un profil sur la page des clés : des noms et des états, jamais une valeur."""

    nom: str
    fournisseur: str
    modele: str
    variable: str
    demarrage: str
    test: str


#: Le délai de chaque lecture de la page des services : celui de `/sante`.
DELAI_SERVICE_S = 5.0
#: L'état d'un service → son icône textuelle (le mot suit toujours : jamais l'icône ni
#: la couleur seules).
ICONES = {"joignable": "✓", "injoignable": "✗", "non_configure": "–"}


@dataclass(frozen=True, slots=True)
class LigneService:
    """Un service de la page des services : un mot, une icône, un délai, des détails."""

    cle: str
    nom: str
    icone: str
    etat: str
    delai: str
    details: list[str]


def etiquette_profil(profil: ProfilDecrit, reglages: Reglages) -> str:
    t = reglages.textes
    fournisseur = presentation.fournisseur_de(reglages, profil.fournisseur)
    statut = t[f"admin_reglages_statut_{profil.statut}"]
    return presentation.dire(
        t,
        "admin_reglages_option",
        nom=profil.nom,
        fournisseur=fournisseur.nom,
        hebergement=fournisseur.hebergement,
        modele=profil.modele,
        statut=statut,
    )


def installer(
    app: FastAPI,
    reglages: Reglages,
    rendre: Rendre,
    vue: VueApi,
    metriques: Metriques,
    catalogue: ClientCatalogue | None,
) -> None:
    t = reglages.textes

    def base_de(request: Request) -> Base:
        return request.app.state.base

    async def formulaire(request: Request) -> FormData:
        try:
            donnees = await request.form()
        except FormParserError:
            raise StarletteHTTPException(status_code=400) from None
        valeur = donnees.get(sessions.CHAMP_JETON)
        sessions.exiger_jeton(request, valeur if isinstance(valeur, str) else "")
        return donnees

    # ------------------------------------------------------------------ #
    #  Le profil IA actif (A2)
    # ------------------------------------------------------------------ #
    async def page_reglages(
        request: Request, statut: int = 200, erreur: str | None = None
    ) -> Response:
        await vue.rafraichir()
        regle = await reglages_app.lire(base_de(request), "profil_actif")
        valeur = None if regle is None else regle.valeur
        info: Info | None = vue.info if vue.etat == "ok" else None
        avis_cle = AVIS_REGLAGES.get(request.query_params.get("etat", ""))
        avis = (
            None
            if avis_cle is None or valeur is None
            else presentation.dire(t, avis_cle, profil=valeur)
        )
        choisi_par: str | None = None
        if regle is not None:
            choisi_par = (
                presentation.dire(
                    t,
                    "admin_reglages_choisi",
                    nom=regle.nom_affiche,
                    date=date_et_heure(regle.modifie_le),
                )
                if regle.nom_affiche is not None
                else presentation.dire(
                    t,
                    "admin_reglages_choisi_anonyme",
                    date=date_et_heure(regle.modifie_le),
                )
            )
        if info is None:
            return rendre(
                request,
                "admin_reglages.html",
                503 if statut == 200 else statut,
                api_absente=True,
                avis=avis,
                erreur=erreur,
                actuel=(
                    t["admin_reglages_actuel_aucun"]
                    if valeur is None
                    else presentation.dire(t, "admin_reglages_actuel", profil=valeur)
                ),
                choisi_par=choisi_par,
                profils=[],
                indisponibles=[],
                exclus=None,
                aucun_reglage=None,
                profil_actif_indisponible=None,
            )
        proposes = presentation.profils_proposes(info, reglages)
        actif = presentation.profil_par_defaut(info, reglages, valeur)
        defaut = presentation.profil_par_defaut(info, reglages)
        indisponible = presentation.regle_indisponible(info, reglages, valeur)
        nb_exclus = sum(
            1 for p in info.modele_langage.profils if p.nom in reglages.profils_exclus
        )
        return rendre(
            request,
            "admin_reglages.html",
            statut,
            api_absente=False,
            avis=avis,
            erreur=erreur,
            actuel=None,
            choisi_par=choisi_par,
            profils=[
                LigneProfil(p.nom, etiquette_profil(p, reglages), p.nom == actif)
                for p in proposes
            ],
            indisponibles=[
                etiquette_profil(p, reglages)
                for p in info.modele_langage.profils
                if not p.disponible and p.nom not in reglages.profils_exclus
            ],
            exclus=(
                presentation.dire(t, "admin_reglages_exclus", n=nb_exclus)
                if nb_exclus
                else None
            ),
            aucun_reglage=(
                presentation.dire(t, "admin_reglages_aucun", profil=defaut)
                if valeur is None and defaut is not None
                else None
            ),
            profil_actif_indisponible=(
                presentation.dire(
                    t, "profil_actif_indisponible", profil=valeur, defaut=defaut
                )
                if indisponible
                else None
            ),
        )

    @app.get("/admin/reglages", include_in_schema=False)
    async def admin_reglages(request: Request, admin: Admin) -> Response:
        return await page_reglages(request)

    @app.post("/admin/reglages", include_in_schema=False)
    async def admin_regler(request: Request) -> Response:
        donnees = await formulaire(request)
        admin = await sessions.exiger_role("admin")(request)
        await vue.rafraichir()
        if vue.etat != "ok" or vue.info is None:
            # La liste ne peut être relue : rien n'est validé, rien n'est écrit.
            request.state.code_erreur = "reglage_api_absente"
            return await page_reglages(request, 503)
        brut = donnees.get("profil")
        profil = brut if isinstance(brut, str) else ""
        noms = [p.nom for p in presentation.profils_proposes(vue.info, reglages)]
        if profil not in noms:
            request.state.code_erreur = "reglage_refuse"
            return await page_reglages(request, 422, t["admin_reglages_erreur"])
        issue = await reglages_app.ecrire(
            base_de(request), "profil_actif", profil, admin.id
        )
        if issue == "fait":
            journal.evenement_requete(request, "profil_actif_change")
        etat = "applique" if issue == "fait" else "inchange"
        return RedirectResponse(f"/admin/reglages?etat={etat}", status_code=303)

    # ------------------------------------------------------------------ #
    #  L'état des clés et leur test (A3)
    # ------------------------------------------------------------------ #
    def texte_du_test(controle: object) -> str:
        """Le dernier test d'un profil, en mots : des états, jamais une valeur."""
        if not isinstance(controle, dict):
            return t["admin_cles_non_teste"]
        lu = cast(dict[str, object], controle)
        statut = str(lu.get("statut", ""))
        if statut == "ok":
            return t[
                "admin_cles_valide" if lu.get("disponible") else "admin_cles_modele"
            ]
        if statut == "absente":
            return t["admin_cles_test_absente"]
        if statut == "injoignable":
            return t["admin_cles_injoignable"]
        if statut.isdigit():
            return presentation.dire(t, "admin_cles_refusee", code=statut)
        return t["admin_cles_illisible"]

    def dernier_test(request: Request) -> dict[str, object] | None:
        """Le dernier test de la session (le cookie signé) : un geste de l'instant,
        jamais gardé en base."""
        brut = request.session.get(SESSION_TEST)
        return cast(dict[str, object], brut) if isinstance(brut, dict) else None

    @app.get("/admin/cles", include_in_schema=False)
    async def admin_cles(request: Request, admin: Admin) -> Response:
        await vue.rafraichir()
        info = vue.info if vue.etat == "ok" else None
        test = dernier_test(request)
        resultats = (
            cast(dict[str, object], test.get("profils", {})) if test is not None else {}
        )
        etat = request.query_params.get("etat", "")
        avis: str | None = None
        repli: str | None = None
        if etat == "teste" and test is not None:
            avis = t["admin_cles_avis_teste"]
        elif etat == "route_absente":
            repli = presentation.dire(
                t,
                "admin_cles_repli",
                version=info.version_api if info is not None else t["valeur_inconnue"],
            )
        elif etat == "trop_tot":
            attente = request.session.pop(SESSION_ATTENTE, None)
            avis = (
                presentation.dire(t, "admin_cles_trop_tot", n=attente)
                if isinstance(attente, int)
                else t["admin_cles_trop_tot_sans"]
            )
        elif etat == "indisponible":
            avis = t["admin_cles_indisponible"]
        lignes = (
            []
            if info is None
            else [
                LigneCle(
                    nom=p.nom,
                    fournisseur=presentation.fournisseur_de(
                        reglages, p.fournisseur
                    ).nom,
                    modele=p.modele,
                    variable=p.variable_cle,
                    demarrage=t[
                        "admin_cles_configuree"
                        if p.disponible
                        else "admin_cles_absente"
                    ],
                    test=texte_du_test(resultats.get(p.nom)),
                )
                for p in info.modele_langage.profils
            ]
        )
        return rendre(
            request,
            "admin_cles.html",
            200 if info is not None else 503,
            api_absente=info is None,
            lignes=lignes,
            avis=avis,
            repli=repli,
            test=(
                None
                if test is None
                else {
                    "verifie_le": str(test.get("verifie_le", "")),
                    "date": presentation.dire(
                        t,
                        "admin_cles_teste_le",
                        date=presentation.date_lisible(str(test.get("verifie_le", ""))),
                    ),
                }
            ),
            lien_procedure=reglages.administration.procedure_cles,
        )

    @app.post("/admin/cles/tester", include_in_schema=False)
    async def admin_tester_cles(request: Request) -> Response:
        await formulaire(request)
        await sessions.exiger_role("admin")(request)
        resultat = await vue.client.verification()
        if isinstance(resultat, Verification):
            request.session[SESSION_TEST] = {
                "verifie_le": resultat.verifie_le,
                "profils": {
                    c.nom: {"disponible": c.disponible, "statut": c.statut}
                    for c in resultat.profils
                },
            }
            issue = "teste"
        elif isinstance(resultat, RouteAbsente):
            issue = "route_absente"
        elif isinstance(resultat, TropTot):
            if resultat.retry_after is not None:
                request.session[SESSION_ATTENTE] = resultat.retry_after
            issue = "trop_tot"
        else:
            issue = "indisponible"
        metriques.verifications_cles.labels(issue=issue).inc()
        journal.evenement_requete(request, "verification_cles", {"issue": issue})
        return RedirectResponse(f"/admin/cles?etat={issue}", status_code=303)

    # ------------------------------------------------------------------ #
    #  Les coûts et la jauge du budget (A4)
    # ------------------------------------------------------------------ #
    regles_couts = reglages.couts

    def dollars(valeur: Decimal | float, n: int = 4) -> str:
        return f"{Decimal(str(valeur)):.{n}f}".replace(".", ",")

    def pourcentage(valeur: Decimal, maximum: Decimal) -> str:
        part = Decimal(0) if maximum <= 0 else valeur * 100 / maximum
        return f"{part:.1f}".replace(".", ",") + " %"

    @app.get("/admin/couts", include_in_schema=False)
    async def admin_couts(request: Request, admin: Admin) -> Response:
        base = base_de(request)
        jour = quotas.jour_utc(request.app.state.horloge())
        cumul = await couts.cumul_du_jour(base, jour)
        lignes = await couts.historique(base, jour, regles_couts.historique_jours)
        tous = await couts.totaux(base)
        plafond = Decimal(str(reglages.bornes.cout_jour_max_usd))
        atteint = cumul >= plafond
        budget = Decimal(str(regles_couts.budget_projet_usd))
        anterieur = Decimal(str(regles_couts.cout_anterieur_usd))
        projet = tous.total_table + anterieur
        jours: list[dict[str, object]] = []
        for le_jour in sorted({ligne.jour for ligne in lignes}, reverse=True):
            du_jour = [ligne for ligne in lignes if ligne.jour == le_jour]
            jours.append(
                {
                    "iso": le_jour.isoformat(),
                    "date": presentation.date_en_lettres(
                        datetime(le_jour.year, le_jour.month, le_jour.day, tzinfo=UTC)
                    ),
                    "lignes": [
                        {
                            "profil": ligne.profil,
                            "mode": ligne.mode,
                            "appels": ligne.appels,
                            "cout": presentation.dire(
                                t, "admin_couts_dollars", cout=dollars(ligne.cout_usd)
                            ),
                        }
                        for ligne in du_jour
                    ],
                    "appels": sum(ligne.appels for ligne in du_jour),
                    "cout": presentation.dire(
                        t,
                        "admin_couts_dollars",
                        cout=dollars(
                            sum((ligne.cout_usd for ligne in du_jour), Decimal(0))
                        ),
                    ),
                }
            )
        premier = (
            None
            if tous.premier_jour is None
            else presentation.date_en_lettres(
                datetime(
                    tous.premier_jour.year,
                    tous.premier_jour.month,
                    tous.premier_jour.day,
                    tzinfo=UTC,
                )
            )
        )

        def agregats(par: dict[str, couts.Agregat]) -> list[str]:
            return [
                presentation.dire(
                    t,
                    "admin_couts_repartition",
                    nom=nom,
                    appels=agregat.appels,
                    cout=dollars(agregat.cout_usd),
                )
                for nom, agregat in sorted(par.items())
            ]

        metriques.cout_jour.set(float(cumul))
        return rendre(
            request,
            "admin_couts.html",
            jour={
                "atteint": atteint,
                "texte": presentation.dire(
                    t,
                    "admin_couts_jour",
                    cout=dollars(cumul),
                    plafond=dollars(plafond, 2),
                    etat=t["admin_couts_atteint" if atteint else "admin_couts_ok"],
                ),
                "max": f"{plafond}",
                "valeur": f"{min(cumul, plafond)}",
                "pourcentage": pourcentage(cumul, plafond),
            },
            projet={
                "texte": presentation.dire(
                    t,
                    "admin_couts_projet",
                    total=dollars(projet),
                    budget=dollars(budget, 0),
                ),
                "termes": presentation.dire(
                    t,
                    "admin_couts_termes",
                    anterieur=dollars(anterieur),
                    table=dollars(tous.total_table),
                    appels=tous.appels,
                    avant=(
                        presentation.dire(t, "admin_couts_avant_le", date=premier)
                        if premier is not None
                        else t["admin_couts_sans_table"]
                    ),
                ),
                "max": f"{budget}",
                "valeur": f"{min(projet, budget)}",
                "pourcentage": pourcentage(projet, budget),
                "premier_jour": (
                    t["admin_couts_table_vide"]
                    if premier is None
                    else presentation.dire(t, "admin_couts_premier_jour", date=premier)
                ),
            },
            historique={
                "titre": presentation.dire(
                    t, "admin_couts_historique_titre", n=regles_couts.historique_jours
                ),
                "jours": jours,
            },
            repartition={
                "profils": agregats(tous.par_profil),
                "modes": agregats(tous.par_mode),
            },
        )

    # ------------------------------------------------------------------ #
    #  L'état des services (A6) : lu à la demande de la page, rien de stocké
    # ------------------------------------------------------------------ #
    async def mesurer[T](appel: Awaitable[T]) -> tuple[T | None, float]:
        """Un appel, borné au délai de `/sante` ; sa durée, en millisecondes."""
        debut = time.perf_counter()
        try:
            async with asyncio.timeout(DELAI_SERVICE_S):
                resultat: T | None = await appel
        except TimeoutError:
            resultat = None
        return resultat, (time.perf_counter() - debut) * 1000

    def ligne_service(
        cle: str, etat: str, delai_ms: float | None, details: list[str]
    ) -> LigneService:
        return LigneService(
            cle=cle,
            nom=t[f"admin_services_{cle}"],
            icone=ICONES[etat],
            etat=t[f"admin_services_etat_{etat}"],
            delai=(
                t["admin_services_sans_delai"]
                if delai_ms is None
                else presentation.dire(t, "admin_services_delai", ms=round(delai_ms))
            ),
            details=details,
        )

    @app.get("/admin/services", include_in_schema=False)
    async def admin_services(request: Request, admin: Admin) -> Response:
        # Le rôle admin, comme toute page d'administration : sans base, 503 « comptes
        # indisponibles » (décision de Max du 2026-11-10, écart 17 du lot 5).
        base = base_de(request)

        async def base_lue() -> str:
            return await base.sonder() if base.configuree else "non_configuree"

        async def catalogue_lu() -> EtatCatalogue | None:
            return None if catalogue is None else await catalogue.etat()

        (
            (sante, d_sante),
            (_, d_info),
            (lu_catalogue, d_catalogue),
            (lu_base, d_base),
        ) = await asyncio.gather(
            mesurer(vue.client.sante()),
            mesurer(vue.rafraichir()),
            mesurer(catalogue_lu()),
            mesurer(base_lue()),
        )
        info = vue.info if vue.etat == "ok" else None
        lignes: list[LigneService] = []
        # L'API du modèle.
        api_ok = bool(sante) and info is not None
        details_api: list[str] = []
        if info is not None:
            regle = None
            if lu_base == "ok":
                try:
                    lu = await reglages_app.lire(base, "profil_actif")
                except BaseIndisponible:
                    lu = None
                regle = None if lu is None else lu.valeur
            actif = presentation.profil_par_defaut(info, reglages, regle)
            details_api = [
                presentation.dire(
                    t, "admin_services_version", version=info.version_api
                ),
                presentation.dire(
                    t,
                    "admin_services_corpus",
                    corpus=info.corpus_id,
                    encodage=info.encodage_id,
                    modele=info.modele,
                ),
                presentation.dire(
                    t,
                    "admin_services_modele_langage",
                    profil=info.modele_langage.profil_actif,
                ),
                presentation.dire(
                    t,
                    "admin_services_profil_actif",
                    profil=actif or t["valeur_inconnue"],
                    etat=t[
                        "admin_services_profil_indisponible"
                        if presentation.regle_indisponible(info, reglages, regle)
                        else (
                            "admin_services_profil_regle"
                            if regle is not None
                            else "admin_services_profil_defaut"
                        )
                    ],
                ),
            ]
        lignes.append(
            ligne_service(
                "api",
                "joignable" if api_ok else "injoignable",
                max(d_sante, d_info),
                details_api,
            )
        )
        # Le catalogue (l'API data du 02).
        if catalogue is None:
            lignes.append(ligne_service("catalogue", "non_configure", None, []))
        else:
            details_catalogue = (
                []
                if lu_catalogue is None or lu_catalogue.normalisation is None
                else [
                    presentation.dire(
                        t,
                        "admin_services_normalisation",
                        normalisation=lu_catalogue.normalisation,
                    )
                ]
            )
            joignable = lu_catalogue is not None and lu_catalogue.ok
            if lu_catalogue is not None and not lu_catalogue.ok:
                details_catalogue.append(t["admin_services_catalogue_degrade"])
            lignes.append(
                ligne_service(
                    "catalogue",
                    "joignable" if joignable else "injoignable",
                    d_catalogue,
                    details_catalogue,
                )
            )
        # La base de l'application.
        etat_base = {
            "ok": "joignable",
            "non_configuree": "non_configure",
        }.get(lu_base or "indisponible", "injoignable")
        lignes.append(
            ligne_service(
                "base", etat_base, None if etat_base == "non_configure" else d_base, []
            )
        )
        # Les clés d'API de l'application : un état de configuration, sans appel.
        deux = reglages.cle_api_comptes is not None
        lignes.append(
            LigneService(
                cle="cles",
                nom=t["admin_services_cles"],
                icone=ICONES["joignable" if deux else "non_configure"],
                etat=t[
                    "admin_services_cles_deux" if deux else "admin_services_cles_une"
                ],
                delai=t["admin_services_sans_delai"],
                details=[] if deux else [t["admin_services_cles_repli"]],
            )
        )
        manquants = [
            ligne.nom for ligne in lignes if ligne.icone == ICONES["injoignable"]
        ]
        maintenant = datetime.fromtimestamp(request.app.state.horloge(), UTC)
        return rendre(
            request,
            "admin_services.html",
            lignes=lignes,
            manquants=(
                presentation.dire(
                    t, "admin_services_encart", services=", ".join(manquants)
                )
                if manquants
                else None
            ),
            lu_iso=maintenant.strftime("%Y-%m-%dT%H:%M:%SZ"),
            lu_a=presentation.dire(
                t, "admin_services_heure", heure=maintenant.strftime("%H:%M:%S")
            ),
            grafana=reglages.monitorage.adresse_grafana,
        )
