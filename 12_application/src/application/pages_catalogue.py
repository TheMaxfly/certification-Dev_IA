"""Les pages du catalogue (E4, lot 2a, spec 02) : la recherche (`/catalogue`) et la
fiche d'une série (`/series/{series_id}`), servies depuis l'API data du module 02.

Pages publiques, rendues côté serveur, sans JavaScript ; elles gardent le menu du
compte connecté. La ligne de titre de la fiche passe par `metadonnees_de`, comme les
cartes du conseiller : les mêmes valeurs partout. Le catalogue absent, injoignable, en
délai, ou qui refuse la clé : un encart nommé et un 503, le conseiller et les comptes
continuent. Une série inconnue : la page introuvable de l'application.

E4, lot 2b : « Vouliez-vous dire… ? » quand le 02 rend des suggestions ; la fiche dit
la source du résumé (Kitsu, en anglais) et replie le résumé Kitsu sous le français.

E4, lot 3 : la fiche porte les critiques publiées des libraires du réseau
(`pages_critiques.reseau_de`) et leur moyenne, avant la note des lecteurs ; la base
perdue, la fiche s'affiche quand même, avec l'encart à la place de la section.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from application import journal, pages_critiques, presentation, sessions
from application.client_catalogue import (
    CatalogueIndisponible,
    ClientCatalogue,
    CorpsInattendu,
    Resultats,
    SerieCatalogue,
)
from application.comptes import Compte
from application.config import Reglages

type Rendre = Callable[..., Response]

#: Le plus grand décalage que `GET /series` admet (contrat 0.5.0 du 02).
DECALAGE_MAX = 1000
#: Un identifiant de série dans un chemin : des chiffres, assez peu pour un `bigint`.
CHIFFRES_MAX = 18


@dataclass(frozen=True, slots=True)
class Ligne:
    """Un résultat, prêt à afficher."""

    series_id: int
    titre: str
    details: str
    trouve_par: str | None


@dataclass(frozen=True, slots=True)
class Pagination:
    page: int
    pages: int
    texte: str
    precedente: str | None
    suivante: str | None


def votes_de(n: int) -> str:
    """Un nombre de votes à la française : l'espace fine insécable des milliers."""
    return f"{n:,}".replace(",", " ")


