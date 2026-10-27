"""La configuration de l'API : lue par ce seul module, validée au démarrage.

Deux sources, et seulement deux :

- `config/reglages.toml`, versionné : défaut, bornes et motif de chaque réglage ;
- l'environnement (le `.env` du module, hors dépôt) : les secrets et les adresses,
  et les réglages d'exploitation que le fichier déclare surchargeables.

Toute valeur est validée ici ; au moindre écart, `ConfigurationInvalide` les nomme
tous, et l'API ne démarre pas. Aucune valeur de réglage n'est écrite ailleurs.

Le modèle de langage : des profils déclarés dans le fichier, dont chacun nomme la
variable d'environnement de sa clé. Ce module ne lit que la PRÉSENCE de la variable
(un profil sans clé est indisponible, sans être fatal) ; sa valeur n'est lue qu'au
moment de l'appel, par la couche des fournisseurs (`fournisseurs.py`). Le profil
actif doit être disponible : sinon l'API ne démarre pas.
"""

from __future__ import annotations

import ipaddress
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from psycopg import ProgrammingError, conninfo

RACINE_MODULE = Path(__file__).resolve().parents[2]
FICHIER_REGLAGES = RACINE_MODULE / "config" / "reglages.toml"

#: Les versions de l'adresse que ce code sert.
VERSIONS_SERVIES = ("v1",)
#: Une clé : un nom de consommateur, en minuscules ; un secret assez long.
NOM_CLE = re.compile(r"^[a-z0-9_-]+$")
SECRET_MIN = 32
#: La table des vecteurs : interpolée dans la requête du module 10, elle est bornée ici.
TABLE_VECTEURS = re.compile(r"^bench\.vecteurs_[a-z0-9_]+$")
#: Un nom d'hôte ou une adresse, comme `CLIENT_HOTE` du module 09.
HOTE = re.compile(r"^[A-Za-z0-9.:-]+$")
VRAI = ("1", "vrai", "true", "oui")
FAUX = ("0", "faux", "false", "non")

#: Les fournisseurs dont la couche d'appel connaît la forme (`fournisseurs.py`).
FOURNISSEURS = ("mistral", "openai")
STATUTS_PROFIL = ("stable", "apercu")
#: Les réglages de la route qu'un profil peut déclarer absents (règle 2.10).
REGLAGES_EXPOSES = ("temperature", "longueur_max")
#: Les efforts de raisonnement documentés par l'un ou l'autre fournisseur (2026-10-26).
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
NOM_PROFIL = re.compile(r"^[a-z0-9][a-z0-9-]*$")
NOM_VARIABLE = re.compile(r"^[A-Z][A-Z0-9_]*$")
#: La forme commune des fournisseurs : l'adresse d'un profil finit par ce chemin.
CHEMIN_APPEL = "/v1/chat/completions"
#: La clé part dans un en-tête : https, ou http vers le bouclage seul (doublures).
BOUCLAGE = ("127.0.0.1", "localhost", "::1")
CHAMPS_PROFIL = frozenset(
    {
        "fournisseur",
        "modele",
        "adresse",
        "variable_cle",
        "reglages_fixes",
        "reglages_absents",
        "prix_usd_par_million",
        "delai_s",
        "statut",
        "motif",
    }
)

type Reseau = ipaddress.IPv4Network | ipaddress.IPv6Network


class ConfigurationInvalide(Exception):
    """La configuration ne tient pas : l'API refuse de démarrer."""

    def __init__(self, ecarts: list[str]) -> None:
        self.ecarts = ecarts
        super().__init__(
            "configuration invalide :\n" + "\n".join(f"  - {e}" for e in ecarts)
        )


@dataclass(frozen=True, slots=True)
class Cle:
    """Une clé d'accès : le nom du consommateur, et son secret (jamais affiché)."""

    nom: str
    secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Borne:
    """Un réglage entier de la recherche : son défaut et ses bornes, incluses."""

    defaut: int
    min: int
    max: int


@dataclass(frozen=True, slots=True)
class BorneReelle:
    """Un réglage réel : son défaut et ses bornes, incluses."""

    defaut: float
    min: float
    max: float


