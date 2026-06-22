#!/usr/bin/env python3
import argparse
import json
import datetime as dt
from pathlib import Path


def truthy_text(x) -> bool:
    if x is None:
        return False
    s = str(x).strip()
    return s != "" and s.lower() != "nan"


def to_int_safe(x):
    try:
        if x is None:
            return None
        return int(float(x))
    except Exception:
        return None


def normalize_source(value):
    if value is None:
        return None
    s = str(value).strip()
    if s in ("manganews", "manga_news"):
        return "manga_news"
    return s


def backfill_record(rec: dict, *, file_kind: str) -> dict:
    # file_kind: "series" or "populaires"
    # 1) slug key cleanup
    if "serie_slug" not in rec and "series_slug" in rec:
        rec["serie_slug"] = rec.pop("series_slug")
    rec.pop("series_slug", None)

    # 2) schema_version normalization (harmonise un style unique)
    if file_kind == "populaires":
        rec["schema_version"] = "manganews.populaires.v1"
    else:
        rec["schema_version"] = "manganews.series.v1"

    # 2b) source normalization
    rec["source"] = normalize_source(rec.get("source"))

    # 3) scraped_at : ne pas écraser si déjà présent (important traçabilité)
    if not truthy_text(rec.get("scraped_at")):
        rec["scraped_at"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

   # 4) enrich_version : par type de fichier
    if not truthy_text(rec.get("enrich_version")):
        rec["enrich_version"] = "enrich_jsonl.v1" if file_kind == "series" else "enrich_item:v2"


    # 5) flags CRITICAL (GX-friendly)
    indexable_rag = bool(rec.get("indexable_rag"))
    rag_text = rec.get("rag_text")
    rag_char_len = rec.get("rag_char_len") or 0
    try:
        rag_char_len = int(rag_char_len)
    except Exception:
        rag_char_len = 0

    rec["rag_is_consistent"] = (not indexable_rag) or (truthy_text(rag_text) and rag_char_len > 0)

    has_resume = bool(rec.get("has_resume"))
    resume = rec.get("resume")
    rec["resume_is_consistent"] = (not has_resume) or truthy_text(resume)

    # 6) flags WARNING
    origin_has_year = bool(rec.get("origin_has_year"))
    origin_year_i = to_int_safe(rec.get("origin_year"))
    current_year = dt.datetime.now(dt.timezone.utc).year
    rec["origin_year_is_realistic"] = (not origin_has_year) or (
        origin_year_i is not None and 1950 <= origin_year_i <= current_year
    )

    rec["genres_norm_is_list"] = isinstance(rec.get("genres_norm"), list)

    rec["type_is_present"] = truthy_text(rec.get("type_norm")) or truthy_text(rec.get("type"))

    return rec


def backfill_jsonl(in_path: Path, out_path: Path, *, file_kind: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

    n = 0
    with in_path.open("r", encoding="utf-8") as fin, tmp_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rec = backfill_record(rec, file_kind=file_kind)
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1

    tmp_path.replace(out_path)
    print(f"[OK] {in_path} -> {out_path} ({n} lignes)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True, help="Chemin du JSONL input")
    ap.add_argument("--out", dest="out_path", required=True, help="Chemin du JSONL output")
    ap.add_argument(
        "--kind",
        choices=["series", "populaires"],
        required=True,
        help="Type de fichier (series ou populaires) pour fixer schema_version"
    )
    args = ap.parse_args()

    backfill_jsonl(Path(args.in_path), Path(args.out_path), file_kind=args.kind)


if __name__ == "__main__":
    main()
