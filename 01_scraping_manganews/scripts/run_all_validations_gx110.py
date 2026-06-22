import argparse
import subprocess
import sys
from pathlib import Path

from gx_report_utils import utc_now_iso, try_git_commit, write_json_report


def run(cmd: list[str]) -> tuple[int, str]:
    """Run a command and return (exit_code, combined_output)."""
    p = subprocess.run(cmd, text=True, capture_output=True)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run backfill (optional) + GX 1.10 validations and write a summary JSON report."
    )

    # Reports
    ap.add_argument("--report-dir", default="reports/gx", help="Directory where reports are written")
    ap.add_argument("--summary-name", default="summary_report.json", help="Filename for the global summary report")

    # Inputs (raw)
    ap.add_argument("--series-file", default="data/enriched/manganews_series.jsonl", help="Path to series JSONL input")
    ap.add_argument("--pop-file", default="data/enriched/populaires.jsonl", help="Path to populaires JSONL input")

    # Backfill
    ap.add_argument("--do-backfill", action="store_true", help="Run backfill_jsonl.py before validations")
    ap.add_argument(
        "--series-backfilled",
        default="data/enriched/manganews_series.backfilled.jsonl",
        help="Output path for backfilled series JSONL",
    )
    ap.add_argument(
        "--pop-backfilled",
        default="data/enriched/populaires.backfilled.jsonl",
        help="Output path for backfilled populaires JSONL",
    )

    # Per-dataset report filenames
    ap.add_argument("--series-report-name", default="manganews_series_report.json")
    ap.add_argument("--pop-report-name", default="populaires_report.json")

    args = ap.parse_args()

    Path(args.report_dir).mkdir(parents=True, exist_ok=True)

    # ---- optional backfill step ----
    backfill_info = {
        "enabled": bool(args.do_backfill),
        "series": {"cmd": None, "exit_code": None, "log_tail": None, "in": args.series_file, "out": args.series_backfilled},
        "populaires": {"cmd": None, "exit_code": None, "log_tail": None, "in": args.pop_file, "out": args.pop_backfilled},
    }

    if args.do_backfill:
        cmd_b1 = [
            sys.executable,
            "scripts/backfill_jsonl.py",
            "--in",
            args.series_file,
            "--out",
            args.series_backfilled,
            "--kind",
            "series",
        ]
        cmd_b2 = [
            sys.executable,
            "scripts/backfill_jsonl.py",
            "--in",
            args.pop_file,
            "--out",
            args.pop_backfilled,
            "--kind",
            "populaires",
        ]

        rc_b1, out_b1 = run(cmd_b1)
        rc_b2, out_b2 = run(cmd_b2)

        backfill_info["series"]["cmd"] = cmd_b1
        backfill_info["series"]["exit_code"] = rc_b1
        backfill_info["series"]["log_tail"] = out_b1[-8000:]

        backfill_info["populaires"]["cmd"] = cmd_b2
        backfill_info["populaires"]["exit_code"] = rc_b2
        backfill_info["populaires"]["log_tail"] = out_b2[-8000:]

        # If backfill fails: write summary and stop (avoid validating stale inputs)
        if rc_b1 != 0 or rc_b2 != 0:
            summary = {
                "run_at_utc": utc_now_iso(),
                "git_commit": try_git_commit(),
                "step": "backfill",
                "backfill": backfill_info,
                "overall_success": False,
            }
            summary_path = write_json_report(args.report_dir, args.summary_name, summary)
            print("Summary report written:", summary_path)
            return 1

        # After successful backfill, validate the backfilled files
        args.series_file = args.series_backfilled
        args.pop_file = args.pop_backfilled

    # ---- validation step (calls your validators which write per-dataset JSON reports) ----
    cmd_series = [
        sys.executable,
        "scripts/validate_manganews_series_gx110.py",
        "--file",
        args.series_file,
        "--report-dir",
        args.report_dir,
        "--report-name",
        args.series_report_name,
    ]

    cmd_pop = [
        sys.executable,
        "scripts/validate_populaires_gx110.py",
        "--file",
        args.pop_file,
        "--report-dir",
        args.report_dir,
        "--report-name",
        args.pop_report_name,
    ]

    rc_series, out_series = run(cmd_series)
    rc_pop, out_pop = run(cmd_pop)

    overall_success = (rc_series == 0 and rc_pop == 0)

    summary = {
        "run_at_utc": utc_now_iso(),
        "git_commit": try_git_commit(),
        "inputs_used_for_validation": {
            "manganews_series_file": args.series_file,
            "populaires_file": args.pop_file,
        },
        "backfill": backfill_info,
        "reports": {
            "manganews_series": str(Path(args.report_dir) / args.series_report_name),
            "populaires": str(Path(args.report_dir) / args.pop_report_name),
        },
        "exit_codes": {
            "manganews_series": rc_series,
            "populaires": rc_pop,
        },
        "logs_tail": {
            "manganews_series": out_series[-8000:],
            "populaires": out_pop[-8000:],
        },
        "overall_success": overall_success,
    }

    summary_path = write_json_report(args.report_dir, args.summary_name, summary)
    print("Summary report written:", summary_path)

    return 0 if overall_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