@dataclass(frozen=True, slots=True)
class Prix:
    """Le prix déclaré d'un profil, en dollars par million de jetons."""

    entree: float
    sortie: float


@dataclass(frozen=True, slots=True)
class Profil:
    """Un profil de modèle de langage. `variable_cle` est le NOM de la variable de sa
    clé, jamais sa valeur ; `disponible` : la variable est présente et non vide."""

    nom: str
    fournisseur: str
    modele: str
    adresse: str
    variable_cle: str
    top_p: float | None
    reasoning_effort: str | None
    reglages_absents: tuple[str, ...]
    prix: Prix | None
    delai_s: float
    statut: str
    disponible: bool


@dataclass(frozen=True, slots=True)
class ReglagesReponse:
    """Les réglages de la route de réponse : défauts et bornes (décision 4)."""

    temperature: BorneReelle
    longueur_max: Borne
    mode_defaut: str
    modes: tuple[str, ...]
    historique_tours_max: int
    historique_caracteres_max: int
    historique_tour_caracteres_max: int
    reformuler: bool
    nombre_series: Borne
    passages_par_serie: Borne
    catalogue_seul: bool


@dataclass(frozen=True, slots=True)
class ModeleLangage:
    """Le profil actif, les profils déclarés, les réglages de la route et de l'appel."""

    profil_actif: str
    profils: tuple[Profil, ...]
    reponse: ReglagesReponse
    caracteres_par_jeton: float
    plafond_run_usd: float
    connexion_s: float
    verifier_modeles: bool

    def profil(self, nom: str) -> Profil | None:
        return next((p for p in self.profils if p.nom == nom), None)


@dataclass(frozen=True, slots=True)
class Contrat:
    """Les réglages qui font partie du contrat `/v1/` : jamais surchargés."""

    nombre_series: Borne
    passages_par_serie: Borne
    catalogue_seul: bool
    longueur_question_max: int


@dataclass(frozen=True, slots=True)
class Reglages:
    cles: tuple[Cle, ...]
    dsn: str = field(repr=False)
    version: str
    nombre_series: Borne
    passages_par_serie: Borne
    catalogue_seul: bool
    longueur_question_max: int
    corps_octets_max: int
    debit_par_minute: int
    delai_service_s: float
    delai_base_s: float
    delai_chargement_s: float
    pool_min: int
    pool_max: int
    pool_attente_s: float
    metriques_reseaux: tuple[Reseau, ...]
    journal_question: bool
    documentation: bool
    instance: str
    table_vecteurs: str
    memoire_besoin_octets: int
    client_hote: str | None
    hote: str
    port: int
    modele_langage: ModeleLangage

    @property
    def contrat(self) -> Contrat:
        return Contrat(
            nombre_series=self.nombre_series,
            passages_par_serie=self.passages_par_serie,
            catalogue_seul=self.catalogue_seul,
            longueur_question_max=self.longueur_question_max,
        )


