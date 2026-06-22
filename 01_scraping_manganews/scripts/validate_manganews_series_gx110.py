import argparse
import os
import re
import sys
from pathlib import Path

import pandas as pd
import great_expectations as gx

from gx_report_utils import (
    utc_now_iso,
    try_git_commit,
    extract_failed_expectations,
    write_json_report,
)


def _non_empty_str(x) -> bool:
    if x is None:
        return False
    try:
        if pd.isna(x):
            return False
    except Exception:
        pass
    return str(x).strip() != ""


def build_runtime_context():
    # Contexte GX runtime (sans great_expectations.yml)
    os.environ["GX_ANALYTICS_ENABLED"] = "False"
    return gx.get_context(mode="ephemeral")


def ensure_suite(context, suite_name: str):
    try:
        context.suites.get(suite_name)
    except Exception:
        context.suites.add(gx.ExpectationSuite(name=suite_name))


def make_validator_from_df(context, df: pd.DataFrame, name: str):
    # Datasource unique pour éviter les lookups/store
    ds = context.data_sources.add_pandas(name=f"pandas_runtime__{name}")
    asset = ds.add_dataframe_asset(name=f"{name}_asset")
    batch_request = asset.build_batch_request(options={"dataframe": df})

    suite_name = f"{name}_suite"
    ensure_suite(context, suite_name)

    return context.get_validator(
        batch_request=batch_request,
        expectation_suite_name=suite_name,
    )


def add_critical_expectations(v):
    v.expect_column_to_exist("url")
    v.expect_column_values_to_not_be_null("url")
    v.expect_column_values_to_be_unique("url")
    v.expect_column_values_to_match_regex("url", r"^https?://")

    v.expect_column_to_exist("title_page")
    v.expect_column_values_to_not_be_null("title_page")
    v.expect_column_values_to_not_match_regex("title_page", r"^\s*$")

    v.expect_column_to_exist("schema_version")
    v.expect_column_values_to_be_in_set("schema_version", ["manganews.series.v1"])

    v.expect_column_to_exist("enrich_version")
    # adapte la liste si tu as plusieurs enrich_version en prod
    v.expect_column_values_to_be_in_set("enrich_version", ["enrich_jsonl.v1", "enrich_item:v2"])

    v.expect_column_to_exist("scraped_at_is_parseable")
    v.expect_column_values_to_be_in_set("scraped_at_is_parseable", [True])

    v.expect_column_to_exist("rag_is_consistent")
    v.expect_column_values_to_be_in_set("rag_is_consistent", [True])

    v.expect_column_to_exist("resume_is_consistent")
    v.expect_column_values_to_be_in_set("resume_is_consistent", [True])


def add_warning_expectations(v):
    v.expect_column_to_exist("origin_year_is_plausible")
    v.expect_column_values_to_be_in_set("origin_year_is_plausible", [True], mostly=0.99)

    v.expect_column_to_exist("genres_norm_is_list")
    v.expect_column_values_to_be_in_set("genres_norm_is_list", [True], mostly=0.99)

    v.expect_column_to_exist("type_norm")
    v.expect_column_values_to_not_be_null("type_norm", mostly=0.99)


def summarize_failures(result, limit=25):
    failed = []
    for r in result.get("results", []):
        if not r.get("success", True):
            exp = r.get("expectation_config", {}).get("expectation_type")
            kwargs = r.get("expectation_config", {}).get("kwargs")
            failed.append((exp, kwargs))
    if not failed:
        return
    print(f"FAILED expectations (up to {limit}):")
    for exp, kwargs in failed[:limit]:
        print(" -", exp, kwargs)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--file", default="data/enriched/manganews_series.jsonl")
    p.add_argument("--min-year", type=int, default=1950)
    p.add_argument("--max-year", type=int, default=2026)
    p.add_argument("--report-dir", default="reports/gx")
    p.add_argument("--report-name", default="manganews_series_report.json")
    args = p.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Fichier introuvable: {path}", file=sys.stderr)
        return 2

    df = pd.read_json(path, lines=True)

    # ===== Flags (conditionnelles -> bool) =====
    scraped = pd.to_datetime(df.get("scraped_at"), errors="coerce", utc=True)
    df["scraped_at_is_parseable"] = ~scraped.isna()

    idx = df.get("indexable_rag") == True  # noqa: E712
    rag_len_ok = pd.to_numeric(df.get("rag_char_len"), errors="coerce").fillna(0) > 0
    rag_text_ok = df.get("rag_text").apply(_non_empty_str)
    df["rag_is_consistent"] = (~idx) | (rag_len_ok & rag_text_ok)

    has_res = df.get("has_resume") == True  # noqa: E712
    resume_ok = df.get("resume").apply(_non_empty_str)
    df["resume_is_consistent"] = (~has_res) | resume_ok

    has_year = df.get("origin_has_year") == True  # noqa: E712
    year = pd.to_numeric(df.get("origin_year"), errors="coerce")
    year_ok = year.between(args.min_year, args.max_year, inclusive="both")
    df["origin_year_is_plausible"] = (~has_year) | year_ok

    df["genres_norm_is_list"] = df.get("genres_norm").apply(lambda x: isinstance(x, list))

    # ===== GX runtime =====
    context = build_runtime_context()

    # CRITICAL
    vcrit = make_validator_from_df(context, df, name="manganews_series_critical")
    add_critical_expectations(vcrit)
    critical = vcrit.validate()
    critical_ok = bool(critical.get("success", False))
    print("CRITICAL success =", critical_ok)

    warning = None
    warning_ok = None

    # WARNING (non bloquant)
    if critical_ok:
        vwarn = make_validator_from_df(context, df, name="manganews_series_warning")
        add_warning_expectations(vwarn)
        warning = vwarn.validate()
        warning_ok = bool(warning.get("success", False))
        print("WARNING success  =", warning_ok)
        if not warning_ok:
            summarize_failures(warning)
    else:
        summarize_failures(critical)

    # ===== Report JSON =====
    report = {
        "dataset": "manganews_series",
        "file": str(path),
        "rows": int(len(df)),
        "gx_version": gx.__version__,
        "run_at_utc": utc_now_iso(),
        "git_commit": try_git_commit(),
        "critical": {
            "success": critical_ok,
            "statistics": critical.get("statistics", {}),
            "failed_expectations": extract_failed_expectations(critical),
        },
        "warning": None,
    }
    if warning is not None:
        report["warning"] = {
            "success": warning_ok,
            "statistics": warning.get("statistics", {}),
            "failed_expectations": extract_failed_expectations(warning),
        }

    out = write_json_report(args.report_dir, args.report_name, report)
    print("Report written:", out)

    return 0 if critical_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
