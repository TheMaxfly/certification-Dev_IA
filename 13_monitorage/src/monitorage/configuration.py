"""La configuration de la sonde et de l'exporteur : des variables d'environnement.

Le DSN de lecture ne porte jamais de mot de passe : il est dans un pgpass monté. Un DSN
qui en porte un empêche de démarrer, et le message ne le répète pas.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict


class ConfigurationInvalide(Exception):
    """Une variable manque ou ne tient pas ; le message ne porte aucune valeur."""


@dataclass(frozen=True)
class Configuration:
    hote: str
    port: int
    periode_s: float
    delai_initial_s: float
    encodage: int
    hote_service: str
    journal: Path | None
    echantillon: Path | None
    lecture_base: bool
    periode_base_s: float
    #: Hors du `repr` : ni journal ni trace ne le montrent.
    dsn: str | None = field(default=None, repr=False)


def _nombre(environ: Mapping[str, str], nom: str, defaut: float) -> float:
    brut = environ.get(nom)
    if brut is None or brut == "":
        return defaut
    try:
        valeur = float(brut)
    except ValueError:
        raise ConfigurationInvalide(f"{nom} : un nombre est attendu") from None
    if valeur < 0:
        raise ConfigurationInvalide(f"{nom} : un nombre positif est attendu")
    return valeur


def _chemin(environ: Mapping[str, str], nom: str) -> Path | None:
    brut = environ.get(nom)
    return Path(brut) if brut else None


def _dsn(environ: Mapping[str, str]) -> str | None:
    brut = environ.get("MONITORAGE_DSN_LECTURE") or None
    if brut is None:
        return None
    try:
        champs = conninfo_to_dict(brut)
    except ProgrammingError:
        raise ConfigurationInvalide("MONITORAGE_DSN_LECTURE : illisible") from None
    if champs.get("password"):
        raise ConfigurationInvalide(
            "MONITORAGE_DSN_LECTURE : aucun mot de passe dans le DSN ; il va dans un"
            " pgpass monté"
        )
    return brut


def charger(environ: Mapping[str, str]) -> Configuration:
    hote, separateur, port = environ.get("SONDE_ECOUTE", "127.0.0.1:9108").partition(
        ":"
    )
    if not separateur or not port.isdecimal():
        raise ConfigurationInvalide("SONDE_ECOUTE : hôte:port attendu")
    encodage = environ.get("SONDE_ENCODAGE", "3")
    if not encodage.isdecimal():
        raise ConfigurationInvalide("SONDE_ENCODAGE : un identifiant est attendu")
    lecture = environ.get("MONITORAGE_LECTURE_BASE", "0")
    if lecture not in ("0", "1"):
        raise ConfigurationInvalide("MONITORAGE_LECTURE_BASE : 0 ou 1")
    dsn = _dsn(environ)
    if lecture == "1" and dsn is None:
        raise ConfigurationInvalide(
            "MONITORAGE_LECTURE_BASE=1 demande MONITORAGE_DSN_LECTURE"
        )
    return Configuration(
        hote=hote,
        port=int(port),
        periode_s=_nombre(environ, "SONDE_PERIODE_S", 900.0),
        delai_initial_s=_nombre(environ, "SONDE_DELAI_INITIAL_S", 60.0),
        encodage=int(encodage),
        hote_service=environ.get("SONDE_HOTE_SERVICE", "embedding"),
        journal=_chemin(environ, "SONDE_JOURNAL"),
        echantillon=_chemin(environ, "SONDE_ECHANTILLON"),
        lecture_base=lecture == "1",
        periode_base_s=_nombre(environ, "SONDE_PERIODE_BASE_S", 300.0),
        dsn=dsn,
    )
