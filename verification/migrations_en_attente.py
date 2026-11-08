"""Les migrations attendues en attente en production : la liste versionnée, le contrôle.

`verification/migrations_en_attente.tsv` déclare les migrations présentes au dépôt et
volontairement absentes de la production (une migration appliquée en préproduction
d'abord, livrée plus tard). L'étape « état d'apimanga » de `make verify-poste` admet :
migrations appliquées = fichiers du dépôt moins la liste, sommes de contrôle égales pour
les appliquées. Tout autre écart est une erreur, nommée :

- une migration du dépôt absente de la production, sans être listée ;
- une migration listée, mais appliquée (la liste est périmée) ;
- une migration listée, sans fichier au dépôt ;
- une migration appliquée, sans fichier au dépôt ;
- une migration appliquée dont la somme de contrôle diffère du fichier.

Bibliothèque standard seule : lu par `etat_apimanga.sh` dans l'environnement de
`database/`, et par ses essais (`tester_migrations_en_attente.py`) sans environnement.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

LISTE = Path(__file__).resolve().parent / "migrations_en_attente.tsv"
VERSION = re.compile(r"^\d{3}$")


class ListeInvalide(Exception):
    """La liste versionnée ne se lit pas : le contrôle ne peut pas conclure."""


def lire(chemin: Path = LISTE) -> dict[str, str]:
    """Version → motif. Ligne fautive, version mal formée ou doublon : refus."""
    attendues: dict[str, str] = {}
    for numero, ligne in enumerate(chemin.read_text(encoding="utf-8").splitlines(), 1):
        if not ligne.strip() or ligne.startswith("#"):
            continue
        champs = ligne.split("\t")
        if len(champs) != 3 or not all(c.strip() for c in champs):
            raise ListeInvalide(f"{chemin.name}:{numero} : trois champs attendus")
        version = champs[0].strip()
        if not VERSION.match(version):
            raise ListeInvalide(f"{chemin.name}:{numero} : version « {version} »")
        if version in attendues:
            raise ListeInvalide(f"{chemin.name}:{numero} : {version} listée deux fois")
        attendues[version] = champs[1].strip()
    return attendues


def ecarts(
    base: Mapping[str, str],
    fichiers: Mapping[str, str],
    attendues: Mapping[str, str],
) -> list[str]:
    """Les écarts, nommés ; une liste vide : la base est égale aux fichiers, hors
    attentes déclarées. `base` et `fichiers` : version → somme de contrôle."""
    trouves: list[str] = []
    for version in sorted(attendues):
        if version not in fichiers:
            trouves.append(f"{version} : listée en attente, sans fichier au dépôt")
        elif version in base:
            trouves.append(
                f"{version} : listée en attente, mais appliquée (liste périmée)"
            )
    for version in sorted(set(fichiers) - set(base) - set(attendues)):
        trouves.append(f"{version} : absente de la base, sans être listée en attente")
    for version in sorted(set(base) - set(fichiers)):
        trouves.append(f"{version} : appliquée, sans fichier au dépôt")
    for version in sorted(set(base) & set(fichiers)):
        if base[version] != fichiers[version]:
            trouves.append(f"{version} : somme de contrôle différente du fichier")
    return trouves


def annoncer(ecarts: list[str], attendues: Mapping[str, str], flux: TextIO) -> bool:
    """Écrit les écarts sur `flux` — ou, sans écart, les attentes déclarées ; vrai si
    aucun écart. `etat_apimanga.sh` s'arrête (code 1) sur un faux : ni contrôle
    conforme, ni référence relevée sur une base qui diffère des fichiers."""
    for ecart in ecarts:
        print(f"migrations : {ecart}", file=flux)
    if ecarts:
        return False
    if attendues:
        print(
            f"migrations attendues en attente : {', '.join(sorted(attendues))}",
            file=flux,
        )
    return True
