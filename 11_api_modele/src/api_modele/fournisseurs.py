"""La couche des fournisseurs du modèle de langage : la seule qui leur parle.

Une fonction d'appel (`Fournisseurs.appeler`), asynchrone, par requête HTTP directe
sur `/v1/chat/completions` (aucun paquet client de fournisseur). Le profil dit qui,
quoi, où ; ses écarts de forme vivent dans un petit adaptateur par fournisseur
(`ADAPTATEURS`), jamais dans les invites ni dans la route.

La clé d'un profil est lue dans l'environnement AU MOMENT de l'appel et posée dans
l'en-tête `Authorization`, nulle part ailleurs : ni gardée, ni journalisée, ni rendue ;
`repr` de la couche ne montre pas l'environnement, et une erreur de transport est
rendue sans sa cause (elle porte la requête, donc l'en-tête).

Avant l'appel : profil inconnu ou indisponible, réglage demandé que le profil déclare
absent — l'appel n'a pas lieu (`ErreurFournisseur`, que la route traduit en 422).
Pendant : connexion refusée ou trop lente → `fournisseur_injoignable` ; réponse trop
lente → `fournisseur_delai` ; statut hors 2xx, corps illisible ou sans `choices` →
`fournisseur_refus` (le statut, et le code d'erreur du fournisseur s'il est court ;
jamais le corps). Un 429 du fournisseur est un refus comme un autre : pas de nouvel
essai, pas de file d'attente.

Coût estimé, en dollars : `entrée × prix.entree + (sortie + raisonnement) ×
prix.sortie`, par million de jetons ; sans `usage`, les jetons sont estimés par
caractères (`estimes`). L'estimation avant appel (`estimer_jetons`, `estimer_cout`)
sert au banc, qui tient le plafond d'un run ; la couche ne bloque pas sur le plafond,
elle rend l'estimation dans sa trace.

Ce que la couche ne fait jamais : journaliser une clé, un message, un passage, une
sortie du modèle ; appeler sans profil ; réessayer.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal, cast

import httpx

from api_modele import journal
from api_modele.configuration import CHEMIN_APPEL, ModeleLangage, Profil
from api_modele.metriques import Metriques

type Role = Literal["reformulation", "reponse"]
type CodeErreur = Literal[
    "profil_inconnu",
    "profil_indisponible",
    "reglage_non_expose",
    "fournisseur_injoignable",
    "fournisseur_delai",
    "fournisseur_refus",
]

#: Le nom du schéma de sortie dans l'enveloppe `json_schema`.
NOM_SCHEMA = "sortie"
#: Un code d'erreur de fournisseur recopié dans `details` : court, sans donnée.
CODE_COURT = re.compile(r"^[a-z0-9_.-]{1,64}$")


@dataclass(frozen=True, slots=True)
class Adaptateur:
    """Les écarts de forme d'un fournisseur à la forme commune."""

    #: Le champ qui borne les jetons de sortie.
    champ_longueur: str
    #: Où `usage` range les jetons de raisonnement ; None : non documenté (compté 0).
    chemin_raisonnement: tuple[str, ...] | None
    #: Les jetons de raisonnement sont-ils déjà comptés dans `completion_tokens` ?
    raisonnement_inclus: bool
    #: La sortie peut-elle venir en morceaux typés (`content` en liste) ?
    morceaux_types: bool = False


