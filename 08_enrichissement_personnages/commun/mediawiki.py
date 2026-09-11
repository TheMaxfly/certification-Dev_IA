"""Client minimal de l'API MediaWiki.

Jamais de collecte HTML, jamais de rendu de page : uniquement `api.php`.

Trois regles d'etiquette y sont cablees plutot que recommandees :
requetes serielles via le limiteur, `maxlag` positionne avec attente et
nouvelle tentative en cas de depassement, et `User-Agent` descriptif portant un
moyen de contact — un agent generique ou absent est un motif de blocage
legitime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from .limiteur import Limiteur

POINT_ACCES = "https://fr.wikipedia.org/w/api.php"
MAXLAG = 5
TENTATIVES_MAX = 4
#: L'API `query` accepte 50 titres par appel pour un client anonyme.
LOT_TITRES = 50


class ReponseInattendue(RuntimeError):
    """L'API a repondu autrement qu'attendu — structure, limitation, blocage."""


@dataclass
class Article:
    """Ce que l'API dit d'un article, avant toute interpretation."""

    titre_demande: str
    titre_servi: str | None = None
    pageid: int | None = None
    revid: int | None = None
    existe: bool = False
    redirige: bool = False
    sections: list[dict[str, Any]] = field(default_factory=list)
    motif_absence: str | None = None


class ClientMediaWiki:
    def __init__(
        self,
        user_agent: str,
        *,
        limiteur: Limiteur | None = None,
        point_acces: str = POINT_ACCES,
        session: requests.Session | None = None,
    ) -> None:
        if not user_agent or "(" not in user_agent:
            raise ValueError(
                "User-Agent descriptif exige, avec moyen de contact entre "
                "parentheses (politique d'etiquette Wikimedia)"
            )
        self.point_acces = point_acces
        self.limiteur = limiteur or Limiteur(1.0)
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.requetes_emises = 0
        self.attentes_maxlag = 0

    def _appeler(self, parametres: dict[str, Any]) -> dict[str, Any]:
        """Un appel, cadence, avec attente et nouvelle tentative sur maxlag."""
        charge = {
            "format": "json",
            "formatversion": "2",
            "maxlag": MAXLAG,
            **parametres,
        }
        for tentative in range(1, TENTATIVES_MAX + 1):
            self.limiteur.attendre()
            reponse = self.session.get(self.point_acces, params=charge, timeout=30)
            self.requetes_emises += 1

            if reponse.status_code in (429, 503):
                self._patienter(reponse, tentative)
                continue
            if reponse.status_code != 200:
                raise ReponseInattendue(f"HTTP {reponse.status_code} sur {reponse.url}")
            try:
                donnees = reponse.json()
            except ValueError as erreur:
                raise ReponseInattendue(f"reponse non JSON : {erreur}") from erreur

            erreur = donnees.get("error")
            if erreur and erreur.get("code") == "maxlag":
                self.attentes_maxlag += 1
                self._patienter(reponse, tentative)
                continue
            if erreur:
                raise ReponseInattendue(
                    f"erreur API {erreur.get('code')} : {erreur.get('info')}"
                )
            return donnees
        raise ReponseInattendue(
            f"{TENTATIVES_MAX} tentatives epuisees — l'API limite ou bloque"
        )

    def _patienter(self, reponse: requests.Response, tentative: int) -> None:
        """On attend et on reessaie, jamais on ne force."""
        entete = reponse.headers.get("Retry-After")
        try:
            secondes = float(entete) if entete else 0.0
        except ValueError:
            secondes = 0.0
        self.limiteur.signaler_attente(max(secondes, 5.0 * tentative))

    def resoudre(self, titres: list[str]) -> dict[str, Article]:
        """Existence, redirection et `revid`, par lots.

        Les deux titres sont conserves : celui stocke dans les sitelinks et
        celui effectivement servi. Le `revid` est le pointeur stable — le
        titre, lui, peut changer.
        """
        resultats: dict[str, Article] = {t: Article(titre_demande=t) for t in titres}
        for debut in range(0, len(titres), LOT_TITRES):
            lot = titres[debut : debut + LOT_TITRES]
            donnees = self._appeler(
                {
                    "action": "query",
                    "prop": "revisions",
                    "rvprop": "ids",
                    "titles": "|".join(lot),
                    "redirects": "1",
                }
            )
            self._appliquer_resolution(donnees, resultats)
        return resultats

    @staticmethod
    def _appliquer_resolution(
        donnees: dict[str, Any], resultats: dict[str, Article]
    ) -> None:
        requete = donnees.get("query") or {}
        # `normalized` puis `redirects` : deux etapes distinctes cote API.
        vers_demande: dict[str, str] = {}
        for etape in ("normalized", "redirects"):
            for saut in requete.get(etape) or []:
                origine = saut["from"]
                demande = vers_demande.get(origine, origine)
                vers_demande[saut["to"]] = demande
                if etape == "redirects" and demande in resultats:
                    resultats[demande].redirige = True

        for page in requete.get("pages") or []:
            titre_servi = page.get("title")
            demande = vers_demande.get(titre_servi, titre_servi)
            article = resultats.get(demande)
            if article is None:
                continue
            article.titre_servi = titre_servi
            if page.get("missing"):
                article.motif_absence = "page absente"
                continue
            article.existe = True
            article.pageid = page.get("pageid")
            revisions = page.get("revisions") or []
            if revisions:
                article.revid = revisions[0].get("revid")

    def sections(self, titre: str) -> list[dict[str, Any]]:
        """Inventaire des sections d'un article — `action=parse&prop=sections`."""
        donnees = self._appeler({"action": "parse", "page": titre, "prop": "sections"})
        parse = donnees.get("parse")
        if parse is None:
            raise ReponseInattendue(f"pas de bloc `parse` pour {titre!r}")
        return parse.get("sections") or []

    def wikitexte(self, titre: str) -> str:
        """Wikitexte brut d'un article — jamais de HTML rendu."""
        donnees = self._appeler({"action": "parse", "page": titre, "prop": "wikitext"})
        parse = donnees.get("parse")
        if parse is None or "wikitext" not in parse:
            raise ReponseInattendue(f"pas de wikitexte pour {titre!r}")
        return parse["wikitext"]