class _Lecteur:
    """Lit le fichier des réglages en notant chaque écart, sans s'arrêter au premier."""

    def __init__(self, contenu: dict[str, Any], ecarts: list[str]) -> None:
        self.contenu = contenu
        self.ecarts = ecarts

    def _noeud(self, chemin: str) -> dict[str, Any] | None:
        noeud: dict[str, Any] | None = self.contenu
        for partie in chemin.split("."):
            suivant: object = noeud.get(partie) if noeud is not None else None
            noeud = cast(dict[str, Any], suivant) if isinstance(suivant, dict) else None
        if noeud is None:
            self.ecarts.append(f"reglages.toml : table [{chemin}] absente")
        return noeud

    def motif(self, table: dict[str, Any], chemin: str) -> None:
        motif = table.get("motif")
        if not isinstance(motif, str) or not motif.strip():
            self.ecarts.append(f"reglages.toml : [{chemin}] sans motif")

    def table(self, chemin: str) -> dict[str, Any]:
        table = self._noeud(chemin)
        if table is None:
            return {}
        self.motif(table, chemin)
        return table

    def sous_tables(self, chemin: str) -> dict[str, dict[str, Any]]:
        """Les tables filles d'une table de regroupement (sans motif propre)."""
        noeud = self._noeud(chemin) or {}
        tables: dict[str, dict[str, Any]] = {}
        for nom, valeur in noeud.items():
            if isinstance(valeur, dict):
                tables[nom] = cast(dict[str, Any], valeur)
            else:
                self.ecarts.append(f"reglages.toml : [{chemin}] {nom} : une table")
        return tables

    def entier(self, table: dict[str, Any], chemin: str, cle: str, min_: int) -> int:
        valeur = table.get(cle)
        if isinstance(valeur, bool) or not isinstance(valeur, int) or valeur < min_:
            self.ecarts.append(f"reglages.toml : [{chemin}] {cle} : entier ≥ {min_}")
            return min_
        return valeur

    def nombre(self, table: dict[str, Any], chemin: str, cle: str) -> float:
        valeur = table.get(cle)
        if isinstance(valeur, bool) or not isinstance(valeur, int | float):
            self.ecarts.append(f"reglages.toml : [{chemin}] {cle} : nombre > 0")
            return 1.0
        if valeur <= 0:
            self.ecarts.append(f"reglages.toml : [{chemin}] {cle} : nombre > 0")
        return float(valeur)

    def reel(self, table: dict[str, Any], chemin: str, cle: str) -> float:
        valeur = table.get(cle)
        if (
            isinstance(valeur, bool)
            or not isinstance(valeur, int | float)
            or valeur < 0
        ):
            self.ecarts.append(f"reglages.toml : [{chemin}] {cle} : nombre ≥ 0")
            return 0.0
        return float(valeur)

    def booleen(self, table: dict[str, Any], chemin: str, cle: str) -> bool:
        valeur = table.get(cle)
        if not isinstance(valeur, bool):
            self.ecarts.append(f"reglages.toml : [{chemin}] {cle} : vrai ou faux")
            return False
        return valeur

    def texte(self, table: dict[str, Any], chemin: str, cle: str) -> str:
        valeur = table.get(cle)
        if not isinstance(valeur, str) or not valeur.strip():
            self.ecarts.append(f"reglages.toml : [{chemin}] {cle} : texte non vide")
            return ""
        return valeur

    def textes(
        self, table: dict[str, Any], chemin: str, cle: str, *, vide_admis: bool = False
    ) -> tuple[str, ...]:
        valeur = table.get(cle)
        elements = cast(list[object], valeur) if isinstance(valeur, list) else None
        if (
            elements is None
            or not all(isinstance(e, str) and e.strip() for e in elements)
            or (not elements and not vide_admis)
        ):
            self.ecarts.append(f"reglages.toml : [{chemin}] {cle} : liste de textes")
            return ()
        return tuple(cast(list[str], elements))

    def borne(self, chemin: str) -> Borne:
        t = self.table(chemin)
        borne = Borne(
            defaut=self.entier(t, chemin, "defaut", 1),
            min=self.entier(t, chemin, "min", 1),
            max=self.entier(t, chemin, "max", 1),
        )
        if not borne.min <= borne.defaut <= borne.max:
            self.ecarts.append(
                f"reglages.toml : [{chemin}] défaut {borne.defaut} hors de"
                f" [{borne.min}, {borne.max}]"
            )
        return borne


def lire_cles(valeur: str | None, ecarts: list[str]) -> tuple[Cle, ...]:
    """`nom:secret,nom:secret` — défaillance fermée : vide ou fautif, aucune clé.

    Repris du trousseau du module 02 (`app/settings.py`, `parse_api_keys`).
    """
    if valeur is None or not valeur.strip():
        ecarts.append("API_MODELE_CLES : absente ou vide — aucune clé, aucun accès")
        return ()
    cles: list[Cle] = []
    for numero, element in enumerate(valeur.split(","), start=1):
        nom, separateur, secret = element.strip().partition(":")
        if not separateur:
            ecarts.append(f"API_MODELE_CLES : élément {numero} sans « nom:secret »")
            continue
        if not NOM_CLE.fullmatch(nom):
            ecarts.append(f"API_MODELE_CLES : élément {numero}, nom invalide")
            continue
        if len(secret) < SECRET_MIN:
            ecarts.append(
                f"API_MODELE_CLES : la clé « {nom} » a moins de {SECRET_MIN} caractères"
            )
            continue
        cles.append(Cle(nom=nom, secret=secret))
    noms = [c.nom for c in cles]
    if len(set(noms)) != len(noms):
        ecarts.append("API_MODELE_CLES : un nom de consommateur en double")
    secrets_ = [c.secret for c in cles]
    if len(set(secrets_)) != len(secrets_):
        ecarts.append("API_MODELE_CLES : un même secret pour deux consommateurs")
    return tuple(cles)


