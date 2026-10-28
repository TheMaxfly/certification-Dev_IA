"""Un run MLflow à partir d'une charge JSON ; rend son identifiant sur la sortie.

Exécuté par l'interpréteur du module 10, qui a MLflow (le module 11 ne l'installe
pas : l'API n'installe que ce qu'elle exécute) :

    uv run --project ../10_mesures_recherche --locked \
        python -I journal_mlflow.py <charge.json>

Autonome : la bibliothèque standard et MLflow, rien du paquet de l'API. La charge
ne porte que des nombres, des noms et des pièces sans texte de passage ni de
réponse (le banc l'a construite ainsi).
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any


def journaliser(mlflow: Any, charge: dict[str, Any]) -> str:
    """Le run, dans l'expérience de la charge (créée au besoin, pièces au dossier
    du magasin) ; terminé, ou tué s'il a été interrompu."""
    mlflow.set_tracking_uri(charge["suivi"])
    nom = charge["experience"]
    if mlflow.get_experiment_by_name(nom) is None:
        mlflow.create_experiment(nom, artifact_location=charge["pieces"])
    mlflow.set_experiment(nom)
    run = mlflow.start_run(run_name=charge["nom"])
    try:
        mlflow.set_tags(charge["etiquettes"])
        mlflow.log_params(charge["parametres"])
        mlflow.log_metrics(charge["metriques"])
        for chemin, contenu in charge["pieces_texte"].items():
            mlflow.log_text(contenu, chemin)
    except BaseException:
        mlflow.end_run("FAILED")
        raise
    interrompu = charge["etiquettes"].get("statut") == "interrompu"
    mlflow.end_run("KILLED" if interrompu else "FINISHED")
    return str(run.info.run_id)


def principal(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage : journal_mlflow.py <charge.json>", file=sys.stderr)
        return 2
    charge = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    print(journaliser(importlib.import_module("mlflow"), charge))
    return 0


if __name__ == "__main__":
    sys.exit(principal(sys.argv))
