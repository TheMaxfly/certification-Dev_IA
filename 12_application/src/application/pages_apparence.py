"""L'écran « Apparence » de l'administration (E4, lot 7, story AP1) : la couleur de
chacun des six espaces, l'intensité du lavis et la matière, choisies dans des listes
fermées ; « Appliquer » recharge la page, qui se rend avec l'apparence réglée — c'est
l'aperçu, sans JavaScript.

**Contrôle d'accès** (OWASP A01) : chaque route exige le rôle `admin` ; un autre rôle
reçoit 403, un visiteur la connexion ; chaque `POST` vérifie son jeton CSRF avant tout.
**Liste blanche côté serveur** : toute valeur hors liste → 422, rien n'est écrit ; les
huit clés s'écrivent en une transaction, sans ligne de journal (décision 5 : l'apparence
n'est ni une donnée ni un droit ; `modifie_par` et `modifie_le` disent qui et quand).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from python_multipart.exceptions import FormParserError
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException as StarletteHTTPException

from application import apparence, presentation, reglages_app, sessions
from application.base import Base
from application.comptes import Compte
from application.config import Reglages
from application.pages_admin import date_et_heure

type Rendre = Callable[..., Response]

Admin = Annotated[Compte, Depends(sessions.exiger_role("admin"))]

#: Les avis de la page, après un `POST` (motif PRG : `?etat=`).
AVIS = {
    "appliquee": "admin_apparence_avis_appliquee",
    "inchangee": "admin_apparence_avis_inchangee",
    "origine": "admin_apparence_avis_origine",
}
#: Le nombre d'espaces qui partagent une couleur → son texte (2 à 6).
NOMBRES = {n: f"admin_apparence_nombre_{n}" for n in range(2, 7)}
#: Le champ de l'intensité et celui de la matière (les couleurs : `apparence_<espace>`).
CHAMP_INTENSITE = apparence.CLE_INTENSITE
CHAMP_MATIERE = apparence.CLE_MATIERE


@dataclass(frozen=True, slots=True)
class Choix:
    """Un bouton radio : sa valeur, son libellé, coché ou non ; la touche de la couleur
    (une pastille décorative), pour les couleurs seulement."""

    valeur: str
    libelle: str
    coche: bool
    touche: str | None = None


@dataclass(frozen=True, slots=True)
class Groupe:
    """Un `fieldset` du formulaire : son champ, sa légende, ses choix, son erreur."""

    champ: str
    legende: str
    choix: list[Choix]
    erreur: str | None = None


def lire_champs(donnees: FormData) -> tuple[dict[str, str], list[str]]:
    """Les huit champs validés contre leur liste ; et les champs fautifs (absents ou
    hors liste), dans l'ordre de l'écran."""
    valeurs: dict[str, str] = {}
    fautifs: list[str] = []
    for cle in apparence.CLES:
        valide = apparence.valider(cle, donnees.get(cle))
        if valide is None:
            fautifs.append(cle)
        else:
            valeurs[cle] = valide
    return valeurs, fautifs


def installer(app: FastAPI, reglages: Reglages, rendre: Rendre) -> None:
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

    def choisi_par(lignes: Mapping[str, reglages_app.Valeur]) -> str:
        """Qui et quand : la plus récente des huit clés ; « apparence d'origine » si
        aucune n'a jamais été posée."""
        if not lignes:
            return t["admin_apparence_origine_jamais"]
        recente = max(lignes.values(), key=lambda v: v.modifie_le)
        date = date_et_heure(recente.modifie_le)
        if recente.nom_affiche is None:
            return presentation.dire(t, "admin_reglages_choisi_anonyme", date=date)
        return presentation.dire(
            t, "admin_reglages_choisi", nom=recente.nom_affiche, date=date
        )

    def avertissements(reglee: apparence.Apparence) -> list[str]:
        return [
            presentation.dire(
                t,
                "admin_apparence_partage",
                nombre=t[NOMBRES[len(espaces)]],
                couleur=t[f"couleur_{couleur}"],
                espaces=", ".join(t[f"espace_{e}"] for e in espaces),
            )
            for couleur, espaces in apparence.partages(reglee)
        ]

    def groupes(reglee: apparence.Apparence, fautifs: list[str]) -> list[Groupe]:
        erreur = t["admin_apparence_erreur"]
        resultat = [
            Groupe(
                apparence.CLE_ESPACE[espace],
                t[f"espace_{espace}"],
                [
                    Choix(
                        couleur,
                        t[f"couleur_{couleur}"],
                        couleur == reglee.couleurs[espace],
                        apparence.touche(couleur),
                    )
                    for couleur in apparence.COULEURS
                ],
                erreur if apparence.CLE_ESPACE[espace] in fautifs else None,
            )
            for espace in apparence.ESPACES
        ]
        resultat.append(
            Groupe(
                CHAMP_INTENSITE,
                t["admin_apparence_intensite_legende"],
                [
                    Choix(
                        n,
                        presentation.dire(t, "admin_apparence_intensite", n=n),
                        n == reglee.intensite,
                    )
                    for n in apparence.INTENSITES
                ],
                erreur if CHAMP_INTENSITE in fautifs else None,
            )
        )
        resultat.append(
            Groupe(
                CHAMP_MATIERE,
                t["admin_apparence_matiere_legende"],
                [
                    Choix(m, t[f"matiere_{m}"], m == reglee.matiere)
                    for m in apparence.MATIERES
                ],
                erreur if CHAMP_MATIERE in fautifs else None,
            )
        )
        return resultat

    def page(
        request: Request, statut: int = 200, fautifs: list[str] | None = None
    ) -> Response:
        reglee: apparence.Apparence = request.state.apparence
        lignes: Mapping[str, reglages_app.Valeur] = request.state.reglages_apparence
        avis_cle = AVIS.get(request.query_params.get("etat", ""))
        return rendre(
            request,
            "admin_apparence.html",
            statut,
            avis=None if avis_cle is None or fautifs else t[avis_cle],
            groupes=groupes(reglee, fautifs or []),
            choisi_par=choisi_par(lignes),
            avertissements=avertissements(reglee),
            premier_fautif=fautifs[0] if fautifs else "",
        )

    @app.get("/admin/apparence", include_in_schema=False)
    async def admin_apparence(request: Request, admin: Admin) -> Response:
        return page(request)

    @app.post("/admin/apparence", include_in_schema=False)
    async def admin_appliquer(request: Request) -> Response:
        donnees = await formulaire(request)
        admin = await sessions.exiger_role("admin")(request)
        valeurs, fautifs = lire_champs(donnees)
        if fautifs:
            # Rien n'est écrit ; le code au journal des requêtes, jamais la valeur.
            request.state.code_erreur = "apparence_refusee"
            return page(request, 422, fautifs)
        issue = await reglages_app.ecrire_plusieurs(base_de(request), valeurs, admin.id)
        etat = "appliquee" if issue == "fait" else "inchangee"
        return RedirectResponse(f"/admin/apparence?etat={etat}", status_code=303)

    @app.post("/admin/apparence/origine", include_in_schema=False)
    async def admin_origine(request: Request) -> Response:
        await formulaire(request)
        admin = await sessions.exiger_role("admin")(request)
        # Des valeurs explicites, égales au défaut (décision 8) : `app_rw` n'a pas de
        # `DELETE` sur la table, et une valeur posée se lit comme un choix.
        await reglages_app.ecrire_plusieurs(
            base_de(request), apparence.DEFAUT.valeurs(), admin.id
        )
        return RedirectResponse("/admin/apparence?etat=origine", status_code=303)
