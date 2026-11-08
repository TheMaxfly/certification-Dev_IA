"""Le seul code qui parle au catalogue — l'API data du module 02 —, par son contrat
0.5.0.

Deux appels de données, `rechercher(q, page)` (`GET /series`) et `serie(series_id)`
(`GET /series/{series_id}`, 404 → None), et une sonde, `sante()` (`GET /live`, sans
clé).
Les formes ci-dessous ne retiennent que ce que l'application emploie : un champ de plus
de l'API est ignoré. L'application n'appelle **jamais** `/series/{id}/reviews` : les
critiques collectées portent des pseudonymes de lecteurs.

Deux erreurs, sans cause : `CatalogueIndisponible` (injoignable, délai, 5xx, et 401 —
la clé refusée, journalisée `catalogue_cle_refusee` sans la clé) et `CorpsInattendu`
(une réponse hors contrat). Une exception de httpx porte la requête, donc l'en-tête de
la clé : elle ne sort jamais d'ici, ni comme cause. Les métriques : un appel compté par
route et issue, l'état du catalogue posé à chaque appel.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from application import journal
from application.client_api import Metadonnees
from application.config import DelaisCatalogue
from application.metriques import Metriques

ENTETE_CLE = "X-API-Key"
ROUTE_RECHERCHE = "/series"
ROUTE_LIVE = "/live"

type Disponibilite = Literal["edite_en_france", "seulement_au_japon", "inconnue"]
type SourceTomes = Literal["champ", "repli_volumes_distincts", "divergent"]


class ResultatCatalogue(BaseModel):
    """Une série trouvée : ce que la liste des résultats affiche."""

    series_id: int
    title: str | None
    type: str | None
    year: int | None
    auteurs: str | None
    members_rating: float | None
    members_votes: int | None
    disponibilite: Disponibilite | None
    nb_volumes: int | None
    nb_volumes_source: SourceTomes | None
    correspondance: Literal["titre", "autre_titre", "auteur"]
    titre_correspondant: str | None


class Resultats(BaseModel):
    query: str
    total: int
    limit: int
    offset: int
    items: list[ResultatCatalogue]


class SerieCatalogue(BaseModel):
    """La fiche d'une série : ce que la page de série affiche."""

    series_id: int
    title: str | None
    type: str | None
    year: int | None
    auteurs: str | None = None
    members_rating: float | None
    members_votes: int | None
    disponibilite: Disponibilite | None = None
    nb_volumes: int | None = None
    nb_volumes_source: SourceTomes | None = None
    statut: Literal["termine", "en_cours"] | None = None
    demographie: str | None = None
    annee_fr: int | None = None
    synopsis_enriched: str | None
    genres_source: list[str] = Field(default_factory=list[str])
    url: str | None

    def metadonnees(self) -> Metadonnees:
        """Les métadonnées de la ligne de titre, sous la forme des cartes du
        conseiller (mêmes noms, mêmes valeurs) : la fiche passe par `metadonnees_de`,
        comme elles."""
        return Metadonnees(
            disponibilite=self.disponibilite,
            nb_volumes=self.nb_volumes,
            nb_volumes_source=self.nb_volumes_source,
            statut=self.statut,
            demographie=self.demographie,
            auteurs=self.auteurs,
        )


class CatalogueIndisponible(Exception):
    """Le catalogue ne répond pas : injoignable, délai, 5xx, ou clé refusée."""

    def __init__(self, motif: Literal["indisponible", "cle"] = "indisponible") -> None:
        super().__init__(motif)
        self.motif = motif


class CorpsInattendu(Exception):
    """Une réponse du catalogue hors de son contrat."""


class ClientCatalogue:
    """La clé n'est gardée que dans le client HTTP ; `repr` ne la montre pas."""

    def __init__(
        self,
        adresse: str,
        cle: str,
        delais: DelaisCatalogue,
        metriques: Metriques,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.adresse = adresse
        self._delais = delais
        self._metriques = metriques
        self._entetes = {ENTETE_CLE: cle}
        self._http = httpx.AsyncClient(
            base_url=adresse, transport=transport, follow_redirects=False
        )

    def __repr__(self) -> str:
        return f"ClientCatalogue(adresse={self.adresse!r})"

    async def fermer(self) -> None:
        await self._http.aclose()

    def _noter(self, route: str, statut: str, etat: str) -> None:
        self._metriques.catalogue_appels.labels(
            route_catalogue=route, statut=statut
        ).inc()
        self._metriques.etat_catalogue(etat)

    async def _appeler(
        self,
        chemin: str,
        route: str,
        parametres: dict[str, str | int] | None = None,
    ) -> httpx.Response:
        """Un `GET` borné ; ses échecs de transport et ses 5xx en une exception, sans
        cause ; un 401 aussi, journalisé. `route` : le nom de la route, pour les
        métriques (jamais l'identifiant demandé)."""
        connexion = self._delais.connexion_s
        delai = self._delais.reponse_s
        try:
            async with asyncio.timeout(connexion + delai):
                reponse = await self._http.get(
                    chemin,
                    params=parametres,
                    headers=self._entetes,
                    timeout=httpx.Timeout(delai, connect=connexion),
                )
        except (httpx.HTTPError, TimeoutError):
            self._noter(route, "indisponible", "indisponible")
            raise CatalogueIndisponible from None
        if reponse.status_code == 401:
            self._noter(route, "cle", "cle")
            journal.evenement("catalogue_cle_refusee", {"route_catalogue": route})
            raise CatalogueIndisponible("cle")
        if reponse.status_code >= 500:
            self._noter(route, "indisponible", "indisponible")
            raise CatalogueIndisponible
        return reponse

    def _hors_contrat(self, route: str) -> CorpsInattendu:
        self._noter(route, "corps", "indisponible")
        journal.evenement("catalogue_corps_inattendu", {"route_catalogue": route})
        return CorpsInattendu()

    async def rechercher(self, q: str, page: int, par_page: int) -> Resultats:
        """`GET /series` : la page `page` (à partir de 1) de `par_page` séries."""
        reponse = await self._appeler(
            ROUTE_RECHERCHE,
            "series",
            {"q": q, "limit": par_page, "offset": (page - 1) * par_page},
        )
        if reponse.status_code != 200:
            raise self._hors_contrat("series")
        try:
            resultats = Resultats.model_validate_json(reponse.content)
        except ValidationError:
            raise self._hors_contrat("series") from None
        self._noter("series", "ok", "ok")
        return resultats

    async def serie(self, series_id: int) -> SerieCatalogue | None:
        """`GET /series/{series_id}` ; une série inconnue (404) : None."""
        reponse = await self._appeler(f"{ROUTE_RECHERCHE}/{series_id}", "serie")
        if reponse.status_code == 404:
            self._noter("serie", "introuvable", "ok")
            return None
        if reponse.status_code != 200:
            raise self._hors_contrat("serie")
        try:
            fiche = SerieCatalogue.model_validate_json(reponse.content)
        except ValidationError:
            raise self._hors_contrat("serie") from None
        self._noter("serie", "ok", "ok")
        return fiche

    async def sante(self) -> bool:
        """`GET /live`, sans clé, délai court : le catalogue vit-il ?"""
        try:
            async with asyncio.timeout(self._delais.connexion_s):
                reponse = await self._http.get(
                    ROUTE_LIVE, timeout=self._delais.connexion_s
                )
        except (httpx.HTTPError, TimeoutError):
            return False
        return reponse.status_code == 200
