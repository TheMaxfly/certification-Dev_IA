"""Les écrans des critiques signées (E4, lot 3, specs 02 et 03) : écrire, modifier,
retirer, « Mes critiques », la modération de l'administrateur, et la section « Les
libraires du réseau » de la fiche de série.

Même construction que `pages_comptes` : pages rendues côté serveur, sans JavaScript ;
le jeton CSRF d'abord, puis le rôle, puis seulement l'identifiant du chemin ; PRG par
`?etat=` ; les erreurs au champ, le focus au premier champ fautif. Le titre des séries
se lit dans le catalogue (le 02) : absent, la liste s'affiche avec « titre
indisponible », jamais un 503 sur une page de compte. Le texte d'une critique est une
donnée : il ne passe jamais par `dire()`, n'entre jamais au journal, et le gabarit
l'échappe.

**Qui** : un libraire écrit, modifie et retire les siennes (`/libraire/critiques*`) ; un
administrateur retire n'importe laquelle (`/admin/critiques*`) et n'en écrit aucune ; un
client ou un visiteur n'a aucun bouton, et 403 ou la connexion sur toute route.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from python_multipart.exceptions import FormParserError
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException as StarletteHTTPException

from application import critiques, journal, presentation, sessions
from application.base import Base, BaseIndisponible
from application.client_catalogue import (
    CatalogueIndisponible,
    ClientCatalogue,
    CorpsInattendu,
    SerieCatalogue,
    Volume,
    libelles_de,
)
from application.comptes import Compte
from application.config import Reglages
from application.critiques import (
    Critique,
    CritiqueIntrouvable,
    CritiqueInvalide,
    DejaPubliee,
    PasLAuteur,
)
from application.metriques import Metriques
from application.sessions import AccesRefuse

type Rendre = Callable[..., Response]

Ecrivain = Annotated[Compte, Depends(sessions.exiger_role("libraire"))]
Moderateur = Annotated[Compte, Depends(sessions.exiger_role("admin"))]

#: Un identifiant dans un chemin : des chiffres, assez peu pour un `bigint`.
CHIFFRES_MAX = 18
#: Le début du texte montré à la modération, en caractères.
DEBUT_MAX = 120
STATUTS = ("publiee", "retiree")
AVIS_LISTE = {
    "publiee": "critiques_avis_publiee",
    "modifiee": "critiques_avis_modifiee",
    "retiree": "critiques_avis_retiree",
    "deja_retiree": "critiques_avis_deja_retiree",
}
AVIS_ADMIN = {
    "retiree": "critiques_avis_retiree",
    "deja_retiree": "critiques_avis_deja_retiree",
}
#: L'ordre des champs du formulaire : le focus va au premier en erreur.
ORDRE = ("volume_url", "texte", "note", "label")
TOME = re.compile(r"(Tome \d+)(?: — (.+))?", re.DOTALL)
#: Les notes proposées : 0 à 10, aucune présélectionnée.
NOTES = [(str(n), str(n)) for n in range(11)]


def milliers(n: int) -> str:
    """Un nombre à la française : l'espace fine insécable des milliers."""
    return f"{n:,}".replace(",", " ")


@dataclass(frozen=True, slots=True)
class Affichee:
    """Une critique prête à afficher (fiche, confirmation, modération)."""

    id: int
    nom: str
    date: str
    tome: str | None
    tome_detail: str | None
    note: str
    label: str | None
    modifiee: str | None
    paragraphes: list[list[str]]
    lien_modifier: str | None
    lien_moderer: str | None
    #: Les noms des liens : deux critiques d'une page ne se confondent pas.
    etiquette_modifier: str
    etiquette_moderer: str


@dataclass(frozen=True, slots=True)
class Reseau:
    """La section « Les libraires du réseau » d'une fiche, et la ligne de sa note."""

    indisponible: bool
    critiques: list[Affichee]
    moyenne: str | None
    ecrire: str | None


@dataclass(frozen=True, slots=True)
class Ligne:
    """Une ligne de « Mes critiques » ou de la modération."""

    id: int
    series_id: int
    titre: str
    tome: str
    note: str
    label: str
    statut: str
    publiee: bool
    date: str
    modifiee: str | None
    libraire: str
    debut: str


@dataclass(frozen=True, slots=True)
class Pagination:
    page: int
    pages: int
    texte: str
    precedente: str | None
    suivante: str | None