def lire_dsn(valeur: str | None, ecarts: list[str]) -> str:
    if valeur is None or not valeur.strip():
        ecarts.append("API_MODELE_DSN : absente")
        return ""
    try:
        parametres = conninfo.conninfo_to_dict(valeur)
    except ProgrammingError:
        ecarts.append("API_MODELE_DSN : illisible")
        return ""
    if parametres.get("password"):
        ecarts.append(
            "API_MODELE_DSN : porte un mot de passe — il va dans ~/.pgpass, pas dans"
            " le DSN"
        )
    return valeur


def lire_reseaux(
    valeurs: list[str], source: str, ecarts: list[str]
) -> tuple[Reseau, ...]:
    reseaux: list[Reseau] = []
    for valeur in valeurs:
        try:
            reseaux.append(ipaddress.ip_network(valeur.strip()))
        except ValueError:
            ecarts.append(f"{source} : réseau illisible « {valeur.strip()} »")
    if not reseaux:
        ecarts.append(f"{source} : aucun réseau")
    return tuple(reseaux)


def _surcharge_entiere(
    environ: Mapping[str, str],
    nom: str,
    defaut: int,
    min_: int,
    max_: int,
    ecarts: list[str],
) -> int:
    valeur = environ.get(nom)
    if valeur is None or valeur == "":
        return defaut
    try:
        entier = int(valeur)
    except ValueError:
        ecarts.append(f"{nom} : entier attendu")
        return defaut
    if not min_ <= entier <= max_:
        ecarts.append(f"{nom} : entre {min_} et {max_}")
    return entier


def _surcharge_booleenne(
    environ: Mapping[str, str], nom: str, defaut: bool, ecarts: list[str]
) -> bool:
    valeur = environ.get(nom)
    if valeur is None or valeur == "":
        return defaut
    if valeur.strip().lower() in VRAI:
        return True
    if valeur.strip().lower() in FAUX:
        return False
    ecarts.append(f"{nom} : vrai ou faux")
    return defaut


def _surcharge_reelle(
    environ: Mapping[str, str], nom: str, defaut: float, ecarts: list[str]
) -> float:
    valeur = environ.get(nom)
    if valeur is None or valeur == "":
        return defaut
    try:
        reel = float(valeur)
    except ValueError:
        ecarts.append(f"{nom} : nombre attendu")
        return defaut
    if not reel > 0:
        ecarts.append(f"{nom} : nombre > 0")
    return reel


def _hote(valeur: str | None, nom: str, ecarts: list[str]) -> str | None:
    if valeur is None or valeur == "":
        return None
    if not HOTE.fullmatch(valeur):
        ecarts.append(f"{nom} : hôte invalide")
        return None
    return valeur


def _lecteur(fichier: Path, ecarts: list[str]) -> _Lecteur:
    try:
        contenu = tomllib.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationInvalide([f"{fichier.name} : illisible ({exc})"]) from exc
    return _Lecteur(contenu, ecarts)


def _contrat(lecteur: _Lecteur) -> Contrat:
    nombre_series = lecteur.borne("recherche.nombre_series")
    passages = lecteur.borne("recherche.passages_par_serie")
    t = lecteur.table("recherche.catalogue_seul")
    catalogue_seul = lecteur.booleen(t, "recherche.catalogue_seul", "defaut")
    t = lecteur.table("question")
    longueur = lecteur.entier(t, "question", "longueur_max", 1)
    return Contrat(nombre_series, passages, catalogue_seul, longueur)


