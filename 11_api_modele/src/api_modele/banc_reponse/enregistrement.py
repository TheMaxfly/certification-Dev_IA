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
- Chaque run joint en artefacts les deux invites et `reglages.toml`, leurs empreintes en
  paramètres, et porte ses étiquettes (`etape`, `role`, `invite_version`, `statut`,
  `verdict`, `cause_interruption`). Le registre de modèles rend la promotion visible
  (une version par profil, liée au run qui l'a qualifiée) ; **il ne contrôle rien** :
  le profil actif reste celui de `reglages.toml`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

# git et uv : commandes fixes, en liste, sans shell.
import subprocess  # nosec B404
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

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
#: L'étiquette `etape` d'un run, par défaut (dette 41 : elle était écrite en dur) :
#: « E3-06 » = E3, étape 3, spec 06, la mesure de la réponse rédigée. Un run en donne
#: une autre par `--etape`, gardée dans ses paramètres ; les runs anciens, sans ce
#: paramètre, gardent ce défaut.
ETAPE = "E3-06"
FORME_ETAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
CONFIGURATION = RACINE_MODULE / "config"
INVITES_DU_DEPOT = "11_api_modele/config/invites"
#: Ce que chaque run joint, et le paramètre qui porte son empreinte.
FICHIERS_DU_RUN = {
    "config/invites/reponse.md": "invite_reponse_sha",
    "config/invites/reformulation.md": "invite_reformulation_sha",
    "config/reglages.toml": "reglages_sha",
}
MODELE_DU_REGISTRE = "conseiller-manga-reponse"
#: Le stade de chaque profil : il dit ce qui a été promu, rien n'y lit le profil actif.
STADES = {
    "mistral-large-2512": "Production",
    "mistral-large-4": "Staging",
    "gpt-6-luna": "Archived",
    "gpt-6-sol": "Archived",
}

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


def lire_textes(dossier: Path) -> dict[str, str]:
    """Les textes d'un run, par identifiant (l'inverse d'`ecrire_textes`)."""
    blocs = re.split(
        r"^## (.+)$",
        (dossier / "reponses.md").read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )[1:]
    return {
        cle.strip(): texte.strip()
        for cle, texte in zip(blocs[0::2], blocs[1::2], strict=True)
    }


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


def par_le_module_10(fichier: Path, executeur: Executeur | None = None) -> str:
    """Le script MLflow sur une charge, par l'interpréteur du module 10 ; sa sortie."""
    return (executeur or executer)(
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


def journaliser_mlflow(
    charge: dict[str, object], dossier: Path, executeur: Executeur | None = None
) -> str:
    """Le run MLflow de la charge, par l'interpréteur du module 10 ; son identifiant."""
    fichier = ecrire_json(dossier / "mlflow_charge.json", charge)
    sortie = par_le_module_10(fichier, executeur)
    lignes = sortie.strip().splitlines()
    run_id = lignes[-1].strip() if lignes else ""
    if not RUN_ID.fullmatch(run_id):
        raise RuntimeError(f"identifiant de run MLflow illisible : {run_id[:40]!r}")
    return run_id


def fichiers_du_run(racine: Path = RACINE_MODULE) -> dict[str, str]:
    """Les invites et les réglages, tels qu'au dépôt, à joindre en artefacts."""
    return {
        chemin: (racine / chemin).read_text(encoding="utf-8")
        for chemin in FICHIERS_DU_RUN
    }


def empreintes_du_run(fichiers: Mapping[str, str]) -> dict[str, str]:
    """L'empreinte SHA-256 de chaque fichier joint, sous le nom de son paramètre."""
    return {
        parametre: hashlib.sha256(fichiers[chemin].encode("utf-8")).hexdigest()
        for chemin, parametre in FICHIERS_DU_RUN.items()
    }


def invite_version(commit: str, racine: Path = RACINE_DEPOT) -> str:
    """Le commit court qui a écrit les invites de `commit` ; `inconnue` si git ne le
    dit pas."""
    try:
        version = git(
            racine, "log", "-1", "--format=%h", commit, "--", INVITES_DU_DEPOT
        )
    except subprocess.CalledProcessError:
        return "inconnue"
    return version or "inconnue"


def etiquettes_du_run(
    profil: str,
    profil_actif: str,
    version_des_invites: str,
    statut: str,
    motif_interruption: str | None,
    verdict: Mapping[str, object] | None,
    etape: str = ETAPE,
) -> dict[str, str]:
    """Les étiquettes d'un run : `verdict` s'il a un critère (pas les scénarios),
    `cause_interruption` s'il a été interrompu ; `etape`, celle du run."""
    etiquettes = {
        "etape": etape,
        "role": "profil_actif" if profil == profil_actif else "repli",
        "invite_version": version_des_invites,
        "statut": "interrompu" if statut == "interrompu" else "complet",
    }
    if verdict is not None:
        etiquettes["verdict"] = "tenu" if verdict.get("tenu") is True else "non_tenu"
    if statut == "interrompu":
        motif = motif_interruption or "inconnue"
        etiquettes["cause_interruption"] = (
            "plafond" if motif.startswith("coût") else motif
        )
    return etiquettes


def etiquettes_des_runs_faits(
    profil_actif: str, sorties: Path | None = None
) -> dict[str, dict[str, str]]:
    """Les étiquettes a posteriori des runs faits : ce que leurs dossiers disent."""
    runs: dict[str, dict[str, str]] = {}
    for fichier in sorted((sorties or SORTIES).glob("*/run_id.json")):
        run_id = str(json.loads(fichier.read_text(encoding="utf-8"))["run_id"])
        resume = cast(
            dict[str, object],
            json.loads(fichier.with_name("resume.json").read_text(encoding="utf-8")),
        )
        parametres = cast(dict[str, str], resume["parametres"])
        # Un run relu : son verdict est celui de la relecture.
        relecture = fichier.with_name("relecture.json")
        verdict = (
            cast(dict[str, object], json.loads(relecture.read_text("utf-8")))["verdict"]
            if relecture.exists()
            else resume.get("verdict")
        )
        runs[run_id] = etiquettes_du_run(
            parametres["profil"],
            profil_actif,
            invite_version(parametres["commit"]),
            str(resume["statut"]),
            cast(str | None, resume.get("motif_interruption")),
            cast(Mapping[str, object] | None, verdict),
            parametres.get("etape", ETAPE),
        )
    return runs


def charge_des_etiquettes(
    runs: Mapping[str, Mapping[str, str]], stockage: Path | None = None
) -> dict[str, object]:
    return {
        "action": "etiquettes",
        "suivi": f"sqlite:///{(stockage or STOCKAGE) / 'mlflow.db'}",
        "runs": runs,
    }


def charge_de_relecture(
    run_id: str,
    etiquettes: Mapping[str, str],
    metriques: Mapping[str, float],
    relecture: object,
    stockage: Path | None = None,
) -> dict[str, object]:
    """Ce qu'une relecture ajoute au run : des étiquettes, des métriques `_relu`, sa
    pièce ; ses métriques d'origine restent."""
    return {
        "action": "relecture",
        "suivi": f"sqlite:///{(stockage or STOCKAGE) / 'mlflow.db'}",
        "run_id": run_id,
        "etiquettes": etiquettes,
        "metriques": metriques,
        "pieces_texte": {
            "relecture.json": json.dumps(
                relecture, ensure_ascii=False, indent=1, default=str
            )
        },
    }


def charge_du_registre(
    qualifiants: Mapping[str, str],
    profil_actif: str,
    stockage: Path | None = None,
) -> dict[str, object]:
    """Une version par profil de `STADES`, chacune avec le run qui l'a qualifiée."""
    return {
        "action": "registre",
        "suivi": f"sqlite:///{(stockage or STOCKAGE) / 'mlflow.db'}",
        "modele": MODELE_DU_REGISTRE,
        "description": (
            "Le modèle de langage de la route de réponse, par profil. Le registre rend"
            " la promotion visible ; il ne contrôle rien : le profil actif est celui"
            " de reglages.toml."
        ),
        "versions": [
            {
                "profil": profil,
                "run_id": qualifiants[profil],
                "stade": stade,
                "role": "profil_actif" if profil == profil_actif else "repli",
            }
            for profil, stade in STADES.items()
        ],
    }


def charge_mlflow(
    nom: str,
    etiquettes: dict[str, str],
    parametres: dict[str, object],
    metriques: dict[str, float],
    pieces: dict[str, object],
    stockage: Path | None = None,
    fichiers: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """La charge d'un run : paramètres en texte, pièces en JSON, fichiers tels quels."""
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
            **{
                chemin: json.dumps(contenu, ensure_ascii=False, indent=1, default=str)
                for chemin, contenu in pieces.items()
            },
            **(fichiers or {}),
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
