"""Les écrans de l'administration (E4, lot 5) : le profil IA actif (A2) et la barre
d'onglets commune à toutes les pages `/admin/*`.

**Contrôle d'accès** (OWASP A01) : chaque route exige le rôle `admin`
(`sessions.exiger_role`) ; un autre rôle reçoit 403, un visiteur la connexion. Chaque
`POST` vérifie son jeton CSRF avant tout. Aucune valeur de secret n'atteint un gabarit :
les clés de l'application restent dans `Reglages` (champs hors `repr`), et rien ici ne
les lit.

**La barre d'onglets** est construite à partir des routes enregistrées : un onglet
n'apparaît que si sa page existe dans l'application (`onglets`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.routing import APIRoute
from python_multipart.exceptions import FormParserError
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import BaseRoute

from application import journal, presentation, reglages_app, sessions
from application.base import Base
from application.client_api import Info, ProfilDecrit, VueApi
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
)
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