def _borne_de_route(
    lecteur: _Lecteur, table: dict[str, Any], chemin: str, cle: str, bornes: Borne
) -> Borne:
    """Un défaut propre à la route de réponse, sous les bornes de la recherche."""
    defaut = lecteur.entier(table, chemin, cle, 1)
    if not bornes.min <= defaut <= bornes.max:
        lecteur.ecarts.append(
            f"reglages.toml : [{chemin}] {cle} {defaut} hors de"
            f" [{bornes.min}, {bornes.max}] (bornes de la recherche)"
        )
    return Borne(defaut=defaut, min=bornes.min, max=bornes.max)


def _reglages_reponse(lecteur: _Lecteur, contrat: Contrat) -> ReglagesReponse:
    chemin = "modele_langage.temperature"
    t = lecteur.table(chemin)
    temperature = BorneReelle(
        defaut=lecteur.reel(t, chemin, "defaut"),
        min=lecteur.reel(t, chemin, "min"),
        max=lecteur.reel(t, chemin, "max"),
    )
    if not temperature.min <= temperature.defaut <= temperature.max:
        lecteur.ecarts.append(
            f"reglages.toml : [{chemin}] défaut {temperature.defaut} hors de"
            f" [{temperature.min}, {temperature.max}]"
        )
    longueur = lecteur.borne("modele_langage.longueur_max")
    chemin = "modele_langage.mode"
    t = lecteur.table(chemin)
    mode_defaut = lecteur.texte(t, chemin, "defaut")
    modes = lecteur.textes(t, chemin, "valeurs")
    if modes and mode_defaut not in modes:
        lecteur.ecarts.append(f"reglages.toml : [{chemin}] défaut hors des valeurs")
    chemin = "modele_langage.historique"
    t = lecteur.table(chemin)
    tours = lecteur.entier(t, chemin, "tours_max", 1)
    caracteres = lecteur.entier(t, chemin, "caracteres_max", 1)
    tour_caracteres = lecteur.entier(t, chemin, "tour_caracteres_max", 1)
    chemin = "modele_langage.reformuler"
    t = lecteur.table(chemin)
    reformuler = lecteur.booleen(t, chemin, "defaut")
    chemin = "modele_langage.recherche"
    t = lecteur.table(chemin)
    return ReglagesReponse(
        temperature=temperature,
        longueur_max=longueur,
        mode_defaut=mode_defaut,
        modes=modes,
        historique_tours_max=tours,
        historique_caracteres_max=caracteres,
        historique_tour_caracteres_max=tour_caracteres,
        reformuler=reformuler,
        nombre_series=_borne_de_route(
            lecteur, t, chemin, "nombre_series", contrat.nombre_series
        ),
        passages_par_serie=_borne_de_route(
            lecteur, t, chemin, "passages_par_serie", contrat.passages_par_serie
        ),
        catalogue_seul=lecteur.booleen(t, chemin, "catalogue_seul"),
    )


def _adresse(adresse: str, chemin: str, ecarts: list[str]) -> None:
    morceaux = urlsplit(adresse)
    if not morceaux.path.endswith(CHEMIN_APPEL) or morceaux.query or morceaux.fragment:
        ecarts.append(f"reglages.toml : [{chemin}] adresse : …{CHEMIN_APPEL} attendu")
    locale = morceaux.scheme == "http" and morceaux.hostname in BOUCLAGE
    if morceaux.scheme != "https" and not locale:
        ecarts.append(
            f"reglages.toml : [{chemin}] adresse : https exigé (la clé part dans un"
            " en-tête) ; http sur le bouclage seul"
        )


def _reglages_fixes(
    lecteur: _Lecteur, table: dict[str, Any], chemin: str
) -> tuple[float | None, str | None]:
    """`top_p` et `reasoning_effort` fixés par le profil, jamais exposés."""
    valeur = table.get("reglages_fixes")
    if not isinstance(valeur, dict):
        lecteur.ecarts.append(f"reglages.toml : [{chemin}] reglages_fixes : une table")
        return None, None
    fixes = cast(dict[str, Any], valeur)
    inconnus = sorted(set(fixes) - {"top_p", "reasoning_effort"})
    if inconnus:
        lecteur.ecarts.append(
            f"reglages.toml : [{chemin}] reglages_fixes : inconnus"
            f" {', '.join(inconnus)}"
        )
    top_p: float | None = None
    if "top_p" in fixes:
        top_p = lecteur.reel(fixes, chemin, "top_p")
        if top_p > 1:
            lecteur.ecarts.append(f"reglages.toml : [{chemin}] top_p : entre 0 et 1")
    effort: str | None = None
    if "reasoning_effort" in fixes:
        effort = lecteur.texte(fixes, chemin, "reasoning_effort")
        if effort and effort not in EFFORTS:
            lecteur.ecarts.append(
                f"reglages.toml : [{chemin}] reasoning_effort : l'un de"
                f" {', '.join(EFFORTS)}"
            )
    return top_p, effort


