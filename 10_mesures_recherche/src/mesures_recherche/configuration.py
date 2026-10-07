"""Lecture de `config/mesures.toml` — la seule source des réglages."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

RACINE_MODULE = Path(__file__).resolve().parents[2]
RACINE_DEPOT = RACINE_MODULE.parent
FICHIER = RACINE_MODULE / "config" / "mesures.toml"


class ConfigurationInvalide(Exception):
    pass


def charger(chemin: Path = FICHIER) -> dict:
    config = tomllib.loads(chemin.read_text(encoding="utf-8"))
    numeros = [m["numero"] for m in config["mesures"]]
    if numeros != list(range(1, len(numeros) + 1)):
        raise ConfigurationInvalide(f"mesures numérotées 1..n attendues : {numeros}")
    for m in config["mesures"]:
        if m["type"] == "fusion" and not all(s < m["numero"] for s in m["sources"]):
            raise ConfigurationInvalide(f"fusion {m['numero']} : sources postérieures")
    for a, b in config["bootstrap"]["comparaisons"]:
        if not {a, b} <= set(numeros):
            raise ConfigurationInvalide(f"comparaison {a}–{b} : mesure inconnue")
    return config


def mesure(config: dict, numero: int) -> dict:
    for m in config["mesures"]:
        if m["numero"] == numero:
            return m
    raise ConfigurationInvalide(f"mesure {numero} absente de la configuration")


def empreinte(chemin: Path = FICHIER) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()
