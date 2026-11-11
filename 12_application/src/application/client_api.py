"""Le seul code qui parle à l'API du modèle, par HTTP et par son contrat.

Quatre appels : `info()`, `sante()`, `reponse(demande)` et, pour l'administration
(E4, lot 5), `verification()` — le test des clés, route du contrat 1.5.0, absente des
versions antérieures (`RouteAbsente`, le repli). Les erreurs sont typées d'après
le modèle d'erreur de l'API : `ErreurApi`, `ApiInjoignable`, `ApiDelai`. Un corps qui ne
tient pas le contrat devient `ErreurApi(statut, "corps_inattendu")`. Les formes
ci-dessous ne retiennent que ce que l'application emploie : un champ de plus de l'API
est ignoré (le contrat ne reçoit que des ajouts). Ce que seul le mode démonstration
affiche est facultatif ici : son absence ne coûte jamais une réponse à la production.

Aucune question, aucun historique, aucune réponse n'est journalisé ici. Une exception de
httpx porte la requête, donc l'en-tête de la clé : elle ne sort jamais d'ici, ni comme
cause. L'API ignore un identifiant reçu et tire le sien : rien n'est propagé, on lit le
sien.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ValidationError

from application import journal
from application.config import Delais, Limites
from application.metriques import ETATS_API, Metriques

ENTETE_CLE = "X-API-Key"
ENTETE_IDENTIFIANT = "X-Identifiant-Requete"
ROUTE_INFO = "/v1/info"
ROUTE_SANTE = "/v1/sante"
ROUTE_REPONSE = "/v1/reponse"
ROUTE_VERIFICATION = "/v1/verification"


class ProfilDecrit(BaseModel):
    nom: str
    fournisseur: str
    modele: str
    statut: Literal["stable", "apercu"]
    disponible: bool
    reglages_absents: list[str]
    #: Le NOM de la variable de la clé, jamais sa valeur (contrat 1.4.0 : obligatoire).
    variable_cle: str


class BorneReelle(BaseModel):
    defaut: float
    min: float
    max: float


class ModeDecrit(BaseModel):
    defaut: str
    valeurs: list[str]


class HistoriqueDecrit(BaseModel):
    tours_max: int
    caracteres_max: int


class BorneBooleenne(BaseModel):
    defaut: bool


class ReglagesReponse(BaseModel):
    temperature: BorneReelle
    mode: ModeDecrit
    historique: HistoriqueDecrit
    reformuler: BorneBooleenne


class ModeleLangage(BaseModel):
    profil_actif: str
    profils: list[ProfilDecrit]
    reglages_reponse: ReglagesReponse


class Question(BaseModel):
    longueur_max_caracteres: int


class Info(BaseModel):
    version_api: str
    question: Question
    modele_langage: ModeleLangage
    #: Le corpus et l'encodage en service (E4, lot 5 : la page des services).
    corpus_id: str
    encodage_id: int
    modele: str


class ControleCle(BaseModel):
    """Le test de la clé d'un profil (contrat 1.5.0) : des noms et des états."""

    nom: str
    fournisseur: str
    modele: str
    variable_cle: str
    configuree: bool
    disponible: bool
    statut: str


class Verification(BaseModel):
    verifie_le: str
    profils: list[ControleCle]


@dataclass(frozen=True, slots=True)
class RouteAbsente:
    """L'API ne connaît pas la route (404) : une version antérieure à 1.5.0."""


@dataclass(frozen=True, slots=True)
class TropTot:
    """La borne du test (un par minute et par clé) : l'attente, si l'API la dit."""

    retry_after: int | None


@dataclass(frozen=True, slots=True)
class Indisponible:
    """L'API ne répond pas, trop lentement, ou hors contrat."""


class SerieCitee(BaseModel):
    serie_id: int
    titre: str
    rang: int
    fragment_ids: list[int]


class Passage(BaseModel):
    fragment_id: int
    type_document: Literal["critique", "resume_serie", "synopsis_kitsu"]
    extrait: str
    score: float | None = None


class Metadonnees(BaseModel):
    disponibilite: Literal["edite_en_france", "seulement_au_japon", "inconnue"] | None
    nb_volumes: int | None
    nb_volumes_source: Literal["champ", "repli_volumes_distincts", "divergent"] | None
    statut: Literal["termine", "en_cours"] | None
    demographie: str | None
    auteurs: str | None


