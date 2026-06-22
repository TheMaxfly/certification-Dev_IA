#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import psycopg2
from psycopg2.extras import register_uuid
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
REPORT_SUMMARY = ROOT / "reports" / "gx" / "summary_report.json"

DATASET_CONFIG = {
    "series": {
        "import_script": "scripts/run_import_series.py",
        "default_file": "data/enriched/manganews_series.backfilled.jsonl",
        "staging_table": "manga.mn_series_staging",
    },
    "populaires": {
        "import_script": "scripts/run_import_populaires.py",
        "default_file": "data/enriched/populaires.backfilled.jsonl",
        "staging_table": "manga.mn_populaires_staging",
    },
}

# Ton orchestrateur “backfill + validations GX”
PIPELINE_VALIDATE_SCRIPT = "scripts/run_pipeline_backfill_then_validate.py"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def run_cmd(cmd: list[str]) -> Tuple[int, str]:
    """Run command, return (exit_code, combined_output)."""
    p = subprocess.run(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=os.environ.copy(),
    )
    return p.returncode, p.stdout


def read_summary_report(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Rapport GX introuvable: {path}. Ton pipeline GX a-t-il bien tourné ?")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def extract_gx_success(summary: Dict[str, Any], dataset: str) -> Optional[bool]:
    """
    Essaie d'être tolérant car la structure du summary_report peut varier.
    On cherche un booléen de succès pour 'series' / 'populaires'.
    """
    dataset_keys = [dataset]
    if dataset == "series":
        dataset_keys.append("manganews_series")
    elif dataset == "populaires":
        dataset_keys.append("manganews_populaires")

    # cas 1: {"datasets": {"series": {"success": true}}}
    if isinstance(summary.get("datasets"), dict):
        for key in dataset_keys:
            d = summary["datasets"].get(key)
            if isinstance(d, dict):
                for k in ("success", "gx_success", "ok", "passed"):
                    if k in d and isinstance(d[k], bool):
                        return d[k]

    # cas 2: {"results": [{"dataset":"series","success":true}, ...]}
    if isinstance(summary.get("results"), list):
        for r in summary["results"]:
            if isinstance(r, dict) and r.get("dataset") in dataset_keys:
                for k in ("success", "gx_success", "ok", "passed"):
                    if k in r and isinstance(r[k], bool):
                        return r[k]

    # cas 3: exit_codes (0 == OK)
    if isinstance(summary.get("exit_codes"), dict):
        for key in dataset_keys:
            code = summary["exit_codes"].get(key)
            if isinstance(code, int):
                return code == 0

    # cas 3: clés directes
    for key in dataset_keys:
        for k in (f"{key}_success", f"{key}_gx_success"):
            if k in summary and isinstance(summary[k], bool):
                return summary[k]

    return None


def ensure_runs_table(conn) -> None:
    """
    Table d'audit centralisée.
    Tu l'as déjà, mais on la sécurise en IF NOT EXISTS.
    """
    sql = """
    CREATE TABLE IF NOT EXISTS manga.mn_import_runs (
      run_id uuid PRIMARY KEY,
      dataset text NOT NULL,
      gx_success boolean NOT NULL,
      rows_staging integer NOT NULL,
      rows_merged integer NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      source_file text
    );
    """
    with conn.cursor() as cur:
        cur.execute(sql)


def upsert_run_log(
    conn,
    run_id: uuid.UUID,
    dataset: str,
    gx_success: bool,
    rows_staging: int,
    rows_merged: int,
    source_file: str,
) -> None:
    sql = """
    INSERT INTO manga.mn_import_runs(run_id, dataset, gx_success, rows_staging, rows_merged, created_at, source_file)
    VALUES (%s, %s, %s, %s, %s, now(), %s)
    ON CONFLICT (run_id) DO UPDATE SET
      dataset = EXCLUDED.dataset,
      gx_success = EXCLUDED.gx_success,
      rows_staging = EXCLUDED.rows_staging,
      rows_merged = EXCLUDED.rows_merged,
      source_file = EXCLUDED.source_file,
      created_at = EXCLUDED.created_at;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (run_id, dataset, gx_success, rows_staging, rows_merged, source_file))


def purge_staging(conn, staging_table: str, keep_days: int) -> int:
    sql = f"""
    DELETE FROM {staging_table}
    WHERE loaded_at < (now() - (%s || ' days')::interval);
    """
    with conn.cursor() as cur:
        cur.execute(sql, (keep_days,))
        return cur.rowcount


def parse_import_output(out: str) -> Tuple[int, int]:
    """
    Parse stdout de run_import_*.py:
      staging_inserted: N
      final_upsert_input_rows: M
    """
    m1 = re.search(r"staging_inserted:\s*(\d+)", out)
    m2 = re.search(r"final_upsert_input_rows:\s*(\d+)", out)
    if not (m1 and m2):
        raise SystemExit(
            "Impossible de parser la sortie de l'import.\n"
            "Assure-toi que run_import_* affiche bien 'staging_inserted:' et 'final_upsert_input_rows:'.\n\n"
            f"--- sortie ---\n{out}\n--- fin ---"
        )
    return int(m1.group(1)), int(m2.group(1))


def main() -> None:
    dotenv_path = ROOT / ".env"
    load_dotenv(dotenv_path=dotenv_path)
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["series", "populaires"], required=True)
    ap.add_argument("--file", default=None, help="Chemin du backfilled.jsonl (sinon valeur par défaut)")
    ap.add_argument("--dsn", default=os.getenv("POSTGRES_DSN"), help="DSN Postgres (sinon env POSTGRES_DSN)")
    ap.add_argument("--run-id", default=None, help="UUID imposé (sinon généré)")
    ap.add_argument("--keep-days", type=int, default=30, help="Rétention staging en jours")
    ap.add_argument("--skip-gx", action="store_true", help="(debug) saute GX et importe quand même")
    args = ap.parse_args()

    if not args.dsn:
        raise SystemExit("DSN manquant : exporte POSTGRES_DSN ou passe --dsn")

    cfg = DATASET_CONFIG[args.dataset]
    backfilled_file = args.file or cfg["default_file"]
    import_script = cfg["import_script"]
    staging_table = cfg["staging_table"]

    run_id = uuid.UUID(args.run_id) if args.run_id else uuid.uuid4()

    # 1) Backfill + GX
    gx_success = True
    gx_out = ""
    if not args.skip_gx:
        code, gx_out = run_cmd([sys.executable, PIPELINE_VALIDATE_SCRIPT])
        if code != 0:
            print(gx_out)
            raise SystemExit(f"Le pipeline GX a échoué (exit code {code}). Import DB annulé.")

        summary = read_summary_report(REPORT_SUMMARY)
        s = extract_gx_success(summary, args.dataset)
        if s is None:
            # sécurité: on préfère bloquer plutôt qu'importer sans preuve
            print(json.dumps(summary, indent=2, ensure_ascii=False)[:2000])
            raise SystemExit(
                f"Je ne trouve pas le statut GX pour dataset='{args.dataset}' dans {REPORT_SUMMARY}.\n"
                "Import DB annulé par sécurité."
            )
        gx_success = bool(s)

        if not gx_success:
            raise SystemExit(f"GX = KO pour dataset='{args.dataset}'. Import DB annulé.")

    # 2) Import staging + merge final (avec run_id)
    # On force --keep-staging pour audit + purge 30 jours.
    cmd_import = [
        sys.executable,
        import_script,
        "--file",
        backfilled_file,
        "--run-id",
        str(run_id),
        "--keep-staging",
    ]
    code, out = run_cmd(cmd_import)
    if code != 0:
        print(out)
        raise SystemExit(f"Import DB échoué (exit code {code}).")

    rows_staging, rows_merged = parse_import_output(out)

    # 3) Audit + purge staging
    conn = psycopg2.connect(args.dsn)
    register_uuid(conn)
    conn.autocommit = False
    try:
        ensure_runs_table(conn)
        upsert_run_log(
            conn,
            run_id=run_id,
            dataset=args.dataset,
            gx_success=gx_success,
            rows_staging=rows_staging,
            rows_merged=rows_merged,
            source_file=backfilled_file,
        )
        purged = purge_staging(conn, staging_table, args.keep_days)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # 4) Sortie lisible
    print("OK PROD IMPORT")
    print("dataset:", args.dataset)
    print("run_id:", str(run_id))
    print("gx_success:", gx_success)
    print("rows_staging:", rows_staging)
    print("rows_merged:", rows_merged)
    print("purged_staging_rows:", purged)
    print("file:", backfilled_file)


if __name__ == "__main__":
    main()
