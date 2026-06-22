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
    os.environ["GX_ANALYTICS_ENABLED"] = "False"
    return gx.get_context(mode="ephemeral")


def ensure_suite(context, suite_name: str):
    try:
        context.suites.get(suite_name)
    except Exception:
        context.suites.add(gx.ExpectationSuite(name=suite_name))


def make_validator_from_df(context, df: pd.DataFrame, name: str):
    ds = context.data_sources.add_pandas(name=f"pandas_runtime__{name}")
    asset = ds.add_dataframe_asset(name=f"{name}_asset")
    batch_request = asset.build_batch_request(options={"dataframe": df})

    suite_name = f"{name}_suite"
    ensure_suite(context, suite_name)

    return context.get_validator(
        batch_request=batch_request,
        expectation_suite_name=suite_name,
    )


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


def add_critical_expectations(v):
    v.expect_column_values_to_be_in_set("source", ["manga_news"])
    v.expect_column_values_to_be_in_set("collection", ["populaires"])

    # NOTE: si tu as backfillé schema_version => adapte ici au standard choisi
    # v.expect_column_values_to_be_in_set("schema_version", ["manganews.populaires.v1"])
    v.expect_column_values_to_be_in_set("schema_version", ["manganews.populaires.v1", "manganews:populaires:v1"])

    v.expect_column_values_to_be_in_set("enrich_version", ["enrich_item:v2"])

    v.expect_column_values_to_not_be_null("category")
    v.expect_column_values_to_not_match_regex("category", r"^\s*$")

    v.expect_column_values_to_not_be_null("rank_in_category")
    v.expect_column_values_to_be_between("rank_in_category", min_value=1, max_value=500)

    v.expect_column_values_to_not_be_null("title")
    v.expect_column_values_to_not_match_regex("title", r"^\s*$")

    v.expect_column_values_to_not_be_null("serie_url")
    v.expect_column_values_to_be_unique("serie_url")
    v.expect_column_values_to_match_regex("serie_url", r"^https?://")

    v.expect_column_values_to_not_be_null("serie_slug")
    v.expect_column_values_to_not_match_regex("serie_slug", r"^\s*$")

    v.expect_column_values_to_not_be_null("image_url")
    v.expect_column_values_to_match_regex("image_url", r"^https?://")

    v.expect_column_values_to_not_be_null("volumes_count")
    v.expect_column_values_to_be_between("volumes_count", min_value=1, max_value=500)

    v.expect_column_values_to_not_be_null("volumes_text")
    v.expect_column_values_to_match_regex("volumes_text", r"^\d+\s+Volume\(s\)$")

    v.expect_compound_columns_to_be_unique(["category", "rank_in_category"])

    v.expect_column_values_to_be_in_set("scraped_at_is_parseable", [True])


def add_warning_expectations(v):
    v.expect_column_values_to_be_in_set("volumes_text_count_consistent", [True], mostly=0.99)
    v.expect_column_values_to_be_in_set("genres_is_list", [True], mostly=0.99)
    v.expect_column_values_to_be_in_set("genres_urls_is_list", [True], mostly=0.99)
    v.expect_column_values_to_be_in_set("genres_norm_is_list", [True], mostly=0.99)
    v.expect_column_values_to_be_in_set("rag_is_empty_as_expected", [True], mostly=0.99)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--file", default="data/enriched/populaires.jsonl")
    p.add_argument("--report-dir", default="reports/gx")
    p.add_argument("--report-name", default="populaires_report.json")
    args = p.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Fichier introuvable: {path}", file=sys.stderr)
        return 2

    df = pd.read_json(path, lines=True)

    # ===== Flags calculés =====
    scraped = pd.to_datetime(df.get("scraped_at"), errors="coerce", utc=True)
    df["scraped_at_is_parseable"] = ~scraped.isna()

    def _extract_int(s):
        if not _non_empty_str(s):
            return None
        m = re.search(r"(\d+)", str(s))
        return int(m.group(1)) if m else None

    vt = df.get("volumes_text").apply(_extract_int)
    vc = pd.to_numeric(df.get("volumes_count"), errors="coerce")
    df["volumes_text_count_consistent"] = (vt == vc).fillna(False)

    df["genres_is_list"] = df.get("genres").apply(lambda x: isinstance(x, list))
    df["genres_urls_is_list"] = df.get("genres_urls").apply(lambda x: isinstance(x, list))
    df["genres_norm_is_list"] = df.get("genres_norm").apply(lambda x: isinstance(x, list))

    idx = df.get("indexable_rag") == True  # noqa: E712
    rag_len = pd.to_numeric(df.get("rag_char_len"), errors="coerce").fillna(0)
    rag_text_ok_empty = df.get("rag_text").apply(lambda x: str(x) == "")
    df["rag_is_empty_as_expected"] = (~idx) & (rag_len == 0) & rag_text_ok_empty

    # ===== GX runtime =====
    context = build_runtime_context()

    vcrit = make_validator_from_df(context, df, name="populaires_critical")
    add_critical_expectations(vcrit)
    rcrit = vcrit.validate()
    okcrit = bool(rcrit.get("success", False))
    print("CRITICAL success =", okcrit)

    rwarn = None
    okwarn = None

    if okcrit:
        vwarn = make_validator_from_df(context, df, name="populaires_warning")
        add_warning_expectations(vwarn)
        rwarn = vwarn.validate()
        okwarn = bool(rwarn.get("success", False))
        print("WARNING success  =", okwarn)
        if not okwarn:
            summarize_failures(rwarn)
    else:
        summarize_failures(rcrit)

    # ===== Report JSON =====
    report = {
        "dataset": "populaires",
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
        "warning": None,
    }
    if rwarn is not None:
        report["warning"] = {
            "success": okwarn,
            "statistics": rwarn.get("statistics", {}),
            "failed_expectations": extract_failed_expectations(rwarn),
        }

    out = write_json_report(args.report_dir, args.report_name, report)
    print("Report written:", out)

    return 0 if okcrit else 1


if __name__ == "__main__":
    raise SystemExit(main())