#: Forme commune, chez les deux : `model`, `messages`, `temperature`, `top_p`,
#: `reasoning_effort`, et `response_format` = {"type": "json_schema", "json_schema":
#: {"name", "schema", "strict"}} ; la sortie dans `choices[0].message.content`, les
#: jetons dans `usage.prompt_tokens` et `usage.completion_tokens`, le modèle servi
#: dans `model`.
ADAPTATEURS: dict[str, Adaptateur] = {
    # Mistral — lu le 2026-10-26 : référence de l'API, « Chat Completion »
    # (docs.mistral.ai/api) : `max_tokens`, `temperature`, `top_p`,
    # `reasoning_effort`, `response_format` (type `json_schema`) ; l'enveloppe du
    # schéma (`json_schema` : `name`, `schema`, `strict`) : documentation du client
    # officiel (github.com/mistralai/client-python, docs/sdks/chat/README.md). Les
    # jetons de raisonnement dans `usage`, non documentés, relevés à l'appel d'essai
    # du 2026-10-28 (`mistral-large-4`, sans effort fixé) :
    # `usage.completion_tokens_details.reasoning_tokens`, compris dans
    # `completion_tokens` (400 = `max_tokens`, dont 399 de raisonnement).
    # Lu le 2026-10-31 : page « Reasoning » (docs.mistral.ai/capabilities/reasoning,
    # sans date affichée) : hors `reasoning_effort = none`, `message.content` est une
    # liste de morceaux typés — `thinking` (le raisonnement, sous `thinking`) et
    # `text` (la réponse, sous `text`) ; `mistral-large-4` admet `low`, `medium`,
    # `high`. Vu à l'essai du 2026-10-31 (`low`, fin `stop`, `content` hors chaîne).
    "mistral": Adaptateur(
        champ_longueur="max_tokens",
        chemin_raisonnement=("completion_tokens_details", "reasoning_tokens"),
        raisonnement_inclus=True,
        morceaux_types=True,
    ),
    # OpenAI — lu le 2026-10-26 : référence « Create chat completion »
    # (developers.openai.com/api/reference) : `max_tokens` « deprecated in favor of
    # `max_completion_tokens` », qui borne sortie visible ET raisonnement ;
    # `reasoning_effort` ; `response_format` `{"type": "json_schema", "json_schema":
    # {...}}` ; `usage.completion_tokens_details.reasoning_tokens`. Guide « Reasoning
    # models » : les jetons de raisonnement sont facturés comme sortie et comptés
    # dans la sortie (exemple de l'API Responses) ; leur inclusion dans
    # `completion_tokens` est tenue pour vraie (non établi, à lever en 06 ; sans
    # effet à l'effort `none` des profils actuels). Guide « Structured Outputs » :
    # `json_schema` en Chat Completions dès gpt-4o-2024-08-06 « and later ».
    "openai": Adaptateur(
        champ_longueur="max_completion_tokens",
        chemin_raisonnement=("completion_tokens_details", "reasoning_tokens"),
        raisonnement_inclus=True,
    ),
}


class ErreurFournisseur(Exception):
    """Un appel qui n'a pas lieu, ou qui échoue : son code stable et ses détails —
    jamais un corps de fournisseur, un texte envoyé ni une clé."""

    def __init__(
        self, code: CodeErreur, message: str, details: Mapping[str, object]
    ) -> None:
        super().__init__(message)
        self.code: CodeErreur = code
        self.message = message
        self.details = dict(details)


@dataclass(frozen=True, slots=True)
class Messages:
    """L'invite système et le message utilisateur (l'historique y est déjà plié par
    la route) : jamais affichés, jamais journalisés."""

    systeme: str = field(repr=False)
    utilisateur: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Demandes:
    """Les réglages demandés par l'appelant ; None : non demandé (défaut du fichier)."""

    temperature: float | None = None
    longueur_max: int | None = None


@dataclass(frozen=True, slots=True)
class Appliques:
    """Les réglages envoyés au fournisseur ; None : non envoyé."""

    temperature: float | None
    longueur_max: int | None
    top_p: float | None
    reasoning_effort: str | None


@dataclass(frozen=True, slots=True)
class Jetons:
    entree: int
    sortie: int
    raisonnement: int
    #: Faux quand `usage` les donne ; vrai : comptés par caractères.
    estimes: bool


@dataclass(frozen=True, slots=True)
class Sortie:
    """Ce qu'un appel réussi rend : le texte JSON de la sortie (jamais journalisé) et
    sa trace."""

    texte: str = field(repr=False)
    jetons: Jetons
    latence_ms: float
    cout_estime_usd: float | None
    #: L'identifiant rendu par le fournisseur, s'il diffère de celui demandé.
    modele_rendu: str | None
    date_appel: datetime
    appliques: Appliques


def estimer_jetons(texte: str, caracteres_par_jeton: float) -> int:
    """Les jetons d'un texte, comptés par caractères (estimation déclarée)."""
    return math.ceil(len(texte) / caracteres_par_jeton)


def cout_usd(
    profil: Profil, entree: int, sortie: int, raisonnement: int
) -> float | None:
    """Le coût en dollars ; None si le profil ne déclare pas de prix."""
    if profil.prix is None:
        return None
    prix = profil.prix
    return (entree * prix.entree + (sortie + raisonnement) * prix.sortie) / 1_000_000


