#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import time
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError


DEFAULT_TASK_KEY = "Export_databricks_manga"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Déclenche un Job Databricks (run_now), attend la fin, récupère la valeur renvoyée par "
            "dbutils.notebook.exit(...), puis télécharge le fichier en local."
        )
    )
    p.add_argument("--dotenv", default="", help="Chemin vers un fichier .env (défaut: .env à côté du script).")
    p.add_argument("--job-id", default="", help="ID du Job Databricks (sinon via DATABRICKS_JOB_ID).")
    p.add_argument(
        "--task-key",
        default=DEFAULT_TASK_KEY,
        help=f"task_key de la task notebook (défaut: {DEFAULT_TASK_KEY}).",
    )
    p.add_argument("--run-id", default="", help='Valeur du widget "run_id" (si vide, ne pas passer le widget).')
    p.add_argument("--export-base", default="/Volumes/workspace/default/exports", help='Valeur du widget "export_base".')
    p.add_argument("--local-csv", default="exports/databricks/albums_manga_clean.csv", help="Chemin local du CSV.")
    p.add_argument("--timeout-seconds", type=int, default=2 * 60 * 60, help="Timeout attente run (défaut 2h).")
    p.add_argument("--poll-seconds", type=int, default=5, help="Intervalle polling (défaut 5s).")
    return p.parse_args()


def is_success(result_state) -> bool:
    # Compatible enum (RunResultState.SUCCESS) et string ("SUCCESS")
    if result_state is None:
        return False
    name = getattr(result_state, "name", None)
    if name:
        return name == "SUCCESS"
    s = str(result_state)
    return s == "SUCCESS" or s.endswith(".SUCCESS") or s.endswith("SUCCESS")


def wait_for_run_terminated(w: WorkspaceClient, run_id: int, timeout_seconds: int, poll_seconds: int) -> None:
    wait_fn = getattr(w.jobs, "wait_get_run_job_terminated_or_skipped", None)
    if callable(wait_fn):
        wait_fn(run_id=run_id, timeout=timedelta(seconds=timeout_seconds))
        run = w.jobs.get_run(run_id=run_id)
        state = getattr(run, "state", None)
        result_state = getattr(state, "result_state", None)
        msg = getattr(state, "state_message", "")
        if result_state is not None and not is_success(result_state):
            raise SystemExit(f"❌ Run Databricks échoué: result_state={result_state} msg={msg}")
        return

    deadline = time.monotonic() + timeout_seconds
    while True:
        run = w.jobs.get_run(run_id=run_id)
        state = getattr(run, "state", None)
        life_cycle = getattr(state, "life_cycle_state", None)
        result_state = getattr(state, "result_state", None)
        msg = getattr(state, "state_message", "")

        if life_cycle in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}:
            if result_state is not None and not is_success(result_state):
                raise SystemExit(
                    f"❌ Run Databricks échoué: life_cycle={life_cycle} result_state={result_state} msg={msg}"
                )
            return

        if time.monotonic() > deadline:
            raise SystemExit(f"❌ Timeout en attendant la fin du run {run_id}. life_cycle={life_cycle}")

        time.sleep(poll_seconds)


def resolve_output_run_id(w: WorkspaceClient, parent_run_id: int, task_key: str) -> int:
    # get_run_output() doit être appelé sur le run enfant de la task
    run = w.jobs.get_run(run_id=parent_run_id)
    tasks = getattr(run, "tasks", None) or []
    if not tasks:
        return parent_run_id

    if task_key:
        for t in tasks:
            if getattr(t, "task_key", None) == task_key:
                task_run_id = getattr(t, "run_id", None)
                if task_run_id is None:
                    break
                return int(task_run_id)
        available = [getattr(t, "task_key", None) for t in tasks]
        raise SystemExit(f"❌ task_key '{task_key}' introuvable. Tasks disponibles: {available}")

    if len(tasks) == 1:
        task_run_id = getattr(tasks[0], "run_id", None)
        return int(task_run_id) if task_run_id is not None else parent_run_id

    available = [getattr(t, "task_key", None) for t in tasks]
    raise SystemExit(
        "❌ Job multi-tasks: spécifie la task à lire avec --task-key.\n"
        f"Tasks disponibles: {available}"
    )


def download_file(w: WorkspaceClient, remote_path: str, local_path: Path) -> None:
    """
    Téléchargement robuste : on utilise l'API officielle download_to (pas de read/stream à gérer).
    """
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Méthode la plus compatible selon la doc SDK
        w.files.download_to(remote_path, str(local_path), overwrite=True)
    except DatabricksError as e:
        raise SystemExit(
            "❌ Échec du téléchargement via Files API.\n"
            f"Chemin distant: {remote_path}\n"
            f"Destination locale: {local_path}\n"
            f"Erreur: {e}\n\n"
            "Selon ton workspace, il peut falloir activer l’API fichiers expérimentale:\n"
            "  export DATABRICKS_ENABLE_EXPERIMENTAL_FILES_API_CLIENT=true\n"
        ) from e


def main() -> None:
    args = parse_args()

    dotenv_path = Path(args.dotenv).expanduser() if args.dotenv else (Path(__file__).resolve().parent / ".env")
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path)
    else:
        load_dotenv()

    host = os.getenv("DATABRICKS_HOST", "").strip().rstrip("/")
    token = os.getenv("DATABRICKS_TOKEN", "").strip()
    job_id = (args.job_id or os.getenv("DATABRICKS_JOB_ID", "")).strip()

    if not host or not token or not job_id:
        raise SystemExit(
            "❌ Configuration incomplète.\n"
            "Requis: DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_JOB_ID (ou --job-id).\n"
            "Astuce: complète ton .env."
        )

    job_id_int = int(job_id)

    notebook_params: dict[str, str] = {"export_base": args.export_base}
    if args.run_id:
        notebook_params["run_id"] = args.run_id

    w = WorkspaceClient(host=host, token=token)

    # 1) Déclenche le Job (run parent)
    run = w.jobs.run_now(job_id=job_id_int, notebook_params=notebook_params)
    parent_run_id = run.run_id
    if parent_run_id is None:
        raise SystemExit("❌ Impossible de récupérer run_id depuis jobs.run_now().")
    print(f"✅ Job déclenché. run_id={parent_run_id}")

    # 2) Attend la fin
    wait_for_run_terminated(w, parent_run_id, timeout_seconds=args.timeout_seconds, poll_seconds=args.poll_seconds)

    # 3) Récupère l'output sur le run enfant de la task
    output_run_id = resolve_output_run_id(w, parent_run_id, task_key=args.task_key)
    out = w.jobs.get_run_output(run_id=output_run_id)
    remote_csv = (out.notebook_output.result or "").strip()

    if not remote_csv:
        raise SystemExit(
            "❌ Le notebook n’a pas renvoyé de chemin.\n"
            "Vérifie qu’il se termine par: dbutils.notebook.exit(final_csv)"
        )

    print("📄 CSV distant :", remote_csv)

    # 4) Télécharge le CSV en local
    local_csv = Path(args.local_csv)
    download_file(w, remote_csv, local_csv)
    print("📥 CSV local   :", local_csv)


if __name__ == "__main__":
    main()

