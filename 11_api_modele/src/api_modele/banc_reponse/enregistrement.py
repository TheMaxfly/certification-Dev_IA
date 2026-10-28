"""Enregistrer un run : la garde de commit, le dossier hors dépôt, le run MLflow, la
ligne `bench.eval_runs`.

- On commite d'abord, on mesure ensuite (règle du module 10, réutilisée) : refus si
  l'arbre n'est pas propre, si la branche n'a pas d'amont ou si `HEAD` n'y est pas.
- Les textes des réponses vont dans un fichier hors dépôt, hors MLflow
  (`05_…/data/rapports/mesures_reponse/<run>/`), pour la lecture humaine ; ils ont
  passé le contrôle des noms de membres avant d'y être écrits.
- Le run MLflow (expérience `E3-reponse`, magasin du module 10) est écrit par
  l'interpréteur du module 10 (`journal_mlflow.py`), avec un environnement réduit
  (ni clé, ni DSN) ; ses pièces ne portent aucun texte de passage ni de réponse.
- La ligne `bench.eval_runs` (corpus, encodage, run) est écrite sous le DSN du
  module 10 (`DATABASE_URL`, rôle `postgres`, mot de passe dans `~/.pgpass`).
"""

from __future__ import annotations

import json
import os
import re

# git et uv : commandes fixes, en liste, sans shell.
import subprocess  # nosec B404
import uuid
from collections.abc import Callable
from pathlib import Path

import psycopg

from api_modele.configuration import RACINE_MODULE

RACINE_DEPOT = RACINE_MODULE.parent
SORTIES = (
    RACINE_DEPOT
    / "05_nettoyage_agregation_bdd"
    / "data"
    / "rapports"
    / "mesures_reponse"
)
MODULE_10 = RACINE_DEPOT / "10_mesures_recherche"
STOCKAGE = MODULE_10 / "mlflow"
EXPERIENCE = "E3-reponse"
SCRIPT = Path(__file__).with_name("journal_mlflow.py")
#: Ce que l'interpréteur du module 10 reçoit de l'environnement : de quoi trouver uv
#: et son cache, rien d'autre (ni clé de fournisseur, ni DSN).
ENVIRONNEMENT_TRANSMIS = ("PATH", "HOME", "LANG", "UV_CACHE_DIR")
RUN_ID = re.compile(r"^[0-9a-f]{32}$")

type Executeur = Callable[[list[str]], str]


class CommitRefuse(Exception):
    """L'arbre n'est pas propre, ou `HEAD` n'est pas poussé : rien n'est mesuré."""


def git(racine: Path, *args: str) -> str:
    # git, trouvé dans le PATH du poste ; arguments fixés par ce module.
    return subprocess.run(  # nosec B603 B607
        ["git", *args], cwd=racine, capture_output=True, text=True, check=True
    ).stdout.strip()


def etat_git(racine: Path = RACINE_DEPOT) -> dict[str, str]:
    """Le commit et la branche, et si l'arbre est propre (pour un essai)."""
    return {
        "commit": git(racine, "rev-parse", "HEAD"),
        "branche": git(racine, "rev-parse", "--abbrev-ref", "HEAD"),
        "arbre_propre": str(git(racine, "status", "--porcelain") == ""),
    }


def exiger_commit(racine: Path = RACINE_DEPOT) -> dict[str, str]:
    """L'état git, si l'arbre est propre et `HEAD` poussé ; sinon `CommitRefuse`."""
    etat = etat_git(racine)
    if etat["arbre_propre"] != "True":
        raise CommitRefuse(
            "arbre non propre : commiter d'abord, mesurer ensuite — aucun appel"
        )
    try:
        git(racine, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    except subprocess.CalledProcessError:
        raise CommitRefuse(
            "branche sans amont : pousser d'abord — aucun appel"
        ) from None
    if git(racine, "rev-list", "--count", "@{u}..HEAD") != "0":
        raise CommitRefuse("HEAD n'est pas poussé : pousser d'abord — aucun appel")
    return etat


def dossier_du_run(nom: str, sorties: Path | None = None) -> Path:
    """Le dossier hors dépôt d'un run, neuf : un run ne réécrit jamais un autre."""
    dossier = (sorties or SORTIES) / nom
    dossier.mkdir(parents=True, exist_ok=False)
    return dossier


def ecrire_json(chemin: Path, donnees: object) -> Path:
    chemin.write_text(
        json.dumps(donnees, ensure_ascii=False, indent=1, default=str) + "\n",
        encoding="utf-8",
    )
    return chemin


def ecrire_textes(dossier: Path, titre: str, textes: dict[str, str]) -> Path:
    """Les textes contrôlés, un par identifiant, pour la lecture humaine."""
    lignes = [f"# {titre}", ""]
    for cle, texte in textes.items():
        lignes += [f"## {cle}", "", texte, ""]
    chemin = dossier / "reponses.md"
    chemin.write_text("\n".join(lignes), encoding="utf-8")
    return chemin


def executer(commande: list[str]) -> str:
    """La commande, dans le module 10, avec l'environnement réduit ; sa sortie."""
    environnement = {k: v for k, v in os.environ.items() if k in ENVIRONNEMENT_TRANSMIS}
    # uv, trouvé dans le PATH ; la commande est construite par `journaliser_mlflow`.
    return subprocess.run(  # nosec B603
        commande,
        cwd=MODULE_10,
        env=environnement,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def journaliser_mlflow(
    charge: dict[str, object], dossier: Path, executeur: Executeur | None = None
) -> str:
    """Le run MLflow de la charge, par l'interpréteur du module 10 ; son identifiant."""
    fichier = ecrire_json(dossier / "mlflow_charge.json", charge)
    sortie = (executeur or executer)(
        [
            "uv",
            "run",
            "--project",
            str(MODULE_10),
            "--locked",
            "python",
            "-I",
            str(SCRIPT),
            str(fichier),
        ]
    )
    lignes = sortie.strip().splitlines()
    run_id = lignes[-1].strip() if lignes else ""
    if not RUN_ID.fullmatch(run_id):
        raise RuntimeError(f"identifiant de run MLflow illisible : {run_id[:40]!r}")
    return run_id


def charge_mlflow(
    nom: str,
    etiquettes: dict[str, str],
    parametres: dict[str, object],
    metriques: dict[str, float],
    pieces: dict[str, object],
    stockage: Path | None = None,
) -> dict[str, object]:
    """La charge d'un run : paramètres en texte, pièces en JSON."""
    stockage = stockage or STOCKAGE
    return {
        "suivi": f"sqlite:///{stockage / 'mlflow.db'}",
        "pieces": (stockage / "pieces").as_uri(),
        "experience": EXPERIENCE,
        "nom": nom,
        "etiquettes": etiquettes,
        "parametres": {
            k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            for k, v in parametres.items()
        },
        "metriques": metriques,
        "pieces_texte": {
            chemin: json.dumps(contenu, ensure_ascii=False, indent=1, default=str)
            for chemin, contenu in pieces.items()
        },
    }


def inscrire_eval_run(
    dsn: str, run_id: str, corpus_id: str, encodage_id: int, note: str
) -> None:
    """La ligne du run dans `bench.eval_runs` (ajout seul)."""
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.eval_runs (run_id, corpus_id, encodage_id, note)"
            " VALUES (%s, %s, %s, %s)",
            (uuid.UUID(run_id), corpus_id, encodage_id, note),
        )
