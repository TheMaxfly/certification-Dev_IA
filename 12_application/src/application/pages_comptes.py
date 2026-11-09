"""Les écrans des comptes (E4, lot 1, spec 03) : inscription, connexion, déconnexion,
changement de mot de passe, administration des comptes. Les critiques du libraire ont
leurs écrans à elles (`pages_critiques`, E4, lot 3).

Pages rendues côté serveur, sans JavaScript ; chaque formulaire `POST` porte le jeton
CSRF, vérifié avant toute autre chose ; chaque page de compte sonde la base d'abord :
sans elle, l'encart « comptes indisponibles » (503), le conseiller reste accessible. Les
messages ne portent aucune valeur saisie, sauf le pseudo rendu à son propre champ.
Le journal ne porte que des identifiants : jamais le pseudo, le mot de passe, son
empreinte, le cookie ni le jeton.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from python_multipart.exceptions import FormParserError
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException as StarletteHTTPException

from application import comptes, journal, presentation, quotas, sessions
from application.base import Base
from application.comptes import Compte, PseudoPris, Refus
from application.config import Reglages
from application.motdepasse import Hacheur

type Rendre = Callable[..., Response]
type RoleAdministre = Literal["libraire", "admin"]

Connecte = Annotated[Compte, Depends(sessions.exiger_connexion)]
Admin = Annotated[Compte, Depends(sessions.exiger_role("admin"))]

ROLES = ("client", "libraire", "admin")
ROLES_ADMINISTRES: tuple[RoleAdministre, ...] = ("libraire", "admin")
#: Les avis de la page d'administration, après une action (motif PRG : `?etat=`).
AVIS_ADMIN = {
    "cree": "admin_cree",
    "desactive": "admin_desactive",
    "reactive": "admin_reactive",
    "soi": "admin_soi",
    "inchange": "admin_inchange",
    "inconnu": "admin_inconnu",
}
#: Un identifiant de compte dans un chemin : des chiffres, assez peu pour un `bigint`.
CHIFFRES_MAX = 18


def installer(app: FastAPI, reglages: Reglages, rendre: Rendre) -> None:
    t = reglages.textes
    regles = reglages.comptes
    bornes_pseudo = {"min": regles.pseudo_min, "max": regles.pseudo_max}
    bornes_mdp = {"min": regles.mot_de_passe_min, "max": regles.mot_de_passe_max}
    libelles_roles = {role: t[f"compte_role_{role}"] for role in ROLES}

    def base_de(request: Request) -> Base:
        return request.app.state.base

    def hacheur_de(request: Request) -> Hacheur:
        return request.app.state.hacheur

    async def disponibles(request: Request) -> None:
        if await base_de(request).sonder() != "ok":
            raise sessions.ComptesIndisponibles

    def champ(donnees: FormData, nom: str) -> str:
        valeur = donnees.get(nom)
        return valeur if isinstance(valeur, str) else ""

    async def formulaire(request: Request) -> FormData:
        """Le corps du formulaire, son jeton vérifié d'abord."""
        try:
            donnees = await request.form()
        except FormParserError:
            raise StarletteHTTPException(status_code=400) from None
        sessions.exiger_jeton(request, champ(donnees, sessions.CHAMP_JETON))
        return donnees

    def erreur_pseudo(pseudo: str) -> str | None:
        if comptes.FORME_PSEUDO.fullmatch(pseudo) and (
            regles.pseudo_min <= len(pseudo) <= regles.pseudo_max
        ):
            return None
        return presentation.dire(t, "erreur_pseudo_forme", **bornes_pseudo)

    def erreurs_mot_de_passe(
        mot_de_passe: str, confirmation: str, champ_mdp: str
    ) -> dict[str, str]:
        if not regles.mot_de_passe_min <= len(mot_de_passe) <= regles.mot_de_passe_max:
            return {
                champ_mdp: presentation.dire(t, "erreur_mdp_longueur", **bornes_mdp)
            }
        if confirmation != mot_de_passe:
            return {"confirmation": t["erreur_confirmation"]}
        return {}

    def aides() -> dict[str, str]:
        return {
            "aide_pseudo": presentation.dire(t, "aide_pseudo", **bornes_pseudo),
            "aide_mot_de_passe": presentation.dire(
                t, "aide_mot_de_passe", **bornes_mdp
            ),
        }

    def premier(erreurs: dict[str, str], ordre: tuple[str, ...]) -> str | None:
        return next((nom for nom in ordre if nom in erreurs), None)

    def rendu(pseudo: str) -> str:
        """Le pseudo rendu à son champ : imprimable, borné."""
        return comptes.affichable(pseudo, regles.pseudo_max)

    # ------------------------------------------------------------------ #
    #  Inscription
    # ------------------------------------------------------------------ #
    def page_inscription(
        request: Request,
        retour: str,
        statut: int = 200,
        erreurs: dict[str, str] | None = None,
        valeurs: dict[str, str] | None = None,
        bornee: str | None = None,
    ) -> Response:
        erreurs = erreurs or {}
        return rendre(
            request,
            "inscription.html",
            statut,
            bornee=bornee,
            retour=retour,
            suffixe_retour=sessions.suffixe_retour(retour),
            erreurs=erreurs,
            valeurs=valeurs or {},
            focus=premier(erreurs, ("pseudo", "mot_de_passe", "confirmation")),
            comptes=regles,
            **aides(),
        )

    @app.get("/inscription", include_in_schema=False)
    async def inscription(request: Request) -> Response:
        await disponibles(request)
        if await sessions.page(request) is not None:
            raise sessions.Redirection("/")
        retour = sessions.retour_local(request.query_params.get("retour"))
        return page_inscription(request, retour)

    bornes = reglages.bornes
    fenetre_inscriptions = bornes.inscriptions_fenetre_min * 60

    def inscriptions_epuisees(request: Request, instant: float) -> int | None:
        """Les secondes avant qu'une inscription de plus tienne dans la fenêtre de la
        session ; None : elle tient (E4, lot 2b, décision 7)."""
        gardes = quotas.horodatages(
            request.session,
            quotas.CLE_INSCRIPTIONS,
            instant,
            fenetre_inscriptions,
            bornes.inscriptions_session_max,
        )
        return quotas.attente(
            gardes, instant, fenetre_inscriptions, bornes.inscriptions_session_max
        )

    @app.post("/inscription", include_in_schema=False)
    async def inscrire(request: Request) -> Response:
        donnees = await formulaire(request)
        await disponibles(request)
        retour = sessions.retour_local(champ(donnees, "retour"))
        instant = request.app.state.horloge()
        attente = inscriptions_epuisees(request, instant)
        if attente is not None:
            request.state.code_erreur = "inscription_bornee"
            request.app.state.metriques.quota_refus.labels(motif="inscription").inc()
            heure = quotas.heure_locale(datetime.fromtimestamp(instant + attente, UTC))
            reponse = page_inscription(
                request,
                retour,
                429,
                valeurs={"pseudo": rendu(comptes.normaliser(champ(donnees, "pseudo")))},
                bornee=presentation.dire(
                    t,
                    "inscription_bornee",
                    n=bornes.inscriptions_session_max,
                    heure=heure,
                ),
            )
            reponse.headers["Retry-After"] = str(attente)
            return reponse
        pseudo = comptes.normaliser(champ(donnees, "pseudo"))
        mot_de_passe = champ(donnees, "mot_de_passe")
        erreurs: dict[str, str] = {}
        if (erreur := erreur_pseudo(pseudo)) is not None:
            erreurs["pseudo"] = erreur
        erreurs |= erreurs_mot_de_passe(
            mot_de_passe, champ(donnees, "confirmation"), "mot_de_passe"
        )
        if not erreurs:
            try:
                compte = await comptes.creer(
                    base_de(request),
                    hacheur_de(request),
                    pseudo=pseudo,
                    # Aucune autre donnée que le pseudo : il sert de nom affiché.
                    nom_affiche=pseudo,
                    role="client",
                    mot_de_passe=mot_de_passe,
                    doit_changer_mdp=False,
                    par_compte_id="soi",
                )
            except PseudoPris:
                erreurs["pseudo"] = t["erreur_pseudo_pris"]
            else:
                sessions.ouvrir(request, compte)
                quotas.noter(
                    request.session,
                    quotas.CLE_INSCRIPTIONS,
                    instant,
                    fenetre_inscriptions,
                    bornes.inscriptions_session_max,
                )
                journal.evenement_requete(request, "inscription")
                return RedirectResponse(retour, status_code=303)
        request.state.code_erreur = "inscription_refusee"
        valeurs = {"pseudo": rendu(pseudo)}
        return page_inscription(request, retour, 422, erreurs, valeurs)

    # ------------------------------------------------------------------ #
    #  Connexion, déconnexion
    # ------------------------------------------------------------------ #
    def page_connexion(
        request: Request,
        retour: str,
        statut: int = 200,
        refus: bool = False,
        pseudo: str = "",
    ) -> Response:
        # Le refus et sa phrase fixe, la même dans tous les cas (inconnu, mauvais mot
        # de passe, verrouillé, désactivé) : les règles, rien sur le compte.
        texte_refus = " ".join(
            (
                t["connexion_refus"],
                presentation.dire(
                    t,
                    "connexion_refus_regles",
                    seuil=regles.echecs_avant_verrou,
                    minutes=-(-regles.duree_verrou_s // 60),
                ),
            )
        )
        return rendre(
            request,
            "connexion.html",
            statut,
            retour=retour,
            suffixe_retour=sessions.suffixe_retour(retour),
            refus=refus,
            texte_refus=texte_refus,
            valeurs={"pseudo": pseudo} if pseudo else {},
            comptes=regles,
        )

    @app.get("/connexion", include_in_schema=False)
    async def connexion(request: Request) -> Response:
        await disponibles(request)
        if await sessions.page(request) is not None:
            raise sessions.Redirection("/")
        retour = sessions.retour_local(request.query_params.get("retour"))
        return page_connexion(request, retour)

    @app.post("/connexion", include_in_schema=False)
    async def connecter(request: Request) -> Response:
        donnees = await formulaire(request)
        await disponibles(request)
        retour = sessions.retour_local(champ(donnees, "retour"))
        pseudo = comptes.normaliser(champ(donnees, "pseudo"))
        resultat = await comptes.authentifier(
            base_de(request),
            hacheur_de(request),
            pseudo=pseudo,
            mot_de_passe=champ(donnees, "mot_de_passe"),
            seuil=regles.echecs_avant_verrou,
            duree_verrou_s=regles.duree_verrou_s,
        )
        if isinstance(resultat, Refus):
            ident = {"compte_id": resultat.compte_id} if resultat.compte_id else {}
            journal.evenement_requete(
                request, "connexion_refusee", {"motif": resultat.motif, **ident}
            )
            if resultat.verrou_pose:
                journal.evenement_requete(request, "verrou_pose", ident)
            request.state.code_erreur = "connexion_refusee"
            # Le même message, le même statut, quel que soit le motif.
            return page_connexion(
                request, retour, 401, refus=True, pseudo=rendu(pseudo)
            )
        sessions.ouvrir(request, resultat)
        journal.evenement_requete(request, "connexion_reussie")
        if resultat.doit_changer_mdp:
            raise sessions.Redirection("/compte/mot-de-passe")
        return RedirectResponse(retour, status_code=303)

    @app.post("/deconnexion", include_in_schema=False)
    async def deconnecter(request: Request) -> Response:
        await formulaire(request)
        compte = await sessions.compte_courant(request)
        if compte is not None:
            # La génération + 1 : toute copie du cookie de ce compte tombe aussi (au
            # prix de fermer ses autres sessions).
            await comptes.couper_sessions(base_de(request), compte.id)
            journal.evenement_requete(request, "deconnexion")
        sessions.fermer(request)
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------------ #
    #  Le mot de passe
    # ------------------------------------------------------------------ #
    def page_mot_de_passe(
        request: Request,
        compte: Compte,
        statut: int = 200,
        erreurs: dict[str, str] | None = None,
        avis: str | None = None,
        reussi: bool = False,
    ) -> Response:
        erreurs = erreurs or {}
        if avis is None and compte.doit_changer_mdp:
            avis = t["mdp_a_changer"]
        focus = premier(erreurs, ("ancien", "nouveau", "confirmation"))
        return rendre(
            request,
            "mot_de_passe.html",
            statut,
            erreurs=erreurs,
            avis=avis,
            # Sans erreur, le focus va à l'avis : le lecteur d'écran le lit d'abord.
            focus=focus if focus or avis is None else "avis",
            # Changé : plus de formulaire, l'encart et le lien pour continuer.
            reussi=reussi,
            comptes=regles,
            **aides(),
        )

    @app.get("/compte/mot-de-passe", include_in_schema=False)
    async def mot_de_passe(request: Request, compte: Connecte) -> Response:
        return page_mot_de_passe(request, compte)

    @app.post("/compte/mot-de-passe", include_in_schema=False)
    async def changer_mot_de_passe(request: Request) -> Response:
        donnees = await formulaire(request)
        compte = await sessions.exiger_connexion(request)
        nouveau = champ(donnees, "nouveau")
        erreurs = erreurs_mot_de_passe(
            nouveau, champ(donnees, "confirmation"), "nouveau"
        )
        if not erreurs:
            change = await comptes.changer_mot_de_passe(
                base_de(request),
                hacheur_de(request),
                compte.id,
                champ(donnees, "ancien"),
                nouveau,
                seuil=regles.echecs_avant_verrou,
                duree_verrou_s=regles.duree_verrou_s,
            )
            if isinstance(change, Compte):
                # La session courante, rouverte à la nouvelle génération ; les autres
                # tombent à leur prochaine requête.
                sessions.ouvrir(request, change)
                journal.evenement_requete(request, "mot_de_passe_change")
                return page_mot_de_passe(
                    request, change, avis=t["mdp_change"], reussi=True
                )
            journal.evenement_requete(
                request, "mot_de_passe_refuse", {"motif": change.motif}
            )
            if change.verrou_pose:
                journal.evenement_requete(request, "verrou_pose")
            erreurs = {
                "ancien": presentation.dire(
                    t, "mdp_verrou", minutes=-(-regles.duree_verrou_s // 60)
                )
                if change.motif == "verrou"
                else t["mdp_ancien_faux"]
            }
        request.state.code_erreur = "mot_de_passe_refuse"
        return page_mot_de_passe(request, compte, 422, erreurs)

    # ------------------------------------------------------------------ #
    #  L'administration des comptes
    # ------------------------------------------------------------------ #
    async def page_admin(
        request: Request,
        admin: Compte,
        statut: int = 200,
        erreurs: dict[str, str] | None = None,
        valeurs: dict[str, str] | None = None,
    ) -> Response:
        erreurs = erreurs or {}
        filtre = request.query_params.get("role")
        filtre = filtre if filtre in ROLES else None
        demande = request.query_params.get("page", "1")
        demande = demande if demande.isascii() and demande.isdigit() else "1"
        lignes, total, page = await comptes.lister(
            base_de(request),
            role=filtre,
            page=int(demande[:CHIFFRES_MAX]),
            par_page=regles.par_page,
        )
        pages = max(1, -(-total // regles.par_page))
        avis_cle = AVIS_ADMIN.get(request.query_params.get("etat", ""))
        focus = premier(
            erreurs, ("pseudo", "nom_affiche", "role", "mot_de_passe", "confirmation")
        )
        return rendre(
            request,
            "admin_comptes.html",
            statut,
            erreurs=erreurs,
            valeurs=valeurs or {},
            focus=focus if focus or avis_cle is None else "avis",
            avis=None if avis_cle is None else t[avis_cle],
            lignes=lignes,
            filtre=filtre,
            page=page,
            pages=pages,
            page_texte=presentation.dire(t, "admin_page", n=page, total=pages),
            roles=ROLES,
            roles_administres=ROLES_ADMINISTRES,
            libelles_roles=libelles_roles,
            comptes=regles,
            **aides(),
        )

    @app.get("/admin/comptes", include_in_schema=False)
    async def admin_comptes(request: Request, admin: Admin) -> Response:
        return await page_admin(request, admin)

    @app.post("/admin/comptes", include_in_schema=False)
    async def admin_creer(request: Request) -> Response:
        donnees = await formulaire(request)
        admin = await sessions.exiger_role("admin")(request)
        pseudo = comptes.normaliser(champ(donnees, "pseudo"))
        nom = champ(donnees, "nom_affiche").strip()
        mot_de_passe = champ(donnees, "mot_de_passe")
        erreurs: dict[str, str] = {}
        if (erreur := erreur_pseudo(pseudo)) is not None:
            erreurs["pseudo"] = erreur
        if not comptes.nom_valide(nom, regles.nom_affiche_max):
            erreurs["nom_affiche"] = presentation.dire(
                t, "erreur_nom_longueur", max=regles.nom_affiche_max
            )
        role: RoleAdministre | None = next(
            (r for r in ROLES_ADMINISTRES if r == champ(donnees, "role")), None
        )
        if role is None:
            erreurs["role"] = t["erreur_role"]
        erreurs |= erreurs_mot_de_passe(
            mot_de_passe, champ(donnees, "confirmation"), "mot_de_passe"
        )
        if not erreurs and role is not None:
            try:
                await comptes.creer(
                    base_de(request),
                    hacheur_de(request),
                    pseudo=pseudo,
                    nom_affiche=nom,
                    role=role,
                    mot_de_passe=mot_de_passe,
                    doit_changer_mdp=True,
                    par_compte_id=admin.id,
                )
            except PseudoPris:
                erreurs["pseudo"] = t["erreur_pseudo_pris"]
            else:
                return RedirectResponse("/admin/comptes?etat=cree", status_code=303)
        request.state.code_erreur = "creation_refusee"
        valeurs = {
            "pseudo": rendu(pseudo),
            "nom_affiche": comptes.affichable(nom, regles.nom_affiche_max),
            "role": role or "",
        }
        return await page_admin(request, admin, 422, erreurs, valeurs)

    async def admin_changer(request: Request, chemin: str, *, actif: bool) -> Response:
        # Le jeton, puis le rôle, puis seulement l'identifiant : un anonyme ou un
        # formulaire forgé n'apprend rien de la forme attendue.
        await formulaire(request)
        admin = await sessions.exiger_role("admin")(request)
        if not (chemin.isascii() and chemin.isdigit() and len(chemin) <= CHIFFRES_MAX):
            raise StarletteHTTPException(status_code=404)
        ident = int(chemin)
        if ident == admin.id:
            return RedirectResponse("/admin/comptes?etat=soi", status_code=303)
        issue = await comptes.changer_etat(
            base_de(request), ident, actif=actif, par_compte_id=admin.id
        )
        etat = {"fait": "reactive" if actif else "desactive"}.get(issue, issue)
        return RedirectResponse(f"/admin/comptes?etat={etat}", status_code=303)

    @app.post("/admin/comptes/{ident}/desactiver", include_in_schema=False)
    async def admin_desactiver(request: Request, ident: str) -> Response:
        return await admin_changer(request, ident, actif=False)

    @app.post("/admin/comptes/{ident}/reactiver", include_in_schema=False)
    async def admin_reactiver(request: Request, ident: str) -> Response:
        return await admin_changer(request, ident, actif=True)
