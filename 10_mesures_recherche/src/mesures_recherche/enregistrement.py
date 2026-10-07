"""Enregistrer une mesure : un run MLflow, et les mêmes métriques dans
`bench.eval_mesures`.

Le run porte :
  - étiquettes : le numéro de la mesure, son tour, la spec qui l'a déclarée ;
  - paramètres : tous les réglages de la mesure, la version et l'empreinte du jeu,
    l'empreinte de la configuration, le commit (et si l'arbre était propre),
    l'empreinte du code du module, l'identifiant d'encodage et la version du
    service quand la mesure passe par lui ;
  - métriques : `<métrique>_<k>/<portée>/<périmètre>` ;
  - pièces : le tableau par question (rang de la première série attendue, les dix
    entités et leurs scores), le fichier de configuration, le résultat complet.

`bench.eval_mesures` reçoit les mêmes valeurs, `run_id` = identifiant du run
MLflow : la table est immuable, un rejeu enregistré serait un nouveau run.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
import uuid
from pathlib import Path

from mesures_recherche.configuration import FICHIER, RACINE_DEPOT, RACINE_MODULE

STOCKAGE = RACINE_MODULE / "mlflow"


def uri_suivi(stockage: Path = STOCKAGE) -> str:
    return f"sqlite:///{stockage / 'mlflow.db'}"


def nom_metrique(metrique: str, k: int, portee: str, perimetre: str) -> str:
    return f"{metrique}_{k}/{portee}/{perimetre}"


def empreinte_code() -> str:
    """sha256 des sources du paquet, triées : ce qui a réellement tourné."""
    h = hashlib.sha256()
    for f in sorted((RACINE_MODULE / "src").rglob("*.py")):
        h.update(f.relative_to(RACINE_MODULE).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def etat_git() -> dict[str, str]:
    def git(*args):
        return subprocess.run(  # nosec B603 B607 — commande fixe, sans shell
            ["git", *args],
            cwd=RACINE_DEPOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    propre = git("status", "--porcelain", "--", str(RACINE_MODULE)) == ""
    return {"commit": git("rev-parse", "HEAD"), "arbre_module_propre": str(propre)}


def parametres(resultat: dict) -> dict[str, str]:
    p = {
        f"reglage.{k}": json.dumps(v) if isinstance(v, list) else str(v)
        for k, v in resultat["reglages"].items()
    }
    p |= {
        "jeu_version": resultat["jeu_version"],
        "jeu_empreinte": resultat["jeu_empreinte"],
        "configuration_empreinte": resultat["configuration_empreinte"],
        "code_empreinte": empreinte_code(),
    }
    p |= {k: str(v) for k, v in resultat["parametres"].items()}
    return p | etat_git()


def tableau_par_question(resultat: dict) -> str:
    sortie = io.StringIO()
    w = csv.writer(sortie, lineterminator="\n")
    entetes = [
        "question_id",
        "famille",
        "mode",
        "issue",
        "de_rang",
        "atteignable",
        "rang_premiere_attendue",
        "egalite_au_seuil",
    ]
    metriques = ["hit_rate@5", "hit_rate@10", "mrr@10", "ndcg@10"]
    w.writerow(
        entetes
        + metriques
        + [f"entite_{i}" for i in range(1, 11)]
        + [f"score_{i}" for i in range(1, 11)]
    )
    for q in resultat["questions"]:
        m = q["metriques"] or {}
        top = q["top"] + [("", "")] * (10 - len(q["top"]))
        w.writerow(
            [q[c] for c in entetes]
            + [m.get(x, "") for x in metriques]
            + [c for c, _ in top]
            + [s for _, s in top]
        )
    return sortie.getvalue()


def enregistrer(resultat: dict, dsn_ecriture: str, stockage: Path = STOCKAGE) -> str:
    """Un run MLflow terminé, puis les lignes de `bench.eval_mesures` ; rend le
    run_id. Les deux portent les mêmes valeurs."""
    import mlflow
    import psycopg

    stockage.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(uri_suivi(stockage))
    nom = resultat["experience"]
    if mlflow.get_experiment_by_name(nom) is None:
        mlflow.create_experiment(nom, artifact_location=(stockage / "pieces").as_uri())
    mlflow.set_experiment(nom)

    with mlflow.start_run(run_name=f"{resultat['mesure']}-{resultat['nom']}") as run:
        mlflow.set_tags(
            {
                "mesure": str(resultat["mesure"]),
                "tour": str(resultat["tour"]),
                "spec": resultat["spec"],
            }
        )
        mlflow.log_params(parametres(resultat))
        mlflow.log_metrics(
            {
                nom_metrique(a["metrique"], a["k"], a["portee"], a["perimetre"]): a[
                    "valeur"
                ]
                for a in resultat["agregats"]
            }
        )
        mlflow.log_metric("duree_questions_s", resultat["duree_questions_s"])
        mlflow.log_text(tableau_par_question(resultat), "par_question.csv")
        mlflow.log_artifact(str(FICHIER))
        mlflow.log_text(
            json.dumps(resultat, ensure_ascii=False, indent=1, default=str),
            "resultat.json",
        )
        run_id = run.info.run_id

    with psycopg.connect(dsn_ecriture) as cx:
        with cx.cursor() as cur:
            cur.executemany(
                "INSERT INTO bench.eval_mesures (run_id, jeu_version, portee,"
                " perimetre, metrique, k, valeur, n_questions)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        uuid.UUID(run_id),
                        resultat["jeu_version"],
                        a["portee"],
                        a["perimetre"],
                        a["metrique"],
                        a["k"],
                        a["valeur"],
                        a["n_questions"],
                    )
                    for a in resultat["agregats"]
                ],
            )
    return run_id