def _prix(lecteur: _Lecteur, table: dict[str, Any], chemin: str) -> Prix | None:
    """Un prix absent est permis : le coût sera nul, « prix non déclaré »."""
    if "prix_usd_par_million" not in table:
        return None
    valeur = table["prix_usd_par_million"]
    if not isinstance(valeur, dict):
        lecteur.ecarts.append(
            f"reglages.toml : [{chemin}] prix_usd_par_million : une table"
        )
        return None
    prix = cast(dict[str, Any], valeur)
    return Prix(
        entree=lecteur.reel(prix, chemin, "entree"),
        sortie=lecteur.reel(prix, chemin, "sortie"),
    )


def _profil(
    lecteur: _Lecteur,
    nom: str,
    table: dict[str, Any],
    environ: Mapping[str, str],
    reponse_s: float,
) -> Profil:
    chemin = f"modele_langage.profils.{nom}"
    ecarts = lecteur.ecarts
    lecteur.motif(table, chemin)
    if not NOM_PROFIL.fullmatch(nom):
        ecarts.append(f"reglages.toml : [{chemin}] nom de profil invalide")
    inconnus = sorted(set(table) - CHAMPS_PROFIL)
    if inconnus:
        ecarts.append(
            f"reglages.toml : [{chemin}] champs inconnus : {', '.join(inconnus)}"
        )
    fournisseur = lecteur.texte(table, chemin, "fournisseur")
    if fournisseur and fournisseur not in FOURNISSEURS:
        ecarts.append(
            f"reglages.toml : [{chemin}] fournisseur : l'un de"
            f" {', '.join(FOURNISSEURS)}"
        )
    modele = lecteur.texte(table, chemin, "modele")
    adresse = lecteur.texte(table, chemin, "adresse")
    if adresse:
        _adresse(adresse, chemin, ecarts)
    variable = lecteur.texte(table, chemin, "variable_cle")
    if variable and not NOM_VARIABLE.fullmatch(variable):
        ecarts.append(f"reglages.toml : [{chemin}] variable_cle : un nom de variable")
    top_p, effort = _reglages_fixes(lecteur, table, chemin)
    absents = lecteur.textes(table, chemin, "reglages_absents", vide_admis=True)
    hors = [a for a in absents if a not in REGLAGES_EXPOSES]
    if hors:
        ecarts.append(
            f"reglages.toml : [{chemin}] reglages_absents : {', '.join(hors)} n'est pas"
            f" un réglage exposé ({', '.join(REGLAGES_EXPOSES)})"
        )
    statut = lecteur.texte(table, chemin, "statut")
    if statut and statut not in STATUTS_PROFIL:
        ecarts.append(
            f"reglages.toml : [{chemin}] statut : l'un de {', '.join(STATUTS_PROFIL)}"
        )
    return Profil(
        nom=nom,
        fournisseur=fournisseur,
        modele=modele,
        adresse=adresse,
        variable_cle=variable,
        top_p=top_p,
        reasoning_effort=effort,
        reglages_absents=absents,
        prix=_prix(lecteur, table, chemin),
        delai_s=(
            lecteur.nombre(table, chemin, "delai_s")
            if "delai_s" in table
            else reponse_s
        ),
        statut=statut,
        # La présence de la variable, jamais sa valeur.
        disponible=bool(variable) and bool(environ.get(variable, "").strip()),
    )


