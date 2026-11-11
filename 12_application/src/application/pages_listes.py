"""Les écrans des listes du client (E4, lot 4, CL2 à CL4) : le geste depuis la fiche
d'une série et depuis une carte du conseiller (`POST /listes/{series_id}`), la page
« Mes listes » (`GET /compte/listes`), et la zone « Vos listes » de la fiche.

Même construction que `pages_critiques` : pages rendues côté serveur, sans JavaScript ;
le jeton CSRF d'abord, puis le rôle, puis seulement l'identifiant du chemin ; PRG par
`?etat=`, l'avis lu à l'arrivée dans une table (`AVIS_FICHE`, `AVIS_PAGE`).

**Qui** : les listes sont celles des **clients**. Un libraire ou un administrateur n'a
ni la zone, ni le menu, ni le geste (403) ; un visiteur voit l'invitation à se
connecter, et son geste le mène à la connexion. Les listes d'un compte ne se lisent et
ne s'écrivent que par lui : toute requête porte l'identifiant de la session, jamais
un identifiant venu du formulaire.

**Le catalogue** (le 02) : la série d'un ajout y est revalidée avant l'écriture
(inconnue : 404 ; catalogue muet : rien n'est écrit, l'avis le dit) ; les titres de la
page se lisent en parallèle ; un retrait n'a pas besoin du catalogue (une série que le
02 ne connaît plus doit pouvoir sortir d'une liste).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from python_multipart.exceptions import FormParserError
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException as StarletteHTTPException

from application import listes, presentation, sessions
from application.base import Base, BaseIndisponible
from application.client_catalogue import (
    CatalogueIndisponible,
    ClientCatalogue,
    CorpsInattendu,
)
from application.comptes import Compte
from application.config import Reglages
from application.listes import ETATS, Etat, ListePleine
from application.metriques import Metriques

type Rendre = Callable[..., Response]

Client = Annotated[Compte, Depends(sessions.exiger_role("client"))]

#: Un identifiant de série dans un chemin : des chiffres, assez peu pour un `integer`.
CHIFFRES_MAX = 10
RETIRER = "retirer"
#: Les issues d'un geste (`?etat=`) → l'avis de la fiche ; celles qui nomment la liste
#: la lisent dans l'état courant de la série.
AVIS_FICHE = {
    "ajoutee": "liste_avis_ajoutee",
    "changee": "liste_avis_changee",
    "inchangee": "liste_avis_inchangee",
    "retiree": "liste_avis_retiree",
    "absente": "liste_avis_absente",
    "indisponible": "liste_avis_indisponible",
    "pleine": "liste_avis_pleine",
}
#: Celles qu'on lit sur la page « Mes listes » (ses boutons ne font que retirer).
AVIS_PAGE = ("retiree", "absente")


@dataclass(frozen=True, slots=True)
class Bouton:
    valeur: str
    libelle: str
    actuel: bool


@dataclass(frozen=True, slots=True)
class Zone:
    """La zone « Vos listes » d'une fiche, ou le formulaire d'une carte : pour un
    visiteur, l'invitation (`connexion`, `inscription`) ; pour un client, la phrase
    d'état et les boutons ; `indisponible` : la base ne répond pas."""

    visiteur: bool
    connexion: str | None
    inscription: str | None
    phrase: str | None
    action: str | None
    retour: str | None
    boutons: tuple[Bouton, ...]
    retirer: bool
    #: Le nom du formulaire (« Mettre … dans une liste ») : deux cartes ne se
    #: confondent pas.
    groupe: str = ""
    indisponible: bool = False


@dataclass(frozen=True, slots=True)
class LigneListe:
    series_id: int
    titre: str
    date: str
    datetime: str
    etiquette_retirer: str


@dataclass(frozen=True, slots=True)
class Section:
    etat: str
    titre: str
    vide: str
    lignes: tuple[LigneListe, ...]


def avec_etat(retour: str, etat: str) -> str:
    """Le chemin de retour, son paramètre `etat` remplacé : un retour qui en portait
    déjà un (une fiche après un premier geste) ne l'empile pas."""
    morceaux = urlsplit(retour)
    garde = [(k, v) for k, v in parse_qsl(morceaux.query) if k != "etat"]
    return f"{morceaux.path}?{urlencode([*garde, ('etat', etat)])}"


