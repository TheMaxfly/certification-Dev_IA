"""MLflow à partir d'une charge JSON : un run, dont l'identifiant sort sur la sortie
(sans `action`) ; des étiquettes sur des runs déjà faits (`etiquettes`) ; les versions
du registre de modèles (`registre`).

Exécuté par l'interpréteur du module 10, qui a MLflow (le module 11 ne l'installe
pas : l'API n'installe que ce qu'elle exécute) :

    uv run --project ../10_mesures_recherche --locked \
        python -I journal_mlflow.py <charge.json>

Autonome : la bibliothèque standard et MLflow, rien du paquet de l'API. La charge
ne porte que des nombres, des noms, les invites et les réglages versionnés, et des
pièces sans texte de passage ni de réponse (le banc l'a construite ainsi).
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


def etiqueter(client: Any, charge: dict[str, Any]) -> str:
    """Les étiquettes des runs de la charge ; celles qui existent sont remplacées."""
    for run_id, etiquettes in charge["runs"].items():
        for cle, valeur in etiquettes.items():
            client.set_tag(run_id, cle, valeur)
    return f"{len(charge['runs'])} runs étiquetés"


def inscrire(client: Any, charge: dict[str, Any]) -> str:
    """Le modèle logique et une version par profil, liée à son run, à son stade. Rejouer
    ne double rien ; un profil requalifié par un autre run a une version de plus, et
    ses versions d'avant passent à `Archived`."""
    nom = charge["modele"]
    filtre = f"name = '{nom}'"
    if not client.search_registered_models(filter_string=filtre):
        client.create_registered_model(nom, description=charge["description"])
    existantes = list(client.search_model_versions(filtre))
    lignes: list[str] = []
    for voulue in charge["versions"]:
        du_profil = [v for v in existantes if v.tags.get("profil") == voulue["profil"]]
        version = next((v for v in du_profil if v.run_id == voulue["run_id"]), None)
        if version is None:
            source = client.get_run(voulue["run_id"]).info.artifact_uri
            version = client.create_model_version(
                nom,
                source,
                run_id=voulue["run_id"],
                tags={"profil": voulue["profil"], "role": voulue["role"]},
            )
        for ancienne in du_profil:
            if ancienne.version != version.version:
                client.transition_model_version_stage(nom, ancienne.version, "Archived")
        client.transition_model_version_stage(nom, version.version, voulue["stade"])
        lignes.append(f"{voulue['profil']}={version.version}:{voulue['stade']}")
    return " ".join(lignes)


def client_de(mlflow: Any, charge: dict[str, Any]) -> Any:
    return mlflow.MlflowClient(
        tracking_uri=charge["suivi"], registry_uri=charge["suivi"]
    )


def principal(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage : journal_mlflow.py <charge.json>", file=sys.stderr)
        return 2
    charge = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    mlflow = importlib.import_module("mlflow")
    action = charge.get("action")
    if action == "etiquettes":
        print(etiqueter(client_de(mlflow, charge), charge))
    elif action == "registre":
        print(inscrire(client_de(mlflow, charge), charge))
    else:
        print(journaliser(mlflow, charge))
    return 0


if __name__ == "__main__":
    sys.exit(principal(sys.argv))
