#!/usr/bin/env python3
"""
run_notebook_job.py

But :
- Lancer un notebook Databricks via Jobs API (submit one-shot)
- Passer des paramètres (widgets) au notebook
- Attendre la fin
- Récupérer le chemin renvoyé par `dbutils.notebook.exit(...)`
- Télécharger le fichier (ex: CSV) en local

Prérequis (variables d'environnement ou .env):
- DATABRICKS_HOST
- DATABRICKS_TOKEN
Optionnel (selon workspace):
- DATABRICKS_ENABLE_EXPERIMENTAL_FILES_API_CLIENT=true
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path
from typing import Optional

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError
from databricks.sdk.service import jobs
from dotenv import load_dotenv


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Lance un notebook Databricks via Jobs API (submit), puis télécharge le fichier "
            "dont le chemin est renvoyé par dbutils.notebook.exit()."
        )
    )
    p.add_argument(
        "--notebook-path",
        required=True,
        help=(
            "Chemin du notebook dans le workspace Databricks. "
            'Exemples: "/Users/<email>/MonNotebook" (souvent attendu par l’API) '
            'ou "/Workspace/Users/<email>/MonNotebook" (souvent affiché par l’UI).'
        ),
    )
    p.add_argument(
        "--existing-cluster-id",
        required=True,
        help="Cluster ID existant à utiliser pour exécuter le notebook (Compute).",
    )
    p.add_argument(
        "--run-id",
        default="",
        help='Valeur du widget "run_id". Si vide, laisse le notebook générer son propre run_id.',
    )
    p.add_argument(
        "--export-base",
        default="/Volumes/workspace/default/exports",
        help='Valeur du widget "export_base" (ex: /Volumes/workspace/default/exports).',
    )
    p.add_argument(
        "--download-to",
        default="albums_manga_clean.csv",
        help="Chemin local où enregistrer le fichier téléchargé.",
    )
    p.add_argument(
        "--download-chunk-bytes",
        type=int,
        default=1024 * 1024,
        help="Taille des chunks de téléchargement (octets). Défaut: 1 MiB.",
    )
    p.add_argument(
        "--write-manifest",
        default="",
        help=(
            "Optionnel: écrire un manifest JSON (preuve de traçabilité) avec "
            "run_id Databricks, chemins, timestamps, etc. Exemple: exports/manifest.json"
        ),
    )
    return p.parse_args()


def _require_env(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(
            f"Variable d'environnement manquante: {name}. "
            "Renseigne-la dans ton shell ou dans un fichier .env (DATABRICKS_HOST, DATABRICKS_TOKEN)."
        )
    return val


def resolve_notebook_path(w: WorkspaceClient, nb_path: str) -> str:
    """
    L'UI Databricks montre souvent des chemins /Workspace/Users/...
    Beaucoup d'appels API (Jobs) attendent plutôt /Users/...

    Essaie:
    1) le chemin fourni
    2) si /Workspace/... -> sans /Workspace
    3) si /Users/... -> avec /Workspace
    """
    candidates = [nb_path]

    if nb_path.startswith("/Workspace/"):
        candidates.append(nb_path.replace("/Workspace", "", 1))
    elif nb_path.startswith("/Users/"):
        candidates.append("/Workspace" + nb_path)

    last_err: Optional[Exception] = None
    for c in candidates:
        try:
            w.workspace.get_status(c)
            return c
        except Exception as e:
            last_err = e

    raise RuntimeError(
        "Notebook introuvable via l’API.\n"
        f"Chemins testés: {candidates}\n"
        f"Dernière erreur: {last_err}\n\n"
        "Astuce: dans Databricks, ouvre le notebook → menu ⋮ → 'Copy path' puis réessaie."
    )


def download_file(w: WorkspaceClient, remote_path: str, local_path: Path, chunk_bytes: int) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with w.files.download(remote_path) as src, local_path.open("wb") as dst:
            while True:
                chunk = src.read(chunk_bytes)
                if not chunk:
                    break
                dst.write(chunk)
    except DatabricksError as e:
        raise RuntimeError(
            "Échec du téléchargement via Files API.\n"
            f"Chemin distant: {remote_path}\n"
            f"Erreur: {e}\n\n"
            "Selon ton workspace, il peut falloir activer l’API fichiers expérimentale:\n"
            "  export DATABRICKS_ENABLE_EXPERIMENTAL_FILES_API_CLIENT=true\n"
        ) from e


def write_manifest(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def wait_for_run_terminated(w: WorkspaceClient, run_id: int, timeout_seconds: int = 2 * 60 * 60) -> None:
    wait_fn = getattr(w.jobs, "wait_get_run_job_terminated_or_skipped", None)
    if callable(wait_fn):
        wait_fn(run_id=run_id, timeout=timedelta(seconds=timeout_seconds))
        return

    deadline = time.monotonic() + timeout_seconds
    while True:
        run = w.jobs.get_run(run_id=run_id)
        state = getattr(run, "state", None)
        life_cycle = getattr(state, "life_cycle_state", None)
        if life_cycle in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}:
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"Timeout en attendant la fin du run {run_id}.")
        time.sleep(5)


def main() -> None:
    args = parse_args()

    load_dotenv()  # charge .env si présent

    _require_env("DATABRICKS_HOST")
    _require_env("DATABRICKS_TOKEN")

    w = WorkspaceClient()

    notebook_path = resolve_notebook_path(w, args.notebook_path)

    # Widgets passés au notebook (s'il ne les lit pas, il ignore)
    base_params = {
        "run_id": args.run_id,
        "export_base": args.export_base,
    }

    task = jobs.SubmitTask(
        task_key="export_manga",
        existing_cluster_id=args.existing_cluster_id,
        notebook_task=jobs.NotebookTask(
            notebook_path=notebook_path,
            base_parameters=base_params,
        ),
    )

    run = w.jobs.submit(run_name="export_manga_notebook", tasks=[task])
    dbx_run_id = run.run_id
    if dbx_run_id is None:
        raise RuntimeError("Impossible de récupérer run_id depuis jobs.submit().")

    wait_for_run_terminated(w, dbx_run_id)
    out = w.jobs.get_run_output(run_id=dbx_run_id)
    final_remote_path = (out.notebook_output.result or "").strip()
    if not final_remote_path:
        raise RuntimeError(
            "Le notebook n'a pas renvoyé de chemin via dbutils.notebook.exit().\n"
            "Assure-toi qu'à la fin du notebook tu as: dbutils.notebook.exit(final_csv)"
        )

    local_path = Path(args.download_to)
    download_file(w, final_remote_path, local_path, args.download_chunk_bytes)

    print("REMOTE:", final_remote_path)
    print("LOCAL :", str(local_path))

    if args.write_manifest:
        manifest = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "databricks": {
                "host": os.getenv("DATABRICKS_HOST"),
                "run_id": dbx_run_id,
                "notebook_path": notebook_path,
                "existing_cluster_id": args.existing_cluster_id,
                "base_parameters": base_params,
                "remote_file": final_remote_path,
            },
            "local": {"download_to": str(local_path)},
        }
        write_manifest(Path(args.write_manifest), manifest)
        print("MANIFEST:", args.write_manifest)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrompu.", file=sys.stderr)
        sys.exit(130)