def tome_de(libelle: str | None) -> tuple[str | None, str | None]:
    """« Tome 3 » et le reste du libellé (« — titre ») ; un libellé sans numéro se
    montre entier."""
    if libelle is None:
        return None, None
    trouve = TOME.fullmatch(libelle)
    if trouve is None:
        return libelle, None
    return trouve.group(1), trouve.group(2)


def affichee(critique: Critique, reglages: Reglages, compte: Compte | None) -> Affichee:
    t = reglages.textes
    tome, detail = tome_de(critique.volume_libelle)
    auteur = (
        compte is not None
        and compte.role == "libraire"
        and compte.id == critique.compte_id
    )
    cible = critique.volume_libelle or t["critiques_serie_entiere"]
    return Affichee(
        id=critique.id,
        nom=critique.nom_affiche,
        date=presentation.dire(
            t, "critique_date", date=presentation.date_en_lettres(critique.publie_le)
        ),
        tome=tome,
        tome_detail=detail,
        note=presentation.dire(t, "critique_note", note=critique.note),
        label=reglages.critiques.libelle(critique.label),
        modifiee=(
            presentation.dire(
                t,
                "critique_modifiee_le",
                date=presentation.date_en_lettres(critique.modifie_le),
            )
            if critique.modifiee
            else None
        ),
        paragraphes=presentation.paragraphes_critique(critique.texte),
        lien_modifier=f"/libraire/critiques/{critique.id}/modifier" if auteur else None,
        lien_moderer=(
            f"/admin/critiques/{critique.id}/retirer"
            if compte is not None and compte.role == "admin"
            else None
        ),
        etiquette_modifier=presentation.dire(t, "critique_lien_modifier", serie=cible),
        etiquette_moderer=presentation.dire(
            t, "critique_lien_moderer", nom=f"{critique.nom_affiche} ({cible})"
        ),
    )


async def reseau_de(
    base: Base, reglages: Reglages, compte: Compte | None, series_id: int
) -> Reseau:
    """Les critiques publiées de la série, sa moyenne ; la base perdue : l'encart."""
    t = reglages.textes
    try:
        publiees = await critiques.publiees_de_la_serie(base, series_id)
        moyenne = await critiques.moyenne_de_la_serie(base, series_id)
    except BaseIndisponible:
        return Reseau(indisponible=True, critiques=[], moyenne=None, ecrire=None)
    if moyenne is None:
        ligne = t["serie_reseau_sans_avis"]
    elif moyenne.nombre == 1:
        ligne = presentation.dire(
            t,
            "serie_reseau_moyenne_une",
            note=presentation.decimales(moyenne.note, t, 1),
        )
    else:
        ligne = presentation.dire(
            t,
            "serie_reseau_moyenne",
            note=presentation.decimales(moyenne.note, t, 1),
            n=milliers(moyenne.nombre),
        )
    return Reseau(
        indisponible=False,
        critiques=[affichee(c, reglages, compte) for c in publiees],
        moyenne=ligne,
        ecrire=(
            f"/libraire/critiques/nouvelle?series_id={series_id}"
            if compte is not None and compte.role == "libraire"
            else None
        ),
    )


