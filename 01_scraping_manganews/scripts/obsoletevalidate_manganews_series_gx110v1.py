import argparse
import sys
from pathlib import Path

import pandas as pd

import great_expectations as gx


def _non_empty_str(s) -> bool:
    if s is None:
        return False
    try:
        if pd.isna(s):
            return False
    except Exception:
        pass
    return str(s).strip() != ""


def build_runtime_context():
    # Contexte ephemere (runtime), sans great_expectations.yml
    # Pour couper l analytics en ephemere: variable d env
    import os
    os.environ["GX_ANALYTICS_ENABLED"] = "False"
    return gx.get_context(mode="ephemeral")


def add_critical_expectations(validator):
    validator.expect_column_values_to_not_be_null("url")
    validator.expect_column_values_to_be_unique("url")
    validator.expect_column_values_to_match_regex("url", r"^https?://")

    validator.expect_column_values_to_not_be_null("title_page")
    validator.expect_column_values_to_not_match_regex("title_page", r"^\s*$")

    validator.expect_column_values_to_be_in_set("schema_version", ["manganews.series.v1"])
    validator.expect_column_values_to_be_in_set("enrich_version", ["enrich_jsonl.v1"])

    validator.expect_column_values_to_be_in_set("scraped_at_is_parseable", [True])
    validator.expect_column_values_to_be_in_set("rag_is_consistent", [True])
    validator.expect_column_values_to_be_in_set("resume_is_consistent", [True])


def add_warning_expectations(validator):
    validator.expect_column_values_to_be_in_set("origin_year_is_plausible", [True], mostly=0.99)
    validator.expect_column_values_to_be_in_set("genres_norm_is_list", [True], mostly=0.99)
    validator.expect_column_values_to_not_be_null("type_norm", mostly=0.99)


def summarize_failures(result, limit=20):
    failed = []
    for r in result.get("results", []):
        if not r.get("success", True):
            exp = r.get("expectation_config", {}).get("expectation_type")
            kwargs = r.get("expectation_config", {}).get("kwargs")
            failed.append((exp, kwargs))
    if not failed:
        return
    print(f"FAILED expectations (showing up to {limit}):")
    for exp, kwargs in failed[:limit]:
        print(" -", exp, kwargs)


def make_validator_from_df(context, df: pd.DataFrame, name: str):
    ds = context.data_sources.add_pandas(name=f"pandas_runtime__{name}")
    asset = ds.add_dataframe_asset(name=f"{name}_asset")
    batch_request = asset.build_batch_request(options={"dataframe": df})

    suite_name = f"{name}_suite"
    try:
        context.suites.get(suite_name)
    except Exception:
        context.suites.add(gx.ExpectationSuite(name=suite_name))

    return context.get_validator(
        batch_request=batch_request,
        expectation_suite_name=suite_name,
    )




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
    rag_len_ok = (pd.to_numeric(df.get("rag_char_len"), errors="coerce").fillna(0) > 0)
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
    v = make_validator_from_df(context, df, name="manganews_series_critical")
    add_critical_expectations(v)
    critical = v.validate()
    critical_ok = bool(critical.get("success", False))
    print("CRITICAL success =", critical_ok)
    if not critical_ok:
        summarize_failures(critical)
        return 1

    # WARNING (non bloquant)
    v2 = make_validator_from_df(context, df, name="manganews_series_warning")
    add_warning_expectations(v2)
    warning = v2.validate()
    warning_ok = bool(warning.get("success", False))
    print("WARNING success =", warning_ok)
    if not warning_ok:
        summarize_failures(warning)

    from scripts.gx_report_utils import (
    utc_now_iso,
    try_git_commit,
    extract_failed_expectations,
    write_json_report,
)

    report = {
        "dataset": "manganews_series",
        "file": str(path),
        "rows": int(len(df)),
        "gx_version": gx.__version__,
        "run_at_utc": utc_now_iso(),
        "git_commit": try_git_commit(),
        "critical": {
            "success": okcrit,
            "statistics": rcrit.get("statistics", {}),
            "failed_expectations": extract_failed_expectations(rcrit),
        },
        "warning": {
            "success": okwarn,
            "statistics": rwarn.get("statistics", {}),
            "failed_expectations": extract_failed_expectations(rwarn),
        },
    }

    out = write_json_report(args.report_dir, args.report_name, report)
    print("Report written:", out)
        

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