class Serie(BaseModel):
    serie_id: int | None
    titre: str
    passages: list[Passage]
    metadonnees: Metadonnees
    rang: int | None = None
    score: float | None = None


class AppelModele(BaseModel):
    role: Literal["reformulation", "reponse"]
    jetons_entree: int
    jetons_sortie: int
    jetons_raisonnement: int
    estimes: bool
    latence_ms: float
    statut: Literal["ok", "refus_debit"] = "ok"


class Latences(BaseModel):
    totale: float
    recherche: float | None = None
    reformulation: float | None = None
    modele: float | None = None


class ReglagesAppliques(BaseModel):
    nombre_series: int | None = None
    passages_par_serie: int | None = None
    catalogue_seul: bool | None = None


class ReglagesReponseAppliques(BaseModel):
    temperature: float | None = None
    longueur_max: int | None = None
    #: Le mode appliqué : il range le coût du jour (E4, lot 2b), pas le mode demandé.
    mode: str | None = None


class ContexteReponse(BaseModel):
    corpus_id: str
    encodage_id: int
    identifiant_requete: str
    profil: str
    fournisseur: str
    modele_langage: str
    date_appel: str | None
    question_reformulee: str | None
    reformulation_ignoree: Literal["trop_longue"] | None
    appels: list[AppelModele]
    cout_estime_usd: float | None
    latences_ms: Latences
    reglages: ReglagesAppliques | None = None
    reglages_appliques: ReglagesReponseAppliques | None = None
    modele: str | None = None
    revision: str | None = None


class Reponse(BaseModel):
    refus: bool
    motif_refus: Literal["aucun_passage", "hors_catalogue", "inconnu"] | None
    reponse: str
    series_citees: list[SerieCitee]
    series: list[Serie]
    contexte: ContexteReponse
    #: Le mode enrichi, quand l'API le publiera : l'écran l'accueille sans retouche.
    complement: str | None = None
    complement_titres: list[str] | None = None
    #: L'étiquette du complément (contrat 1.4.0, D9) ; absente, le texte du 12.
    complement_etiquette: str | None = None


#: L'étiquette des erreurs est fermée : le contrat type `code` en simple texte, sans
#: énumération. Ceux qu'il nomme et les génériques de l'API ; tout autre compte sous
#: `corps_inattendu`. Un test confronte cette liste aux codes nommés par le contrat.
CODES_API = frozenset(
    {
        "api_non_prete",
        "base_indisponible",
        "cle_invalide",
        "corps_trop_volumineux",
        "debit_depasse",
        "encodage_en_service_change",
        "entree_invalide",
        "erreur_interne",
        "fournisseur_delai",
        "fournisseur_injoignable",
        "fournisseur_refus",
        "historique_invalide",
        "json_invalide",
        "methode_non_permise",
        "profil_inconnu",
        "profil_indisponible",
        "question_trop_longue",
        "reglage_non_expose",
        "route_inconnue",
        "service_embedding_incoherent",
        "service_embedding_indisponible",
        "sortie_modele_invalide",
    }
)


class CorpsErreur(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None
    identifiant_requete: str


class ApiInjoignable(Exception):
    """Connexion refusée, coupée, ou délai de connexion dépassé."""


class ApiDelai(Exception):
    """L'API n'a pas répondu dans le délai."""


class ErreurApi(Exception):
    """Une réponse d'erreur de l'API, lue selon le contrat."""

    def __init__(
        self,
        statut: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        identifiant_requete: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(f"{statut} {code}")
        self.statut = statut
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})
        self.identifiant_requete = identifiant_requete
        self.retry_after = retry_after


def lire_retry_after(valeur: str | None) -> int | None:
    """Un nombre entier de secondes, d'au moins 1 ; None sinon (une date aussi)."""
    if valeur is None:
        return None
    try:
        secondes = int(valeur.strip())
    except ValueError:
        return None
    return secondes if secondes >= 1 else None


@dataclass(frozen=True, slots=True)
class Resultat:
    """Une réponse lue, et l'identifiant de la requête côté API."""

    corps: Reponse
    identifiant_api: str | None


