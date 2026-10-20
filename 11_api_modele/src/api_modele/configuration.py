"""La configuration de l'API : lue par ce seul module, validée au démarrage.

Deux sources, et seulement deux :

- `config/reglages.toml`, versionné : défaut, bornes et motif de chaque réglage ;
- l'environnement (le `.env` du module, hors dépôt) : les secrets et les adresses,
  et les réglages d'exploitation que le fichier déclare surchargeables.

Toute valeur est validée ici ; au moindre écart, `ConfigurationInvalide` les nomme
tous, et l'API ne démarre pas. Aucune valeur de réglage n'est écrite ailleurs.
"""

from __future__ import annotations

import ipaddress
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from psycopg import ProgrammingError, conninfo

RACINE_MODULE = Path(__file__).resolve().parents[2]
FICHIER_REGLAGES = RACINE_MODULE / "config" / "reglages.toml"

#: Les versions de l'adresse que ce code sert.
VERSIONS_SERVIES = ("v1",)
#: Une clé : un nom de consommateur, en minuscules ; un secret assez long.
NOM_CLE = re.compile(r"^[a-z0-9_-]+$")
SECRET_MIN = 32
#: Un nom d'hôte ou une adresse, comme `CLIENT_HOTE` du module 09.
HOTE = re.compile(r"^[A-Za-z0-9.:-]+$")
VRAI = ("1", "vrai", "true", "oui")
FAUX = ("0", "faux", "false", "non")

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
    pool_min: int
    pool_max: int
    pool_attente_s: float
    metriques_reseaux: tuple[Reseau, ...]
    journal_question: bool
    instance: str
    client_hote: str | None
    hote: str
    port: int


class _Lecteur:
    """Lit le fichier des réglages en notant chaque écart, sans s'arrêter au premier."""

    def __init__(self, contenu: dict[str, Any], ecarts: list[str]) -> None:
        self.contenu = contenu
        self.ecarts = ecarts

    def table(self, chemin: str) -> dict[str, Any]:
        noeud: dict[str, Any] | None = self.contenu
        for partie in chemin.split("."):
            suivant: object = noeud.get(partie) if noeud is not None else None
            noeud = cast(dict[str, Any], suivant) if isinstance(suivant, dict) else None
        if noeud is None:
            self.ecarts.append(f"reglages.toml : table [{chemin}] absente")
            return {}
        table = noeud
        motif = table.get("motif")
        if not isinstance(motif, str) or not motif.strip():
            self.ecarts.append(f"reglages.toml : [{chemin}] sans motif")
        return table

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


def _hote(valeur: str | None, nom: str, ecarts: list[str]) -> str | None:
    if valeur is None or valeur == "":
        return None
    if not HOTE.fullmatch(valeur):
        ecarts.append(f"{nom} : hôte invalide")
        return None
    return valeur


def charger(environ: Mapping[str, str], fichier: Path = FICHIER_REGLAGES) -> Reglages:
    """Les réglages validés ; `ConfigurationInvalide` au premier passage fautif."""
    ecarts: list[str] = []
    try:
        contenu = tomllib.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationInvalide([f"{fichier.name} : illisible ({exc})"]) from exc
    lecteur = _Lecteur(contenu, ecarts)

    nombre_series = lecteur.borne("recherche.nombre_series")
    passages = lecteur.borne("recherche.passages_par_serie")
    t = lecteur.table("recherche.catalogue_seul")
    catalogue_seul = lecteur.booleen(t, "recherche.catalogue_seul", "defaut")
    t = lecteur.table("question")
    longueur = lecteur.entier(t, "question", "longueur_max", 1)
    t = lecteur.table("corps")
    corps = lecteur.entier(t, "corps", "octets_max", 1)
    t = lecteur.table("debit")
    debit = lecteur.entier(t, "debit", "par_minute", 1)
    t = lecteur.table("delais")
    delai_service = lecteur.nombre(t, "delais", "service_s")
    delai_base = lecteur.nombre(t, "delais", "base_s")
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
    t = lecteur.table("service")
    instance = lecteur.texte(t, "service", "instance")
    t = lecteur.table("serveur")
    hote_fichier = lecteur.texte(t, "serveur", "hote")
    port_fichier = lecteur.entier(t, "serveur", "port", 1)

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
        nombre_series=nombre_series,
        passages_par_serie=passages,
        catalogue_seul=catalogue_seul,
        longueur_question_max=longueur,
        corps_octets_max=corps,
        debit_par_minute=_surcharge_entiere(
            environ, "API_MODELE_DEBIT_PAR_MINUTE", debit, 1, 100_000, ecarts
        ),
        delai_service_s=delai_service,
        delai_base_s=delai_base,
        pool_min=pool_min,
        pool_max=pool_max,
        pool_attente_s=pool_attente,
        metriques_reseaux=reseaux,
        journal_question=_surcharge_booleenne(
            environ, "API_MODELE_JOURNAL_QUESTION", journal_question, ecarts
        ),
        instance=instance,
        client_hote=_hote(environ.get("CLIENT_HOTE"), "CLIENT_HOTE", ecarts),
        hote=_hote(environ.get("API_MODELE_HOTE"), "API_MODELE_HOTE", ecarts)
        or hote_fichier,
        port=_surcharge_entiere(
            environ, "API_MODELE_PORT", port_fichier, 1, 65_535, ecarts
        ),
    )
    if ecarts:
        raise ConfigurationInvalide(ecarts)
    return reglages