def estimer_cout(profil: Profil, entree: int, sortie_max: int) -> float | None:
    """Le coût au plus d'un appel, avant de le faire : sortie au maximum, sans
    raisonnement (base du plafond du banc)."""
    return cout_usd(profil, entree, sortie_max, 0)


def _entier(valeur: object) -> int | None:
    if isinstance(valeur, bool) or not isinstance(valeur, int) or valeur < 0:
        return None
    return valeur


def _lire(donnees: object, chemin: tuple[str, ...]) -> object:
    for cle in chemin:
        if not isinstance(donnees, dict):
            return None
        donnees = cast(dict[str, object], donnees).get(cle)
    return donnees


def _code_fournisseur(reponse: httpx.Response) -> str | None:
    """Le code d'erreur du corps, s'il est court et sans donnée ; jamais le corps."""
    try:
        donnees: object = reponse.json()
    except ValueError:
        return None
    for chemin in (("error", "code"), ("error", "type"), ("code",), ("type",)):
        valeur = _lire(donnees, chemin)
        if isinstance(valeur, str) and CODE_COURT.fullmatch(valeur):
            return valeur
    return None


def adresse_modeles(adresse: str) -> str:
    """`…/v1/chat/completions` → `…/v1/models` (contrôle d'exploitation)."""
    return adresse.removesuffix(CHEMIN_APPEL) + "/v1/models"