class ClientApi:
    """La clé n'est gardée que dans le client HTTP ; `repr` ne la montre pas.

    E4, lot 2b (décision 5) : une clé **par appel** — celle des comptes pour un compte
    connecté, la publique sinon (`reponse(demande, cle=…)`) ; sans clé passée, la
    publique, celle du constructeur. Jamais deux clients distincts."""

    def __init__(
        self,
        adresse: str,
        cle: str,
        delais: Delais,
        metriques: Metriques,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.adresse = adresse
        self._delais = delais
        self._metriques = metriques
        self._cle = cle
        self._http = httpx.AsyncClient(
            base_url=adresse, transport=transport, follow_redirects=False
        )

    def __repr__(self) -> str:
        return f"ClientApi(adresse={self.adresse!r})"

    async def fermer(self) -> None:
        await self._http.aclose()

    async def _appeler(
        self,
        methode: str,
        route: str,
        delai_s: float,
        corps: object | None = None,
        *,
        avec_cle: bool = True,
        cle: str | None = None,
    ) -> httpx.Response:
        """La requête ; ses échecs de transport en deux exceptions, sans leur cause. Le
        délai borne l'appel entier, pas seulement chaque lecture."""
        connexion = self._delais.connexion_s
        delais = httpx.Timeout(delai_s, connect=connexion)
        try:
            async with asyncio.timeout(connexion + delai_s):
                reponse = await self._http.request(
                    methode,
                    route,
                    json=corps,
                    timeout=delais,
                    headers={ENTETE_CLE: cle or self._cle} if avec_cle else None,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            self._noter(route, "injoignable", "api_injoignable")
            raise ApiInjoignable from None
        except (httpx.TimeoutException, TimeoutError):
            self._noter(route, "delai", "api_delai")
            raise ApiDelai from None
        except httpx.HTTPError:
            self._noter(route, "injoignable", "api_injoignable")
            raise ApiInjoignable from None
        self._metriques.api_appels.labels(
            route_api=route, statut=str(reponse.status_code)
        ).inc()
        return reponse

    def _noter(self, route: str, statut: str, code: str) -> None:
        self._metriques.api_appels.labels(route_api=route, statut=statut).inc()
        self._metriques.api_erreurs.labels(code=code).inc()

    def _erreur(self, reponse: httpx.Response) -> ErreurApi:
        """Une réponse hors 2xx, selon le contrat ; sinon `corps_inattendu`."""
        identifiant = reponse.headers.get(ENTETE_IDENTIFIANT)
        try:
            corps = CorpsErreur.model_validate_json(reponse.content)
        except ValidationError:
            erreur = ErreurApi(
                reponse.status_code,
                "corps_inattendu",
                "Corps de réponse hors contrat.",
                None,
                identifiant,
            )
        else:
            erreur = ErreurApi(
                reponse.status_code,
                corps.code,
                corps.message,
                corps.details,
                corps.identifiant_requete,
                lire_retry_after(reponse.headers.get("Retry-After")),
            )
        code = erreur.code if erreur.code in CODES_API else "corps_inattendu"
        self._metriques.api_erreurs.labels(code=code).inc()
        return erreur

    def _inattendu(self, reponse: httpx.Response) -> ErreurApi:
        self._metriques.api_erreurs.labels(code="corps_inattendu").inc()
        return ErreurApi(
            reponse.status_code,
            "corps_inattendu",
            "Corps de réponse hors contrat.",
            None,
            reponse.headers.get(ENTETE_IDENTIFIANT),
        )

    async def info(self) -> Info:
        reponse = await self._appeler("GET", ROUTE_INFO, self._delais.info_s)
        if reponse.status_code != 200:
            raise self._erreur(reponse)
        try:
            return Info.model_validate_json(reponse.content)
        except ValidationError:
            raise self._inattendu(reponse) from None

    async def sante(self) -> bool:
        """Vrai si l'API est vivante et en bon état ; un 503, une panne : faux."""
        try:
            # La route est sans clé : elle ne la reçoit pas.
            reponse = await self._appeler(
                "GET", ROUTE_SANTE, self._delais.sante_s, avec_cle=False
            )
        except (ApiInjoignable, ApiDelai):
            return False
        return reponse.status_code == 200

    async def reponse(
        self, demande: Mapping[str, object], cle: str | None = None
    ) -> Resultat:
        """`POST /v1/reponse` ; `demande` : le corps, tel que le contrat le décrit ;
        `cle` : celle de l'appel (None : la publique)."""
        reponse = await self._appeler(
            "POST", ROUTE_REPONSE, self._delais.reponse_s, dict(demande), cle=cle
        )
        identifiant = reponse.headers.get(ENTETE_IDENTIFIANT)
        if not 200 <= reponse.status_code < 300:
            raise self._erreur(reponse)
        try:
            corps = Reponse.model_validate_json(reponse.content)
        except ValidationError:
            raise self._inattendu(reponse) from None
        return Resultat(corps, identifiant)

    async def verification(
        self, cle: str | None = None
    ) -> Verification | RouteAbsente | TropTot | Indisponible:
        """`POST /v1/verification` (contrat 1.5.0), le test des clés : 404, l'API est
        d'une version antérieure (le repli) ; 429, la borne d'un test par minute ; une
        panne, un délai, une réponse hors contrat : indisponible."""
        try:
            reponse = await self._appeler(
                "POST", ROUTE_VERIFICATION, self._delais.verification_s, cle=cle
            )
        except (ApiInjoignable, ApiDelai):
            return Indisponible()
        if reponse.status_code == 404:
            return RouteAbsente()
        if reponse.status_code == 429:
            return TropTot(lire_retry_after(reponse.headers.get("Retry-After")))
        if reponse.status_code != 200:
            self._erreur(reponse)
            return Indisponible()
        try:
            return Verification.model_validate_json(reponse.content)
        except ValidationError:
            self._inattendu(reponse)
            return Indisponible()


#: L'état de l'API vu d'ici : elle répond, elle ne répond pas, elle refuse la clé.
type EtatApi = Literal["ok", "ko", "cle"]


class VueApi:
    """Ce que l'application sait de l'API : le dernier `/v1/info` lu, et son état. Relu
    à chaque page ; un échec n'empêche ni de démarrer ni de servir la page."""

    def __init__(
        self, client: ClientApi, limites: Limites, metriques: Metriques
    ) -> None:
        self.client = client
        self.info: Info | None = None
        self.etat: EtatApi = "ko"
        self._limites = limites
        self._metriques = metriques
        self._ecarts_journalises: set[tuple[str, int]] = set()

    async def rafraichir(self) -> None:
        try:
            self.info = await self.client.info()
            self.etat = "ok"
        except ErreurApi as erreur:
            if erreur.statut == 401:
                self.etat = "cle"
            elif erreur.statut == 429 and self.info is not None:
                self.etat = (
                    "ok"  # la dernière liste reste bonne ; le `POST` dira le 429
                )
            else:
                self.etat = "ko"
        except (ApiInjoignable, ApiDelai):
            self.etat = "ko"
        for etat in ETATS_API:
            self._metriques.api_info_etat.labels(etat=etat).set(
                1 if self.etat == etat else 0
            )

    def limites(self) -> Limites:
        """Les bornes en vigueur : celles que l'API publie l'emportent sur la
        configuration ; un écart est journalisé, une fois par valeur."""
        if self.info is None:
            return self._limites
        historique = self.info.modele_langage.reglages_reponse.historique
        effectives = replace(
            self._limites,
            question_caracteres_max=self.info.question.longueur_max_caracteres,
            tours_max=historique.tours_max,
            caracteres_max=historique.caracteres_max,
            echanges=historique.tours_max // 2,
        )
        for nom, configuree, publiee in (
            (
                "question_caracteres_max",
                self._limites.question_caracteres_max,
                effectives.question_caracteres_max,
            ),
            ("tours_max", self._limites.tours_max, effectives.tours_max),
            ("caracteres_max", self._limites.caracteres_max, effectives.caracteres_max),
            ("echanges", self._limites.echanges, effectives.echanges),
        ):
            if configuree != publiee and (nom, publiee) not in self._ecarts_journalises:
                self._ecarts_journalises.add((nom, publiee))
                journal.evenement(
                    "ecart_bornes",
                    {"borne": nom, "configuree": configuree, "publiee": publiee},
                    30,
                )
        return effectives