def installer(
    app: FastAPI,
    reglages: Reglages,
    rendre: Rendre,
    client: ClientCatalogue | None,
) -> None:
    t = reglages.textes
    regles = reglages.catalogue
    page_max = DECALAGE_MAX // regles.par_page + 1
    disponibilites = {
        "edite_en_france": t["dispo_france"],
        "seulement_au_japon": t["dispo_japon"],
        "inconnue": t["dispo_inconnue"],
    }

    def aide() -> str:
        return presentation.dire(
            t, "catalogue_aide", min=regles.q_min, max=regles.q_max
        )

    def ligne_de(resultat: Resultats, indice: int) -> Ligne:
        item = resultat.items[indice]
        parties = [
            valeur
            for valeur in (
                item.type,
                None if item.year is None else str(item.year),
                item.auteurs,
                None
                if item.members_rating is None
                else presentation.dire(
                    t,
                    "catalogue_note",
                    note=presentation.decimales(item.members_rating, t, 1),
                ),
                None
                if item.disponibilite is None
                else disponibilites[item.disponibilite],
            )
            if valeur
        ]
        trouve_par = None
        if item.correspondance == "auteur":
            trouve_par = t["catalogue_trouve_par_auteur"]
        elif item.correspondance == "autre_titre" and item.titre_correspondant:
            trouve_par = presentation.dire(
                t, "catalogue_trouve_par", titre=item.titre_correspondant
            )
        return Ligne(
            series_id=item.series_id,
            titre=item.title or t["valeur_inconnue"],
            details=" · ".join(parties),
            trouve_par=trouve_par,
        )

    def lien(q: str, page: int) -> str:
        return "/catalogue?" + urlencode({"q": q, "page": page})

    def pagination_de(q: str, page: int, total: int) -> Pagination:
        pages = min(max(1, -(-total // regles.par_page)), page_max)
        return Pagination(
            page=page,
            pages=pages,
            texte=presentation.dire(t, "catalogue_page", n=page, total=pages),
            precedente=lien(q, page - 1) if page > 1 else None,
            suivante=lien(q, page + 1) if page < pages else None,
        )

    def page_catalogue(
        request: Request,
        statut: int = 200,
        *,
        q: str = "",
        erreur: str | None = None,
        resultats: Resultats | None = None,
        page: int = 1,
        indisponible: bool = False,
    ) -> Response:
        lignes = (
            [ligne_de(resultats, i) for i in range(len(resultats.items))]
            if resultats is not None
            else []
        )
        return rendre(
            request,
            "catalogue.html",
            statut,
            q=q,
            aide=aide(),
            erreur=erreur,
            lignes=lignes,
            cherche=resultats is not None,
            aucun=presentation.dire(t, "catalogue_aucun", q=q),
            # Contrat 0.6.0 : rien ne correspond, des séries ressemblent (« Vouliez-vous
            # dire… ? ») ; un lien par fiche, la fiche n'est pas préchargée.
            suggestions=(
                [
                    (s.series_id, s.title or t["valeur_inconnue"])
                    for s in resultats.suggestions
                ]
                if resultats is not None and resultats.total == 0
                else []
            ),
            pagination=(
                pagination_de(q, page, resultats.total)
                if resultats is not None and resultats.total > regles.par_page
                else None
            ),
            indisponible=indisponible,
            catalogue_regles=regles,
        )

    def indisponible(request: Request, motif: str, q: str = "") -> Response:
        request.state.code_erreur = f"catalogue_{motif}"
        return page_catalogue(request, 503, q=q, indisponible=True)

    @app.get("/catalogue", include_in_schema=False)
    async def catalogue(request: Request) -> Response:
        await sessions.page(request)  # le menu du compte connecté
        brute = request.query_params.get("q")
        if brute is None:
            if client is None:
                return indisponible(request, "non_configure")
            return page_catalogue(request)
        q = brute.strip()
        if not regles.q_min <= len(q) <= regles.q_max:
            request.state.code_erreur = "catalogue_saisie"
            erreur = presentation.dire(
                t, "catalogue_erreur_saisie", min=regles.q_min, max=regles.q_max
            )
            return page_catalogue(request, 422, q=q[: regles.q_max], erreur=erreur)
        if client is None:
            return indisponible(request, "non_configure", q)
        demande = request.query_params.get("page", "1")
        page = int(demande) if demande.isascii() and demande.isdigit() else 1
        page = min(max(1, page), page_max)
        try:
            resultats = await client.rechercher(q, page, regles.par_page)
            derniere = min(max(1, -(-resultats.total // regles.par_page)), page_max)
            if not resultats.items and resultats.total and page > derniere:
                # Une page au-delà de la dernière : la dernière.
                page = derniere
                resultats = await client.rechercher(q, page, regles.par_page)
        except CatalogueIndisponible as exc:
            return indisponible(request, exc.motif, q)
        except CorpsInattendu:
            return indisponible(request, "corps", q)
        journal.evenement_requete(
            request,
            "catalogue_recherche",
            {"resultats": resultats.total, "page": page},
        )
        return page_catalogue(request, q=q, resultats=resultats, page=page)

    async def page_serie(
        request: Request, fiche: SerieCatalogue, compte: Compte | None
    ) -> Response:
        meta = fiche.metadonnees()
        reseau = await pages_critiques.reseau_de(
            request.app.state.base, reglages, compte, fiche.series_id
        )
        publiee = request.query_params.get("etat") == "critique_publiee"
        return rendre(
            request,
            "serie.html",
            reseau=reseau,
            avis=t["critiques_l5"] if publiee else None,
            titre=fiche.title or t["valeur_inconnue"],
            fiche=fiche,
            # Une adresse du catalogue, et rien d'autre (ni `javascript:`, ni relatif).
            lien_catalogue=(
                fiche.url
                if fiche.url and fiche.url.startswith(("https://", "http://"))
                else None
            ),
            metadonnees=presentation.metadonnees_de(meta, t),
            note=(
                presentation.dire(
                    t,
                    "serie_note",
                    note=presentation.decimales(fiche.members_rating, t, 1),
                    votes=votes_de(fiche.members_votes or 0),
                )
                if fiche.members_rating is not None
                else None
            ),
        )

    @app.get("/series/{chemin}", include_in_schema=False)
    async def serie(request: Request, chemin: str) -> Response:
        compte = await sessions.page(request)  # le menu, les liens des critiques
        if not (chemin.isascii() and chemin.isdigit() and len(chemin) <= CHIFFRES_MAX):
            raise StarletteHTTPException(status_code=404)
        if client is None:
            return indisponible(request, "non_configure")
        try:
            fiche = await client.serie(int(chemin))
        except CatalogueIndisponible as exc:
            return indisponible(request, exc.motif)
        except CorpsInattendu:
            return indisponible(request, "corps")
        if fiche is None:
            raise StarletteHTTPException(status_code=404)
        return await page_serie(request, fiche, compte)