class Fournisseurs:
    """La couche d'appel : les profils (disponibilité effective), l'environnement où
    les clés sont lues au moment de l'appel, le client HTTP, les métriques."""

    def __init__(
        self,
        modele_langage: ModeleLangage,
        environ: Mapping[str, str],
        metriques: Metriques,
        *,
        horloge: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._ml = modele_langage
        self._environ = environ
        self._metriques = metriques
        self._horloge = horloge
        # Sans l'environnement du poste : ni mandataire, ni `.netrc`.
        self._client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
        self.profil_actif = modele_langage.profil_actif
        self.profils: dict[str, Profil] = {p.nom: p for p in modele_langage.profils}

    def __repr__(self) -> str:
        return (
            f"Fournisseurs(profil_actif={self.profil_actif!r},"
            f" profils={list(self.profils)!r})"
        )

    # ------------------------------------------------------------------ #
    #  Démarrage, arrêt, jauges
    # ------------------------------------------------------------------ #
    async def demarrer(self) -> None:
        """Le contrôle d'exploitation s'il est demandé, puis les jauges ; le profil
        actif doit rester disponible (`profil_indisponible` sinon)."""
        if self._ml.verifier_modeles:
            await self.verifier_modeles()
        actif = self.profils[self.profil_actif]
        if not actif.disponible:
            raise ErreurFournisseur(
                "profil_indisponible",
                f"le profil actif « {actif.nom} » est indisponible : {actif.modele}"
                " absent des modèles du fournisseur",
                {"profil": actif.nom},
            )
        self.publier()

    async def fermer(self) -> None:
        await self._client.aclose()

    def publier(self) -> None:
        for nom, profil in self.profils.items():
            self._metriques.profil_actif.labels(profil=nom).set(
                1 if nom == self.profil_actif else 0
            )
            self._metriques.profil_disponible.labels(profil=nom).set(
                1 if profil.disponible else 0
            )

    async def verifier_modeles(self) -> dict[str, bool]:
        """Geste d'exploitation, jamais par défaut : `/v1/models` de chaque
        fournisseur disponible, une fois par adresse et clé ; un identifiant absent,
        ou une liste illisible, rend le profil indisponible."""
        groupes: dict[tuple[str, str], list[Profil]] = {}
        for profil in self.profils.values():
            if profil.disponible:
                cle = (adresse_modeles(profil.adresse), profil.variable_cle)
                groupes.setdefault(cle, []).append(profil)
        for (adresse, variable), profils in groupes.items():
            identifiants, statut = await self._lister(adresse, variable)
            absents = [p.nom for p in profils if p.modele not in identifiants]
            for nom in absents:
                self.profils[nom] = replace(self.profils[nom], disponible=False)
            journal.evenement(
                "verification_modeles",
                {
                    "adresse": adresse,
                    "variable_cle": variable,
                    "statut": statut,
                    "profils": [p.nom for p in profils],
                    "absents": absents,
                },
            )
        return {nom: p.disponible for nom, p in self.profils.items()}

    async def _lister(self, adresse: str, variable: str) -> tuple[set[str], str]:
        cle = self._environ.get(variable, "")
        delais = httpx.Timeout(self._ml.connexion_s)
        try:
            reponse = await self._client.get(
                adresse, headers={"Authorization": f"Bearer {cle}"}, timeout=delais
            )
        except httpx.HTTPError:
            return set(), "injoignable"
        if not 200 <= reponse.status_code < 300:
            return set(), str(reponse.status_code)
        try:
            donnees: object = reponse.json()
        except ValueError:
            return set(), "corps_inattendu"
        liste = _lire(donnees, ("data",))
        elements = cast(list[object], liste) if isinstance(liste, list) else []
        ids = {_lire(e, ("id",)) for e in elements}
        return {i for i in ids if isinstance(i, str)}, "ok"

    # ------------------------------------------------------------------ #
    #  L'appel
    # ------------------------------------------------------------------ #
    async def appeler(
        self,
        nom_profil: str,
        identifiant: str,
        role: Role,
        messages: Messages,
        demandes: Demandes,
        schema: Mapping[str, object],
    ) -> Sortie:
        profil = self.profils.get(nom_profil)
        if profil is None:
            raise ErreurFournisseur(
                "profil_inconnu",
                f"Profil inconnu : « {nom_profil} ».",
                {"profil": nom_profil, "profils": list(self.profils)},
            )
        cle = self._environ.get(profil.variable_cle, "")
        if not profil.disponible or not cle.strip():
            raise ErreurFournisseur(
                "profil_indisponible",
                f"Le profil « {profil.nom} » est indisponible.",
                {"profil": profil.nom},
            )
        for reglage, valeur in (
            ("temperature", demandes.temperature),
            ("longueur_max", demandes.longueur_max),
        ):
            if valeur is not None and reglage in profil.reglages_absents:
                raise ErreurFournisseur(
                    "reglage_non_expose",
                    f"Le profil « {profil.nom} » n'expose pas « {reglage} ».",
                    {"reglage": reglage, "profil": profil.nom},
                )
        appliques = self.appliques(profil, demandes)
        corps = self._corps(profil, appliques, messages, schema)
        estimation = estimer_cout(
            profil,
            estimer_jetons(
                messages.systeme + messages.utilisateur, self._ml.caracteres_par_jeton
            ),
            appliques.longueur_max or 0,
        )
        date_appel = datetime.now(UTC)
        debut = self._horloge()
        statut = "erreur_interne"  # une exception imprévue ne se trace pas « ok »
        sortie: Sortie | None = None
        try:
            reponse = await self._poster(profil, role, corps, cle)
            sortie = self._sortie(
                profil, role, reponse, messages, appliques, date_appel, debut
            )
            statut = "ok"
            return sortie
        except ErreurFournisseur as exc:
            statut = exc.code
            raise
        finally:
            latence_s = self._horloge() - debut
            self._compter(profil, role, statut, latence_s, sortie)
            self._tracer(
                identifiant=identifiant,
                role=role,
                profil=profil,
                statut=statut,
                appliques=appliques,
                sortie=sortie,
                estimation=estimation,
                latence_s=latence_s,
                date_appel=date_appel,
                variable_cle=profil.variable_cle,
            )

    def appliques(self, profil: Profil, demandes: Demandes) -> Appliques:
        """Le réglage demandé, sinon le défaut du fichier ; rien pour un réglage que
        le profil déclare absent (il n'est pas envoyé)."""
        reponse = self._ml.reponse
        temperature = (
            None
            if "temperature" in profil.reglages_absents
            else (
                demandes.temperature
                if demandes.temperature is not None
                else reponse.temperature.defaut
            )
        )
        longueur = (
            None
            if "longueur_max" in profil.reglages_absents
            else (
                demandes.longueur_max
                if demandes.longueur_max is not None
                else reponse.longueur_max.defaut
            )
        )
        return Appliques(temperature, longueur, profil.top_p, profil.reasoning_effort)

    def _corps(
        self,
        profil: Profil,
        appliques: Appliques,
        messages: Messages,
        schema: Mapping[str, object],
    ) -> dict[str, object]:
        adaptateur = ADAPTATEURS[profil.fournisseur]
        corps: dict[str, object] = {
            "model": profil.modele,
            "messages": [
                {"role": "system", "content": messages.systeme},
                {"role": "user", "content": messages.utilisateur},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": NOM_SCHEMA,
                    "schema": dict(schema),
                    "strict": True,
                },
            },
        }
        if appliques.temperature is not None:
            corps["temperature"] = appliques.temperature
        if appliques.longueur_max is not None:
            corps[adaptateur.champ_longueur] = appliques.longueur_max
        if appliques.top_p is not None:
            corps["top_p"] = appliques.top_p
        if appliques.reasoning_effort is not None:
            corps["reasoning_effort"] = appliques.reasoning_effort
        return corps

    async def _poster(
        self, profil: Profil, role: Role, corps: dict[str, object], cle: str
    ) -> httpx.Response:
        """La requête ; ses échecs de transport en codes stables, sans leur cause
        (une exception de httpx porte la requête, donc l'en-tête de la clé)."""
        connexion = self._ml.connexion_s
        delais = httpx.Timeout(profil.delai_s, connect=connexion)
        details = {"profil": profil.nom, "role": role}
        try:
            # Le délai de réponse borne l'appel entier, pas seulement chaque lecture.
            async with asyncio.timeout(connexion + profil.delai_s):
                return await self._client.post(
                    profil.adresse,
                    json=corps,
                    headers={"Authorization": f"Bearer {cle}"},
                    timeout=delais,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise ErreurFournisseur(
                "fournisseur_injoignable",
                f"Le fournisseur du profil « {profil.nom} » est injoignable.",
                details,
            ) from None
        except (httpx.TimeoutException, TimeoutError):
            raise ErreurFournisseur(
                "fournisseur_delai",
                f"Le fournisseur du profil « {profil.nom} » n'a pas répondu en"
                f" {profil.delai_s} s.",
                details,
            ) from None
        except httpx.TransportError:
            raise ErreurFournisseur(
                "fournisseur_injoignable",
                f"La connexion au fournisseur du profil « {profil.nom} » a été"
                " interrompue.",
                details,
            ) from None

    def _sortie(
        self,
        profil: Profil,
        role: Role,
        reponse: httpx.Response,
        messages: Messages,
        appliques: Appliques,
        date_appel: datetime,
        debut: float,
    ) -> Sortie:
        details: dict[str, object] = {
            "profil": profil.nom,
            "role": role,
            "statut": reponse.status_code,
        }
        if not 200 <= reponse.status_code < 300:
            code = _code_fournisseur(reponse)
            if code is not None:
                details["code_fournisseur"] = code
            raise ErreurFournisseur(
                "fournisseur_refus",
                f"Le fournisseur a refusé l'appel (statut {reponse.status_code}).",
                details,
            )
        try:
            donnees: object = reponse.json()
        except ValueError:
            donnees = None
        premier = _premier_choix(donnees)
        if _lire(premier, ("finish_reason",)) == "length":
            # La borne de longueur atteinte (le raisonnement compris) : la sortie est
            # coupée, rien n'en est lu.
            raise ErreurFournisseur(
                "fournisseur_refus",
                "Le fournisseur a atteint la longueur maximale avant la fin de la"
                " sortie.",
                details | {"motif": "longueur_atteinte"},
            )
        texte = _lire(premier, ("message", "content"))
        if ADAPTATEURS[profil.fournisseur].morceaux_types and not isinstance(
            texte, str
        ):
            texte = texte_des_morceaux(texte)
        if not isinstance(texte, str):
            raise ErreurFournisseur(
                "fournisseur_refus",
                "Le fournisseur a rendu un corps inattendu.",
                details | {"motif": "corps_inattendu"},
            )
        jetons = self._jetons(profil, _lire(donnees, ("usage",)), messages, texte)
        modele = _lire(donnees, ("model",))
        return Sortie(
            texte=texte,
            jetons=jetons,
            latence_ms=round((self._horloge() - debut) * 1000, 1),
            cout_estime_usd=cout_usd(
                profil, jetons.entree, jetons.sortie, jetons.raisonnement
            ),
            modele_rendu=(
                modele if isinstance(modele, str) and modele != profil.modele else None
            ),
            date_appel=date_appel,
            appliques=appliques,
        )

    def _jetons(
        self, profil: Profil, usage: object, messages: Messages, texte: str
    ) -> Jetons:
        adaptateur = ADAPTATEURS[profil.fournisseur]
        entree = _entier(_lire(usage, ("prompt_tokens",)))
        sortie = _entier(_lire(usage, ("completion_tokens",)))
        if entree is not None and sortie is not None:
            chemin = adaptateur.chemin_raisonnement
            raisonnement = (_entier(_lire(usage, chemin)) if chemin else None) or 0
            if adaptateur.raisonnement_inclus:
                sortie = max(sortie - raisonnement, 0)
            return Jetons(entree, sortie, raisonnement, estimes=False)
        cpj = self._ml.caracteres_par_jeton
        return Jetons(
            estimer_jetons(messages.systeme + messages.utilisateur, cpj),
            estimer_jetons(texte, cpj),
            0,
            estimes=True,
        )

    # ------------------------------------------------------------------ #
    #  Traces
    # ------------------------------------------------------------------ #
    def _compter(
        self,
        profil: Profil,
        role: Role,
        statut: str,
        latence_s: float,
        sortie: Sortie | None,
    ) -> None:
        m = self._metriques
        m.llm_appels.labels(profil=profil.nom, role=role, statut=statut).inc()
        m.llm_latence.labels(profil=profil.nom, role=role).observe(latence_s)
        if sortie is None:
            return
        j = sortie.jetons
        for type_, nombre in (
            ("entree", j.entree),
            ("sortie", j.sortie),
            ("raisonnement", j.raisonnement),
        ):
            m.llm_jetons.labels(profil=profil.nom, role=role, type=type_).inc(nombre)
        if sortie.cout_estime_usd is not None:
            m.llm_cout.labels(profil=profil.nom).inc(sortie.cout_estime_usd)

    def _tracer(
        self,
        *,
        identifiant: str,
        role: Role,
        profil: Profil,
        statut: str,
        appliques: Appliques,
        sortie: Sortie | None,
        estimation: float | None,
        latence_s: float,
        date_appel: datetime,
        variable_cle: str,
    ) -> None:
        """Une ligne par appel : jamais le texte, jamais la clé — son NOM seulement."""
        jetons = sortie.jetons if sortie is not None else None
        journal.appel(
            {
                "identifiant_requete": identifiant,
                "role": role,
                "profil": profil.nom,
                "fournisseur": profil.fournisseur,
                "modele_demande": profil.modele,
                "modele_rendu": sortie.modele_rendu if sortie is not None else None,
                "statut": statut,
                "temperature": appliques.temperature,
                "longueur_max": appliques.longueur_max,
                "top_p": appliques.top_p,
                "reasoning_effort": appliques.reasoning_effort,
                "jetons_entree": jetons.entree if jetons else None,
                "jetons_sortie": jetons.sortie if jetons else None,
                "jetons_raisonnement": jetons.raisonnement if jetons else None,
                "jetons_estimes": jetons.estimes if jetons else None,
                "cout_estime_usd": sortie.cout_estime_usd if sortie else None,
                "cout_estime_avant_usd": estimation,
                "latence_ms": round(latence_s * 1000, 1),
                "date_appel": date_appel.isoformat(timespec="seconds"),
                "variable_cle": variable_cle,
            }
        )


def texte_des_morceaux(contenu: object) -> str | None:
    """Le texte d'un `content` en morceaux typés : les morceaux `text` mis bout à
    bout, les morceaux de raisonnement (`thinking`) ignorés ; None sans morceau de
    texte."""
    if not isinstance(contenu, list):
        return None
    textes: list[str] = []
    for morceau in cast(list[object], contenu):
        texte = _lire(morceau, ("text",))
        if _lire(morceau, ("type",)) == "text" and isinstance(texte, str):
            textes.append(texte)
    return "".join(textes) if textes else None


def _premier_choix(donnees: object) -> object:
    choix = _lire(donnees, ("choices",))
    if isinstance(choix, list) and choix:
        return cast(list[Any], choix)[0]
    return None