def peut_lister(compte: Compte | None) -> bool:
    """Les listes sont celles des clients (décision 2 du lot 4)."""
    return compte is not None and compte.role == "client"


def boutons_de(t: Mapping[str, str], courant: str | None) -> tuple[Bouton, ...]:
    sortie: list[Bouton] = []
    for etat in ETATS:
        libelle = t[f"liste_bouton_{etat}"]
        actuel = etat == courant
        if actuel:
            libelle = presentation.dire(t, "liste_bouton_actuelle", libelle=libelle)
        sortie.append(Bouton(etat, libelle, actuel))
    return tuple(sortie)


def zone_client(
    t: Mapping[str, str],
    series_id: int,
    courant: str | None,
    retour: str,
    groupe: str,
) -> Zone:
    return Zone(
        visiteur=False,
        connexion=None,
        inscription=None,
        phrase=(
            presentation.dire(t, "serie_listes_etat", liste=t[f"liste_{courant}"])
            if courant is not None
            else None
        ),
        action=f"/listes/{series_id}",
        retour=retour,
        boutons=boutons_de(t, courant),
        retirer=courant is not None,
        groupe=groupe,
    )


async def zone_de_la_fiche(
    base: Base, t: Mapping[str, str], compte: Compte | None, series_id: int
) -> Zone | None:
    """La zone de la fiche selon le rôle ; None : elle n'est pas rendue (libraire,
    administrateur)."""
    retour = f"/series/{series_id}"
    if compte is None:
        suffixe = sessions.suffixe_retour(retour)
        return Zone(
            visiteur=True,
            connexion="/connexion" + suffixe,
            inscription="/inscription" + suffixe,
            phrase=None,
            action=None,
            retour=None,
            boutons=(),
            retirer=False,
        )
    if not peut_lister(compte):
        return None
    try:
        courant = await listes.etat_de(base, compte.id, series_id)
    except BaseIndisponible:
        return Zone(
            visiteur=False,
            connexion=None,
            inscription=None,
            phrase=None,
            action=None,
            retour=None,
            boutons=(),
            retirer=False,
            indisponible=True,
        )
    return zone_client(t, series_id, courant, retour, t["serie_listes_groupe"])


def avis_de_la_fiche(
    t: Mapping[str, str], etat: str, zone: Zone | None, maximum: int
) -> str | None:
    """L'avis d'un geste, à l'arrivée sur la fiche ; la liste nommée est l'état
    courant (le geste vient d'être écrit) — sans état connu, pas d'avis qui en parle."""
    cle = AVIS_FICHE.get(etat)
    if cle is None or zone is None or zone.visiteur:
        return None
    if etat == "pleine":
        return presentation.dire(t, cle, n=maximum)
    if etat in ("ajoutee", "changee", "inchangee"):
        courant = next((b.valeur for b in zone.boutons if b.actuel), None)
        if courant is None:
            return None
        return presentation.dire(t, cle, liste=t[f"liste_dans_{courant}"])
    return t[cle]


async def zones_des_cartes(
    base: Base,
    t: Mapping[str, str],
    compte: Compte | None,
    cartes: Iterable[tuple[int, str]],
) -> dict[int, Zone]:
    """Le formulaire de chaque carte du conseiller (CL4), pour un client seulement ;
    retour : la fiche de la série (la réponse n'est pas conservée par un `POST` sans
    JavaScript). Les états en une requête ; la base muette : aucun formulaire."""
    if not peut_lister(compte):
        return {}
    titres = dict(cartes)
    try:
        etats = (
            await listes.etats_de(base, compte.id, list(titres))
            if compte is not None
            else {}
        )
    except BaseIndisponible:
        return {}
    return {
        sid: zone_client(
            t,
            sid,
            etats.get(sid),
            f"/series/{sid}",
            presentation.dire(t, "carte_listes_groupe", titre=titre),
        )
        for sid, titre in titres.items()
    }