def _modele_langage(
    lecteur: _Lecteur, contrat: Contrat, environ: Mapping[str, str]
) -> ModeleLangage:
    ecarts = lecteur.ecarts
    t = lecteur.table("modele_langage")
    actif_fichier = lecteur.texte(t, "modele_langage", "profil_actif")
    reponse = _reglages_reponse(lecteur, contrat)
    chemin = "modele_langage.estimation"
    t = lecteur.table(chemin)
    caracteres_par_jeton = lecteur.nombre(t, chemin, "caracteres_par_jeton")
    chemin = "modele_langage.cout"
    t = lecteur.table(chemin)
    plafond = lecteur.nombre(t, chemin, "plafond_run_usd")
    chemin = "modele_langage.delais"
    t = lecteur.table(chemin)
    connexion_s = lecteur.nombre(t, chemin, "connexion_s")
    reponse_s = lecteur.nombre(t, chemin, "reponse_s")
    chemin = "modele_langage.verification_demarrage"
    t = lecteur.table(chemin)
    verifier = lecteur.booleen(t, chemin, "appeler_fournisseurs")
    profils = tuple(
        _profil(lecteur, nom, table, environ, reponse_s)
        for nom, table in lecteur.sous_tables("modele_langage.profils").items()
    )
    noms = [p.nom for p in profils]
    if not profils:
        ecarts.append("reglages.toml : [modele_langage.profils] aucun profil déclaré")
    actif = environ.get("API_MODELE_PROFIL_ACTIF") or actif_fichier
    profil = next((p for p in profils if p.nom == actif), None)
    if profil is None:
        ecarts.append(
            f"profil actif « {actif} » : pas un profil déclaré ({', '.join(noms)})"
        )
    elif not profil.disponible:
        ecarts.append(
            f"profil actif « {actif} » : variable {profil.variable_cle} absente ou"
            " vide — la clé du profil actif est exigée"
        )
    return ModeleLangage(
        profil_actif=actif,
        profils=profils,
        reponse=reponse,
        caracteres_par_jeton=caracteres_par_jeton,
        plafond_run_usd=_surcharge_reelle(environ, "COUT_PLAFOND_RUN", plafond, ecarts),
        connexion_s=connexion_s,
        verifier_modeles=_surcharge_booleenne(
            environ, "API_MODELE_VERIFIER_MODELES", verifier, ecarts
        ),
    )


def lire_contrat(fichier: Path = FICHIER_REGLAGES) -> Contrat:
    """Les seuls réglages du contrat, sans l'environnement : les formes de l'API en
    dérivent (bornes et défauts de la recherche, longueur de la question)."""
    ecarts: list[str] = []
    contrat = _contrat(_lecteur(fichier, ecarts))
    if ecarts:
        raise ConfigurationInvalide(ecarts)
    return contrat


def lire_reglages_reponse(fichier: Path = FICHIER_REGLAGES) -> ReglagesReponse:
    """Les réglages de la route de réponse, sans l'environnement : les formes de
    `/v1/reponse` en dérivent (défauts et bornes publiés au contrat)."""
    ecarts: list[str] = []
    lecteur = _lecteur(fichier, ecarts)
    reponse = _reglages_reponse(lecteur, _contrat(lecteur))
    if ecarts:
        raise ConfigurationInvalide(ecarts)
    return reponse


def besoin_memoire(fichier: Path = FICHIER_REGLAGES) -> int:
    """Le pic de mémoire vive de l'API, en octets (seuils de `verify-poste`)."""
    ecarts: list[str] = []
    lecteur = _lecteur(fichier, ecarts)
    besoin = lecteur.entier(lecteur.table("memoire"), "memoire", "besoin_octets", 1)
    if ecarts:
        raise ConfigurationInvalide(ecarts)
    return besoin


