#!/usr/bin/env python3
import argparse
import subprocess
import sys


def run(cmd: list[str]) -> int:
    p = subprocess.run(cmd)
    return p.returncode


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run backfill + GX validations, then import if OK."
    )
    ap.add_argument("--series-file", default="data/enriched/manganews_series.jsonl")
    ap.add_argument("--pop-file", default="data/enriched/populaires.jsonl")
    ap.add_argument(
        "--series-backfilled",
        default="data/enriched/manganews_series.backfilled.jsonl",
    )
    ap.add_argument(
        "--pop-backfilled",
        default="data/enriched/populaires.backfilled.jsonl",
    )
    ap.add_argument("--report-dir", default="reports/gx")
    ap.add_argument("--summary-name", default="summary_report.json")
    ap.add_argument("--no-backfill", action="store_true", help="Skip backfill step")
    ap.add_argument("--skip-import", action="store_true", help="Skip import step")
    ap.add_argument("--dsn", default=None, help="Optional DSN override for imports")
    args = ap.parse_args()

    do_backfill = not args.no_backfill

    cmd_validate = [
        sys.executable,
        "scripts/run_all_validations_gx110.py",
        "--report-dir",
        args.report_dir,
        "--summary-name",
        args.summary_name,
        "--series-file",
        args.series_file,
        "--pop-file",
        args.pop_file,
        "--series-backfilled",
        args.series_backfilled,
        "--pop-backfilled",
        args.pop_backfilled,
    ]
    if do_backfill:
        cmd_validate.append("--do-backfill")

    rc = run(cmd_validate)
    if rc != 0:
        return rc

    if args.skip_import:
        print("Validation OK; import skipped (--skip-import).")
        return 0

    series_import_file = args.series_backfilled if do_backfill else args.series_file
    pop_import_file = args.pop_backfilled if do_backfill else args.pop_file

    cmd_series = [
        sys.executable,
        "scripts/run_import_series.py",
        "--file",
        series_import_file,
    ]
    cmd_pop = [
        sys.executable,
        "scripts/run_import_populaires.py",
        "--file",
        pop_import_file,
    ]
    if args.dsn:
        cmd_series += ["--dsn", args.dsn]
        cmd_pop += ["--dsn", args.dsn]

    rc = run(cmd_series)
    if rc != 0:
        return rc
    return run(cmd_pop)


if __name__ == "__main__":
    raise SystemExit(main())