def installer(
    app: FastAPI,
    reglages: Reglages,
    rendre: Rendre,
    client: ClientCatalogue | None,
    metriques: Metriques,
) -> None:
    t = reglages.textes
    maximum = reglages.comptes.listes_max_par_etat

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
        series_id = int(chemin)
        if not listes.serie_valide(series_id):
            raise StarletteHTTPException(status_code=404)
        return series_id

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

    def vers(retour: str, etat: str) -> Response:
        return RedirectResponse(avec_etat(retour, etat), status_code=303)

    async def serie_connue(series_id: int) -> bool | None:
        """La série existe-t-elle au catalogue ? None : il ne répond pas (ou n'est
        pas configuré) — rien ne s'écrit à l'aveugle."""
        if client is None:
            return None
        try:
            fiche = await client.serie(series_id)
        except (CatalogueIndisponible, CorpsInattendu):
            return None
        return fiche is not None

    @app.post("/listes/{chemin}", include_in_schema=False)
    async def geste(request: Request, chemin: str) -> Response:
        donnees = await formulaire(request)
        retour = sessions.retour_local(
            champ(donnees, "retour"),
            f"/series/{chemin}"
            if chemin.isascii() and chemin.isdigit() and len(chemin) <= CHIFFRES_MAX
            else "/",
        )
        try:
            compte = await sessions.exiger_role("client")(request)
        except sessions.NonConnecte:
            # Un visiteur : la connexion, puis retour où il était.
            raise sessions.Redirection(
                "/connexion" + sessions.suffixe_retour(retour)
            ) from None
        series_id = identifiant(chemin)
        demande = champ(donnees, "etat")
        etat: Etat | None = listes.etat_connu(demande)
        if etat is None and demande != RETIRER:
            request.state.code_erreur = "liste_etat_inconnu"
            return page_nommee(
                request, 422, "liste_etat_inconnu_titre", "liste_etat_inconnu"
            )
        base = base_de(request)
        if etat is None:
            if await listes.retirer(base, compte.id, series_id):
                metriques.listes.labels(action="retrait").inc()
                return vers(retour, "retiree")
            return vers(retour, "absente")
        connue = await serie_connue(series_id)
        if connue is None:
            return vers(retour, "indisponible")
        if not connue:
            raise StarletteHTTPException(status_code=404)
        try:
            issue = await listes.poser(
                base, compte.id, series_id, etat, maximum=maximum
            )
        except ListePleine:
            return vers(retour, "pleine")
        if issue == "inchange":
            return vers(retour, "inchangee")
        metriques.listes.labels(action=issue).inc()
        return vers(retour, "ajoutee" if issue == "ajout" else "changee")

    async def titres_de(ids: list[int]) -> tuple[dict[int, str], bool]:
        """Les titres, demandés en parallèle au catalogue ; une série qu'il ne connaît
        plus : pas de titre ; le catalogue muet : aucun titre, et l'encart."""
        if not ids:
            return {}, False
        if client is None:
            return {}, True
        try:
            fiches = await asyncio.gather(*(client.serie(i) for i in ids))
        except (CatalogueIndisponible, CorpsInattendu):
            return {}, True
        titres = {
            series_id: fiche.title
            for series_id, fiche in zip(ids, fiches, strict=True)
            if fiche is not None and fiche.title
        }
        return titres, False

    @app.get("/compte/listes", include_in_schema=False)
    async def mes_listes(request: Request, compte: Client) -> Response:
        contenu = await listes.lister(base_de(request), compte.id, maximum=maximum)
        ids = [e.series_id for etat in ETATS for e in contenu[etat]]
        titres, indisponible = await titres_de(ids)
        sections: list[Section] = []
        for etat in ETATS:
            lignes: list[LigneListe] = []
            for entree in contenu[etat]:
                titre = titres.get(entree.series_id, t["listes_titre_indisponible"])
                lignes.append(
                    LigneListe(
                        series_id=entree.series_id,
                        titre=titre,
                        date=presentation.dire(
                            t,
                            "liste_ajoutee_le",
                            date=presentation.date_en_lettres(entree.ajoute_le),
                        ),
                        datetime=entree.ajoute_le.isoformat(timespec="seconds"),
                        etiquette_retirer=presentation.dire(
                            t, "liste_retirer_etiquette", titre=titre
                        ),
                    )
                )
            sections.append(
                Section(
                    etat=etat,
                    titre=presentation.dire(
                        t,
                        "liste_section_compte",
                        titre=t[f"liste_section_{etat}"],
                        n=len(lignes),
                    ),
                    vide=t[f"liste_vide_{etat}"],
                    lignes=tuple(lignes),
                )
            )
        etat_avis = request.query_params.get("etat", "")
        avis = t[AVIS_FICHE[etat_avis]] if etat_avis in AVIS_PAGE else None
        return rendre(
            request,
            "listes.html",
            sections=sections,
            indisponible=indisponible,
            avis=avis,
        )