def charger(environ: Mapping[str, str], fichier: Path = FICHIER_REGLAGES) -> Reglages:
    """Les réglages validés ; `ConfigurationInvalide` au premier passage fautif."""
    ecarts: list[str] = []
    lecteur = _lecteur(fichier, ecarts)
    contrat = _contrat(lecteur)
    t = lecteur.table("corps")
    corps = lecteur.entier(t, "corps", "octets_max", 1)
    t = lecteur.table("debit")
    debit = lecteur.entier(t, "debit", "par_minute", 1)
    t = lecteur.table("delais")
    delai_service = lecteur.nombre(t, "delais", "service_s")
    delai_base = lecteur.nombre(t, "delais", "base_s")
    delai_chargement = lecteur.nombre(t, "delais", "chargement_s")
    t = lecteur.table("pool")
    pool_min = lecteur.entier(t, "pool", "min", 1)
    pool_max = lecteur.entier(t, "pool", "max", 1)
    pool_attente = lecteur.nombre(t, "pool", "attente_s")
    if pool_min > pool_max:
        ecarts.append("reglages.toml : [pool] min > max")
    t = lecteur.table("metriques")
    brut = t.get("reseaux")
    reseaux_fichier = (
        [r for r in cast(list[object], brut) if isinstance(r, str)]
        if isinstance(brut, list)
        else []
    )
    t = lecteur.table("journal")
    journal_question = lecteur.booleen(t, "journal", "question_en_clair")
    t = lecteur.table("documentation")
    documentation = lecteur.booleen(t, "documentation", "interactive")
    t = lecteur.table("service")
    instance = lecteur.texte(t, "service", "instance")
    table_vecteurs = lecteur.texte(t, "service", "table")
    if table_vecteurs and not TABLE_VECTEURS.fullmatch(table_vecteurs):
        ecarts.append(
            "reglages.toml : [service] table : bench.vecteurs_<modèle> attendu"
        )
    t = lecteur.table("memoire")
    besoin = lecteur.entier(t, "memoire", "besoin_octets", 1)
    t = lecteur.table("serveur")
    hote_fichier = lecteur.texte(t, "serveur", "hote")
    port_fichier = lecteur.entier(t, "serveur", "port", 1)
    modele_langage = _modele_langage(lecteur, contrat, environ)

    version = environ.get("API_MODELE_VERSION") or VERSIONS_SERVIES[0]
    if version not in VERSIONS_SERVIES:
        ecarts.append(
            f"API_MODELE_VERSION : « {version} » n'est pas servie"
            f" ({', '.join(VERSIONS_SERVIES)})"
        )
    reseaux_env = environ.get("API_MODELE_METRIQUES_RESEAUX")
    if reseaux_env:
        reseaux = lire_reseaux(
            reseaux_env.split(","), "API_MODELE_METRIQUES_RESEAUX", ecarts
        )
    else:
        reseaux = lire_reseaux(reseaux_fichier, "reglages.toml : [metriques]", ecarts)

    reglages = Reglages(
        cles=lire_cles(environ.get("API_MODELE_CLES"), ecarts),
        dsn=lire_dsn(environ.get("API_MODELE_DSN"), ecarts),
        version=version,
        nombre_series=contrat.nombre_series,
        passages_par_serie=contrat.passages_par_serie,
        catalogue_seul=contrat.catalogue_seul,
        longueur_question_max=contrat.longueur_question_max,
        corps_octets_max=corps,
        debit_par_minute=_surcharge_entiere(
            environ, "API_MODELE_DEBIT_PAR_MINUTE", debit, 1, 100_000, ecarts
        ),
        delai_service_s=delai_service,
        delai_base_s=delai_base,
        delai_chargement_s=delai_chargement,
        pool_min=pool_min,
        pool_max=pool_max,
        pool_attente_s=pool_attente,
        metriques_reseaux=reseaux,
        journal_question=_surcharge_booleenne(
            environ, "API_MODELE_JOURNAL_QUESTION", journal_question, ecarts
        ),
        documentation=_surcharge_booleenne(
            environ, "API_MODELE_DOCUMENTATION", documentation, ecarts
        ),
        instance=instance,
        table_vecteurs=table_vecteurs,
        memoire_besoin_octets=besoin,
        client_hote=_hote(environ.get("CLIENT_HOTE"), "CLIENT_HOTE", ecarts),
        hote=_hote(environ.get("API_MODELE_HOTE"), "API_MODELE_HOTE", ecarts)
        or hote_fichier,
        port=_surcharge_entiere(
            environ, "API_MODELE_PORT", port_fichier, 1, 65_535, ecarts
        ),
        modele_langage=modele_langage,
    )
    if ecarts:
        raise ConfigurationInvalide(ecarts)
    return reglages
