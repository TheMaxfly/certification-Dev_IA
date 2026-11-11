"""Les commandes du poste, le bouclage, l'horloge : tout ce que `livrer` emploie passe
par un `Outils`, que les tests remplacent par des doubles.

LES COMMANDES sont lancées sans shell, dans un environnement vidé (comme
`verification/verifier.sh`) : ni `VIRTUAL_ENV`, ni DSN, ni clé héritée du terminal. Ce
point compte pour Compose : une variable de l'environnement du processus l'emporte sur
les fichiers `--env-file` ; seules celles que `livrer` pose explicitement y entrent.

LE BOUCLAGE SEUL. Les requêtes HTTP ne visent que `127.0.0.1` ; toute autre adresse est
refusée avant l'envoi.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess  # nosec B404 — commandes fixes, sans shell
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx

from livraison.codes import Arret, Code

RACINE_MODULE = Path(__file__).resolve().parents[2]
RACINE_DEPOT = RACINE_MODULE.parent
BOUCLAGE = "http://127.0.0.1:"


@dataclass(frozen=True)
class Commande:
    """Une commande : son nom stable, ses arguments, son dossier, ce qu'elle reçoit de
    plus dans son environnement, et son entrée."""

    nom: str
    argv: tuple[str, ...]
    dossier: Path = RACINE_DEPOT
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    entree: str | None = None


@dataclass(frozen=True)
class Sortie:
    code: int
    stdout: str = ""
    stderr: str = ""


Lanceur = Callable[[Commande], Sortie]
Http = Callable[[str], tuple[int, str]]
Afficher = Callable[[str], None]


def environnement_propre(ajouts: Mapping[str, str]) -> dict[str, str]:
    """Le strict nécessaire du terminal, plus les ajouts de la commande."""
    base = {
        cle: os.environ[cle]
        for cle in ("HOME", "USER", "PATH", "DOCKER_HOST", "XDG_RUNTIME_DIR")
        if cle in os.environ
    }
    return base | {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"} | dict(ajouts)


def lancer_reel(commande: Commande) -> Sortie:  # pragma: no cover — le vrai poste
    r = subprocess.run(  # nosec B603 — arguments fixés par livrer, sans shell
        list(commande.argv),
        cwd=commande.dossier,
        env=environnement_propre(commande.env),
        input=commande.entree,
        capture_output=True,
        text=True,
        check=False,
    )
    return Sortie(r.returncode, r.stdout, r.stderr)


def exiger_bouclage(url: str) -> None:
    if not url.startswith(BOUCLAGE):
        raise Arret(Code.INTERNE, "requête hors du bouclage refusée")


def http_reel(url: str) -> tuple[int, str]:  # pragma: no cover — le vrai poste
    """GET sur le bouclage : (statut, corps) ; (0, raison) si rien ne répond."""
    exiger_bouclage(url)
    try:
        r = httpx.get(url, timeout=5.0)
    except httpx.HTTPError as exc:
        return 0, type(exc).__name__
    return r.status_code, r.text


@dataclass
class Outils:
    """Ce que `livrer` emploie : les commandes, le bouclage, l'horloge, l'écran, le
    dépôt, l'environnement du terminal (lu pour une seule variable : le chemin du
    fichier de mots de passe de la préproduction, jamais ouvert)."""

    lanceur: Lanceur
    http: Http
    afficher: Afficher = print
    horloge: Callable[[], float] = time.monotonic
    dormir: Callable[[float], None] = time.sleep
    maintenant: Callable[[], datetime] = lambda: datetime.now(UTC)
    racine: Path = RACINE_DEPOT
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    utilisateur: Callable[[], str] = getpass.getuser
    journal: Path = RACINE_MODULE / "journal"
    constructions: Path = RACINE_MODULE / ".construction"


def lignes(sortie: Sortie) -> list[str]:
    return [ligne.strip() for ligne in sortie.stdout.splitlines() if ligne.strip()]


def commande_affichee(commande: Commande, racine: Path = RACINE_DEPOT) -> str:
    """La commande telle qu'on la lit : variables posées, arguments ; tout chemin du
    dépôt relatif à sa racine, aucun chemin personnel."""
    texte = " ".join(
        [
            f"{cle}=<chemin>" if "PGPASS" in cle else f"{cle}={v}"
            for cle, v in commande.env.items()
        ]
        + list(commande.argv)
    )
    return texte.replace(f"{racine}/", "").replace(str(racine), ".")


def objet(valeur: object) -> dict[str, object]:
    """Un objet JSON lu (dictionnaire) ; `{}` pour toute autre forme."""
    if isinstance(valeur, dict):
        return {str(k): v for k, v in cast(dict[object, object], valeur).items()}
    return {}


def liste(valeur: object) -> list[object]:
    return list(cast(list[object], valeur)) if isinstance(valeur, list) else []


def json_objet(texte: str) -> dict[str, object]:
    try:
        return objet(json.loads(texte)) if texte else {}
    except json.JSONDecodeError:
        return {}