def installer(
    app: FastAPI,
    reglages: Reglages,
    rendre: Rendre,
    client: ClientCatalogue | None,
    metriques: Metriques,
) -> None:
    t = reglages.textes
    regles = reglages.critiques
    bornes = presentation.dire(
        t,
        "critique_bornes",
        min=milliers(regles.texte_min),
        max=milliers(regles.texte_max),
    )
    labels = [(label.code, label.libelle) for label in regles.labels]
    statuts = {
        "publiee": t["critiques_statut_publiee"],
        "retiree": t["critiques_statut_retiree"],
    }

    def base_de(request: Request) -> Base:
        return request.app.state.base

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

    def identifiant(chemin: str) -> int:
        if not (chemin.isascii() and chemin.isdigit() and len(chemin) <= CHIFFRES_MAX):
            raise StarletteHTTPException(status_code=404)
        return int(chemin)

    def refuser(request: Request) -> AccesRefuse:
        """Connecté, avec le rôle, mais pas l'auteur : 403, journalisé comme la
        garde."""
        route = getattr(request.scope.get("route"), "path", None)
        journal.evenement_requete(request, "acces_refuse", {"route": route})
        return AccesRefuse()

    def lire_page(request: Request) -> int:
        demande = request.query_params.get("page", "1")
        demande = demande if demande.isascii() and demande.isdigit() else "1"
        return int(demande[:CHIFFRES_MAX])

    def lire_note(brute: str) -> int | None:
        return (
            int(brute)
            if brute.isascii() and brute.isdigit() and len(brute) <= 2
            else None
        )

    def page_nommee(request: Request, statut: int, titre: str, texte: str) -> Response:
        return rendre(
            request,
            "erreur.html",
            statut,
            titre=t[titre],
            texte=t[texte],
            identifiant=presentation.dire(
                t, "erreur_identifiant", identifiant=request.state.identifiant_requete
            ),
        )

    def catalogue_indisponible(request: Request) -> Response:
        request.state.code_erreur = "catalogue_indisponible"
        return page_nommee(request, 503, "catalogue_encart_titre", "catalogue_encart")

    def critique_retiree(request: Request) -> Response:
        request.state.code_erreur = "critique_retiree"
        return page_nommee(
            request, 404, "critique_retiree_titre", "critique_retiree_texte"
        )

    def compter(action: str) -> None:
        metriques.critiques.labels(action=action).inc()

    async def titres_de(ids: Iterable[int]) -> tuple[dict[int, str], bool]:
        """Le titre de chaque série (un appel par série distincte) ; le catalogue
        perdu : « titre indisponible » pour le reste, et l'encart."""
        titres: dict[int, str] = {}
        for series_id in sorted(set(ids)):
            if client is None:
                return titres, True
            try:
                fiche = await client.serie(series_id)
            except (CatalogueIndisponible, CorpsInattendu):
                return titres, True
            if fiche is not None and fiche.title:
                titres[series_id] = fiche.title
        return titres, False

    def ligne_de(critique: Critique, titres: dict[int, str]) -> Ligne:
        texte = critique.texte.replace("\n", " ")
        return Ligne(
            id=critique.id,
            series_id=critique.series_id,
            titre=titres.get(critique.series_id, t["critiques_titre_indisponible"]),
            tome=critique.volume_libelle or t["critiques_serie_entiere"],
            note=presentation.dire(t, "critique_note", note=critique.note),
            label=regles.libelle(critique.label) or t["demo_absent"],
            statut=statuts.get(critique.statut, critique.statut),
            publiee=critique.publiee,
            date=presentation.dire(
                t,
                "critique_date",
                date=presentation.date_en_lettres(critique.publie_le),
            ),
            modifiee=(
                presentation.dire(
                    t,
                    "critique_modifiee_le",
                    date=presentation.date_en_lettres(critique.modifie_le),
                )
                if critique.modifiee
                else None
            ),
            libraire=critique.nom_affiche,
            debut=texte
            if len(texte) <= DEBUT_MAX
            else texte[:DEBUT_MAX].rstrip() + "…",
        )

    def pagination_de(
        chemin: str, page: int, total: int, filtres: dict[str, str]
    ) -> Pagination | None:
        pages = max(1, -(-total // regles.par_page))
        if pages == 1:
            return None

        def lien(n: int) -> str:
            return f"{chemin}?" + urlencode({**filtres, "page": n})

        return Pagination(
            page=page,
            pages=pages,
            texte=presentation.dire(t, "critiques_page", n=page, total=pages),
            precedente=lien(page - 1) if page > 1 else None,
            suivante=lien(page + 1) if page < pages else None,
        )

    def messages(fautes: dict[str, str], texte: str) -> dict[str, str]:
        """Les motifs des règles → les messages au champ."""
        dits: dict[str, str] = {}
        for nom, motif in fautes.items():
            if nom == "texte" and motif == "longueur":
                dits[nom] = presentation.dire(
                    t,
                    "critique_erreur_longueur",
                    min=milliers(regles.texte_min),
                    max=milliers(regles.texte_max),
                    n=milliers(len(texte)),
                )
            elif nom == "texte":
                dits[nom] = t["critique_erreur_controle"]
            elif nom == "note":
                dits[nom] = t["critique_erreur_note"]
            elif nom == "label":
                dits[nom] = t["critique_erreur_label"]
            elif nom == "volume_url":
                dits[nom] = t["critique_erreur_tome"]
            else:
                # Un refus de la base que les règles n'ont pas vu : dit au texte.
                dits["texte"] = t["critique_erreur_base"]
        return dits

    # ------------------------------------------------------------------ #
    #  Le formulaire : écrire et modifier
    # ------------------------------------------------------------------ #
    async def serie_et_volumes(
        request: Request, series_id: int
    ) -> tuple[SerieCatalogue, list[Volume]] | Response:
        if client is None:
            return catalogue_indisponible(request)
        try:
            fiche = await client.serie(series_id)
            if fiche is None:
                request.state.code_erreur = "serie_inconnue"
                return page_nommee(
                    request,
                    404,
                    "critique_serie_inconnue_titre",
                    "critique_serie_inconnue",
                )
            volumes = await client.volumes(series_id)
        except (CatalogueIndisponible, CorpsInattendu):
            return catalogue_indisponible(request)
        return fiche, volumes or []

    def page_formulaire(
        request: Request,
        *,
        titre: str,
        series_id: int,
        statut: int = 200,
        volumes: dict[str, str] | None = None,
        critique: Critique | None = None,
        erreurs: dict[str, str] | None = None,
        valeurs: dict[str, str] | None = None,
        existante: int | None = None,
        deja: int | None = None,
    ) -> Response:
        """Un seul gabarit pour écrire et modifier ; `critique` : la modification (la
        série et le tome affichés, non modifiables)."""
        erreurs = erreurs or {}
        if critique is not None and not valeurs:
            valeurs = {
                "texte": critique.texte,
                "note": str(critique.note),
                "label": critique.label or "",
            }
        return rendre(
            request,
            "critique_formulaire.html",
            statut,
            modification=critique is not None,
            titre_page=t[
                "critique_modifier_titre" if critique else "critique_nouvelle_titre"
            ],
            titre_serie=titre,
            series_id=series_id,
            action=(
                f"/libraire/critiques/{critique.id}/modifier"
                if critique is not None
                else "/libraire/critiques/nouvelle"
            ),
            tome_fixe=(
                (critique.volume_libelle or t["critique_tome_serie"])
                if critique is not None
                else None
            ),
            volumes=list((volumes or {}).items()),
            labels=labels,
            notes=NOTES,
            bornes=bornes,
            regles=regles,
            erreurs=erreurs,
            valeurs=valeurs or {},
            focus=next((nom for nom in ORDRE if nom in erreurs), None),
            existante=existante,
            deja=deja,
        )

    def lire_saisie(
        donnees: FormData,
    ) -> tuple[str, int | None, str | None, dict[str, str]]:
        texte = critiques.normaliser_texte(champ(donnees, "texte"))
        note = lire_note(champ(donnees, "note"))
        label = champ(donnees, "label") or None
        valeurs = {
            "texte": texte,
            "note": "" if note is None else str(note),
            "label": label or "",
        }
        return texte, note, label, valeurs

    @app.get("/libraire/critiques/nouvelle", include_in_schema=False)
    async def nouvelle(request: Request, libraire: Ecrivain) -> Response:
        series_id = identifiant(request.query_params.get("series_id", ""))
        lu = await serie_et_volumes(request, series_id)
        if isinstance(lu, Response):
            return lu
        fiche, volumes = lu
        existante = await critiques.publiee_existante(
            base_de(request), libraire.id, series_id, None
        )
        return page_formulaire(
            request,
            titre=fiche.title or t["valeur_inconnue"],
            series_id=series_id,
            volumes=libelles_de(volumes),
            existante=existante,
        )

    @app.post("/libraire/critiques/nouvelle", include_in_schema=False)
    async def publier(request: Request) -> Response:
        donnees = await formulaire(request)
        libraire = await sessions.exiger_role("libraire")(request)
        series_id = identifiant(champ(donnees, "series_id"))
        lu = await serie_et_volumes(request, series_id)
        if isinstance(lu, Response):
            return lu
        fiche, volumes = lu
        libelles = libelles_de(volumes)
        volume_url = champ(donnees, "volume_url") or None
        texte, note, label, valeurs = lire_saisie(donnees)
        valeurs["volume_url"] = volume_url or ""
        fautes = messages(critiques.erreurs(regles, texte, note, label), texte)
        if volume_url is not None and volume_url not in libelles:
            fautes["volume_url"] = t["critique_erreur_tome"]
            valeurs["volume_url"] = ""
        deja: int | None = None
        if not fautes and note is not None:
            try:
                ident = await critiques.publier(
                    base_de(request),
                    libraire,
                    series_id,
                    volume_url,
                    None if volume_url is None else libelles[volume_url],
                    texte,
                    note,
                    label,
                    regles=regles,
                )
            except DejaPubliee as exc:
                fautes["volume_url"] = t["critique_deja_publiee"]
                deja = exc.ident
            except CritiqueInvalide as exc:
                fautes |= messages({exc.champ: exc.motif}, texte)
            else:
                compter("publiee")
                journal.evenement_requete(
                    request,
                    "critique_publiee",
                    {"critique_id": ident, "series_id": series_id},
                )
                return RedirectResponse(
                    f"/series/{series_id}?etat=critique_publiee", status_code=303
                )
        request.state.code_erreur = "critique_refusee"
        return page_formulaire(
            request,
            titre=fiche.title or t["valeur_inconnue"],
            series_id=series_id,
            statut=422,
            volumes=libelles,
            erreurs=fautes,
            valeurs=valeurs,
            deja=deja,
        )

    async def la_sienne(request: Request, libraire: Compte, chemin: str) -> Critique:
        """La critique du chemin, à ce libraire : 404 si elle n'existe pas, 403 si elle
        est à un autre."""
        critique = await critiques.lire(base_de(request), identifiant(chemin))
        if critique is None:
            raise StarletteHTTPException(status_code=404)
        if critique.compte_id != libraire.id:
            raise refuser(request)
        return critique

    async def titre_seul(series_id: int) -> str:
        titres, _ = await titres_de((series_id,))
        return titres.get(series_id, t["critiques_titre_indisponible"])

    @app.get("/libraire/critiques/{chemin}/modifier", include_in_schema=False)
    async def modifier_page(
        request: Request, chemin: str, libraire: Ecrivain
    ) -> Response:
        critique = await la_sienne(request, libraire, chemin)
        if not critique.publiee:
            return critique_retiree(request)
        return page_formulaire(
            request,
            titre=await titre_seul(critique.series_id),
            series_id=critique.series_id,
            critique=critique,
        )

    @app.post("/libraire/critiques/{chemin}/modifier", include_in_schema=False)
    async def modifier(request: Request, chemin: str) -> Response:
        donnees = await formulaire(request)
        libraire = await sessions.exiger_role("libraire")(request)
        critique = await la_sienne(request, libraire, chemin)
        if not critique.publiee:
            return critique_retiree(request)
        texte, note, label, valeurs = lire_saisie(donnees)
        fautes = messages(critiques.erreurs(regles, texte, note, label), texte)
        if not fautes and note is not None:
            try:
                await critiques.modifier(
                    base_de(request),
                    libraire,
                    critique.id,
                    texte,
                    note,
                    label,
                    regles=regles,
                )
            except PasLAuteur:
                raise refuser(request) from None
            except CritiqueIntrouvable:
                return critique_retiree(request)
            except CritiqueInvalide as exc:
                fautes |= messages({exc.champ: exc.motif}, texte)
            else:
                compter("modifiee")
                journal.evenement_requete(
                    request,
                    "critique_modifiee",
                    {"critique_id": critique.id, "series_id": critique.series_id},
                )
                return RedirectResponse(
                    "/libraire/critiques?etat=modifiee", status_code=303
                )
        request.state.code_erreur = "critique_refusee"
        return page_formulaire(
            request,
            titre=await titre_seul(critique.series_id),
            series_id=critique.series_id,
            statut=422,
            critique=critique,
            erreurs=fautes,
            valeurs=valeurs,
        )

    # ------------------------------------------------------------------ #
    #  Retirer : l'auteur, l'administrateur
    # ------------------------------------------------------------------ #
    @app.get("/libraire/critiques/{chemin}/retirer", include_in_schema=False)
    async def retirer_page(
        request: Request, chemin: str, libraire: Ecrivain
    ) -> Response:
        critique = await la_sienne(request, libraire, chemin)
        if not critique.publiee:
            return RedirectResponse(
                "/libraire/critiques?etat=deja_retiree", status_code=303
            )
        titres, _ = await titres_de((critique.series_id,))
        return rendre(
            request,
            "critique_retirer.html",
            ligne=ligne_de(critique, titres),
            action=f"/libraire/critiques/{critique.id}/retirer",
            annuler="/libraire/critiques",
        )

    async def retirer_par(
        request: Request, chemin: str, compte: Compte, en_tant_que: critiques.EnTantQue
    ) -> Response:
        retour = (
            "/libraire/critiques" if en_tant_que == "auteur" else "/admin/critiques"
        )
        ident = identifiant(chemin)
        try:
            issue = await critiques.retirer(
                base_de(request), compte, ident, en_tant_que
            )
        except PasLAuteur:
            raise refuser(request) from None
        except CritiqueIntrouvable:
            raise StarletteHTTPException(status_code=404) from None
        if issue == "fait":
            compter("retiree")
            journal.evenement_requete(
                request,
                "critique_retiree",
                {"critique_id": ident, "par": en_tant_que},
            )
            return RedirectResponse(f"{retour}?etat=retiree", status_code=303)
        return RedirectResponse(f"{retour}?etat=deja_retiree", status_code=303)

    @app.post("/libraire/critiques/{chemin}/retirer", include_in_schema=False)
    async def retirer(request: Request, chemin: str) -> Response:
        await formulaire(request)
        libraire = await sessions.exiger_role("libraire")(request)
        return await retirer_par(request, chemin, libraire, "auteur")

    @app.get("/admin/critiques/{chemin}/retirer", include_in_schema=False)
    async def moderer_page(
        request: Request, chemin: str, admin: Moderateur
    ) -> Response:
        critique = await critiques.lire(base_de(request), identifiant(chemin))
        if critique is None:
            raise StarletteHTTPException(status_code=404)
        if not critique.publiee:
            return RedirectResponse(
                "/admin/critiques?etat=deja_retiree", status_code=303
            )
        titres, _ = await titres_de((critique.series_id,))
        return rendre(
            request,
            "admin_critique_retirer.html",
            critique=affichee(critique, reglages, None),
            ligne=ligne_de(critique, titres),
            action=f"/admin/critiques/{critique.id}/retirer",
        )

    @app.post("/admin/critiques/{chemin}/retirer", include_in_schema=False)
    async def moderer(request: Request, chemin: str) -> Response:
        await formulaire(request)
        admin = await sessions.exiger_role("admin")(request)
        return await retirer_par(request, chemin, admin, "admin")

    # ------------------------------------------------------------------ #
    #  Les listes : « Mes critiques », la modération
    # ------------------------------------------------------------------ #
    @app.get("/libraire/critiques", include_in_schema=False)
    async def mes_critiques(request: Request, libraire: Ecrivain) -> Response:
        brut = request.query_params.get("statut", "")
        filtre = brut if brut in STATUTS else None
        lignes, total, page = await critiques.les_miennes(
            base_de(request),
            libraire.id,
            lire_page(request),
            par_page=regles.par_page,
            statut=filtre,
        )
        titres, indisponible = await titres_de(c.series_id for c in lignes)
        avis_cle = AVIS_LISTE.get(request.query_params.get("etat", ""))
        return rendre(
            request,
            "critiques.html",
            lignes=[ligne_de(c, titres) for c in lignes],
            filtre=filtre,
            statuts=statuts,
            pagination=pagination_de(
                "/libraire/critiques", page, total, {"statut": filtre} if filtre else {}
            ),
            avis=None if avis_cle is None else t[avis_cle],
            focus="avis" if avis_cle is not None else None,
            indisponible=indisponible,
        )

    @app.get("/admin/critiques", include_in_schema=False)
    async def moderation(request: Request, admin: Moderateur) -> Response:
        brut = request.query_params.get("libraire", "")
        filtre = (
            int(brut)
            if brut.isascii() and brut.isdigit() and len(brut) <= CHIFFRES_MAX
            else None
        )
        base = base_de(request)
        lignes, total, page = await critiques.toutes_publiees(
            base, lire_page(request), par_page=regles.par_page, compte_id=filtre
        )
        titres, indisponible = await titres_de(c.series_id for c in lignes)
        avis_cle = AVIS_ADMIN.get(request.query_params.get("etat", ""))
        return rendre(
            request,
            "admin_critiques.html",
            lignes=[ligne_de(c, titres) for c in lignes],
            libraires=await critiques.libraires(base),
            filtre=filtre,
            pagination=pagination_de(
                "/admin/critiques",
                page,
                total,
                {"libraire": str(filtre)} if filtre is not None else {},
            ),
            avis=None if avis_cle is None else t[avis_cle],
            focus="avis" if avis_cle is not None else None,
            indisponible=indisponible,
        )
