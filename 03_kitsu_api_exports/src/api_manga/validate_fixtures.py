#!/usr/bin/env python3
"""
validate_fixtures.py

Valide des fixtures JSON (issues/rapport) pour un pipeline RAG:
- JSON parseable
- Schéma minimal cohérent (id, titles, synopsis, authors, ratings, popularity, tags)
- Types (string/int/float/list/dict)
- Détection des champs manquants / inattendus
- Rapport lisible + code retour non-zéro si erreurs

Usage:
  python validate_fixtures.py tests/fixtures/*.json
  python validate_fixtures.py --dir tests/fixtures
  python validate_fixtures.py --strict --dir tests/fixtures
  python validate_fixtures.py --generate --strict

Options:
  --strict      : échoue si champs inattendus ou si champs optionnels absents
  --warn-extra  : (défaut) signale champs inattendus sans échouer (hors --strict)
  --max-items N : limite le nombre d'items validés par fichier (défaut: 0 = tout)
  --generate    : génère les 4 exports JSON via l'API Kitsu, puis valide ces fichiers
  --trending-limit N   : limite trending hebdo (défaut: 20)
  --publishing-limit N : limite top publishing (défaut: 100)
  --rated-limit N      : limite top rated (défaut: 0 = tout)
  --popular-limit N    : limite top popular (défaut: 100)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .client import KitsuClient
from .exporter import export_all
from .service import MangaService


# ---------------------------
# Acronymes utiles (doc)
# ---------------------------
# JSON : JavaScript Object Notation
# RAG : Retrieval-Augmented Generation
# LLM : Large Language Model
# ETL : Extract Transform Load
# API : Application Programming Interface
# JSON:API : convention data/included/relationships


@dataclass
class Issue:
    level: str  # "ERROR" | "WARN"
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.path}: {self.message}"


def is_non_empty_str(x: Any) -> bool:
    return isinstance(x, str) and x.strip() != ""


def is_int(x: Any) -> bool:
    # bool est un sous-type de int en Python -> on l'exclut
    return isinstance(x, int) and not isinstance(x, bool)


def is_number(x: Any) -> bool:
    # accepte int/float (hors bool). rejette NaN/inf
    if isinstance(x, bool):
        return False
    if isinstance(x, (int, float)):
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return False
        return True
    return False


def add_issue(issues: List[Issue], level: str, path: str, message: str) -> None:
    issues.append(Issue(level=level, path=path, message=message))


def get_dict(d: Any, path: str, issues: List[Issue], required: bool = True) -> Optional[Dict[str, Any]]:
    if d is None:
        if required:
            add_issue(issues, "ERROR", path, "Expected object/dict, got null.")
        return None
    if not isinstance(d, dict):
        add_issue(issues, "ERROR", path, f"Expected object/dict, got {type(d).__name__}.")
        return None
    return d


def get_list(v: Any, path: str, issues: List[Issue], required: bool = True) -> Optional[List[Any]]:
    if v is None:
        if required:
            add_issue(issues, "ERROR", path, "Expected array/list, got null.")
        return None
    if not isinstance(v, list):
        add_issue(issues, "ERROR", path, f"Expected array/list, got {type(v).__name__}.")
        return None
    return v


def validate_root(payload: Any, file_path: Path, issues: List[Issue], strict: bool) -> Optional[Dict[str, Any]]:
    root = get_dict(payload, str(file_path), issues, required=True)
    if not root:
        return None

    # Attendu: {"data": [...], "meta": {...}} ; tolère d'autres clés (warn)
    if "data" not in root:
        add_issue(issues, "ERROR", f"{file_path}.$", "Missing top-level key 'data'.")
        return None

    if "meta" not in root:
        if strict:
            add_issue(issues, "ERROR", f"{file_path}.$", "Missing top-level key 'meta'.")
        else:
            add_issue(issues, "WARN", f"{file_path}.$", "Missing top-level key 'meta' (recommended: fetched_at, source, category...).")
    else:
        meta = get_dict(root.get("meta"), f"{file_path}.meta", issues, required=True)
        if meta is not None:
            required_meta = {"category", "source", "endpoint", "fetched_at", "limit", "offset"}
            for k in required_meta:
                if k not in meta:
                    add_issue(
                        issues,
                        "ERROR" if strict else "WARN",
                        f"{file_path}.meta",
                        f"Missing meta key '{k}'.",
                    )
            if "category" in meta and not is_non_empty_str(meta.get("category")):
                add_issue(issues, "ERROR", f"{file_path}.meta.category", "Must be a non-empty string.")
            if "source" in meta and not is_non_empty_str(meta.get("source")):
                add_issue(issues, "ERROR", f"{file_path}.meta.source", "Must be a non-empty string.")
            if "endpoint" in meta and not is_non_empty_str(meta.get("endpoint")):
                add_issue(issues, "ERROR", f"{file_path}.meta.endpoint", "Must be a non-empty string.")
            if "fetched_at" in meta and not is_non_empty_str(meta.get("fetched_at")):
                add_issue(issues, "ERROR", f"{file_path}.meta.fetched_at", "Must be a non-empty string.")
            if "limit" in meta and meta.get("limit") is not None and not is_int(meta.get("limit")):
                add_issue(issues, "ERROR", f"{file_path}.meta.limit", "Must be int or null.")
            if "offset" in meta and meta.get("offset") is not None and not is_int(meta.get("offset")):
                add_issue(issues, "ERROR", f"{file_path}.meta.offset", "Must be int or null.")
            allowed_meta = required_meta
            for k in meta.keys():
                if k not in allowed_meta:
                    if strict:
                        add_issue(
                            issues,
                            "ERROR",
                            f"{file_path}.meta",
                            f"Unexpected meta key '{k}'. Allowed: {sorted(allowed_meta)}",
                        )
                    else:
                        add_issue(issues, "WARN", f"{file_path}.meta", f"Unexpected meta key '{k}'.")

    # Champs inattendus
    allowed_top = {"data", "meta"}
    for k in root.keys():
        if k not in allowed_top:
            if strict:
                add_issue(issues, "ERROR", f"{file_path}.$", f"Unexpected top-level key '{k}'. Allowed: {sorted(allowed_top)}")
            else:
                add_issue(issues, "WARN", f"{file_path}.$", f"Unexpected top-level key '{k}'.")

    return root


def validate_item(item: Any, base_path: str, issues: List[Issue], strict: bool) -> None:
    obj = get_dict(item, base_path, issues, required=True)
    if not obj:
        return

    # Schéma cible minimal (recommandé)
    required_keys = {"id", "slug", "titles", "status", "synopsis", "authors", "ratings", "popularity", "tags"}
    allowed_keys = set(required_keys)

    for k in required_keys:
        if k not in obj:
            add_issue(issues, "ERROR", base_path, f"Missing required key '{k}'.")

    for k in obj.keys():
        if k not in allowed_keys:
            if strict:
                add_issue(issues, "ERROR", base_path, f"Unexpected key '{k}'. Allowed: {sorted(allowed_keys)}")
            else:
                add_issue(issues, "WARN", base_path, f"Unexpected key '{k}'.")

    # id
    if "id" in obj and not is_non_empty_str(obj.get("id")):
        add_issue(issues, "ERROR", f"{base_path}.id", "Must be a non-empty string.")

    # slug/status (string|null)
    if "slug" in obj and obj["slug"] is not None and not is_non_empty_str(obj["slug"]):
        add_issue(issues, "ERROR", f"{base_path}.slug", "Must be a non-empty string or null.")
    if "status" in obj and obj["status"] is not None and not is_non_empty_str(obj["status"]):
        add_issue(issues, "ERROR", f"{base_path}.status", "Must be a non-empty string or null.")

    # titles
    titles = get_dict(obj.get("titles"), f"{base_path}.titles", issues, required=True)
    if titles is not None:
        # canonical recommandé
        if "canonical" not in titles:
            add_issue(issues, "WARN", f"{base_path}.titles", "Missing 'canonical' title (recommended).")
        for key in ("canonical", "en", "ja"):
            if key in titles and titles[key] is not None and not is_non_empty_str(titles[key]):
                add_issue(issues, "ERROR", f"{base_path}.titles.{key}", "Must be a non-empty string or null.")
        # champs inattendus
        allowed_title_keys = {"canonical", "en", "ja"}
        for k in titles.keys():
            if k not in allowed_title_keys:
                if strict:
                    add_issue(issues, "ERROR", f"{base_path}.titles", f"Unexpected key '{k}'. Allowed: {sorted(allowed_title_keys)}")
                else:
                    add_issue(issues, "WARN", f"{base_path}.titles", f"Unexpected key '{k}'.")

    # synopsis (string ou null)
    if "synopsis" in obj and obj["synopsis"] is not None and not isinstance(obj["synopsis"], str):
        add_issue(issues, "ERROR", f"{base_path}.synopsis", "Must be a string or null.")

    # authors (list[{"name": str, "role": str|null}])
    authors = get_list(obj.get("authors"), f"{base_path}.authors", issues, required=True)
    if authors is not None:
        for i, a in enumerate(authors):
            ap = f"{base_path}.authors[{i}]"
            ad = get_dict(a, ap, issues, required=True)
            if not ad:
                continue
            if not is_non_empty_str(ad.get("name")):
                add_issue(issues, "ERROR", f"{ap}.name", "Must be a non-empty string.")
            if "role" in ad and ad["role"] is not None and not is_non_empty_str(ad["role"]):
                add_issue(issues, "ERROR", f"{ap}.role", "Must be a non-empty string or null.")
            # champs inattendus auteur
            allowed_author_keys = {"name", "role"}
            for k in ad.keys():
                if k not in allowed_author_keys:
                    if strict:
                        add_issue(issues, "ERROR", ap, f"Unexpected key '{k}' in author. Allowed: {sorted(allowed_author_keys)}")
                    else:
                        add_issue(issues, "WARN", ap, f"Unexpected key '{k}' in author.")

    # ratings (average: number|null, rank: int|null)
    ratings = get_dict(obj.get("ratings"), f"{base_path}.ratings", issues, required=True)
    if ratings is not None:
        avg = ratings.get("average")
        if avg is not None:
            if not is_number(avg):
                add_issue(issues, "ERROR", f"{base_path}.ratings.average", "Must be number or null.")
        rank = ratings.get("rank")
        if rank is not None and not is_int(rank):
            add_issue(issues, "ERROR", f"{base_path}.ratings.rank", "Must be int or null.")
        # champs inattendus
        allowed_ratings = {"average", "rank"}
        for k in ratings.keys():
            if k not in allowed_ratings:
                if strict:
                    add_issue(issues, "ERROR", f"{base_path}.ratings", f"Unexpected key '{k}'. Allowed: {sorted(allowed_ratings)}")
                else:
                    add_issue(issues, "WARN", f"{base_path}.ratings", f"Unexpected key '{k}'.")

    # popularity (rank: int|null)
    pop = get_dict(obj.get("popularity"), f"{base_path}.popularity", issues, required=True)
    if pop is not None:
        rank = pop.get("rank")
        if rank is not None and not is_int(rank):
            add_issue(issues, "ERROR", f"{base_path}.popularity.rank", "Must be int or null.")
        allowed_pop = {"rank"}
        for k in pop.keys():
            if k not in allowed_pop:
                if strict:
                    add_issue(issues, "ERROR", f"{base_path}.popularity", f"Unexpected key '{k}'. Allowed: {sorted(allowed_pop)}")
                else:
                    add_issue(issues, "WARN", f"{base_path}.popularity", f"Unexpected key '{k}'.")

    # tags (categories:list[str], genres:list[str])
    tags = get_dict(obj.get("tags"), f"{base_path}.tags", issues, required=True)
    if tags is not None:
        for list_key in ("categories", "genres"):
            if list_key not in tags:
                add_issue(issues, "WARN", f"{base_path}.tags", f"Missing '{list_key}' (recommended).")
                continue
            lst = get_list(tags.get(list_key), f"{base_path}.tags.{list_key}", issues, required=True)
            if lst is None:
                continue
            for j, v in enumerate(lst):
                if not is_non_empty_str(v):
                    add_issue(issues, "ERROR", f"{base_path}.tags.{list_key}[{j}]", "Must be non-empty string.")
        allowed_tags = {"categories", "genres"}
        for k in tags.keys():
            if k not in allowed_tags:
                if strict:
                    add_issue(issues, "ERROR", f"{base_path}.tags", f"Unexpected key '{k}'. Allowed: {sorted(allowed_tags)}")
                else:
                    add_issue(issues, "WARN", f"{base_path}.tags", f"Unexpected key '{k}'.")


def validate_file(file_path: Path, strict: bool, max_items: int) -> Tuple[List[Issue], int]:
    issues: List[Issue] = []
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as exc:
        add_issue(issues, "ERROR", str(file_path), f"JSON parse error: {exc}")
        return issues, 0

    root = validate_root(payload, file_path, issues, strict)
    if not root:
        return issues, 0

    data = get_list(root.get("data"), f"{file_path}.data", issues, required=True)
    if data is None:
        return issues, 0

    n = len(data)
    limit = n if max_items <= 0 else min(n, max_items)
    for i in range(limit):
        validate_item(data[i], f"{file_path}.data[{i}]", issues, strict)

    return issues, n


def iter_files_from_args(paths: Sequence[str], dir_path: Optional[str]) -> List[Path]:
    out: List[Path] = []
    if dir_path:
        d = Path(dir_path)
        if d.exists() and d.is_dir():
            out.extend(sorted([p for p in d.glob("*.json") if p.is_file()]))
    for p in paths:
        out.append(Path(p))
    # dédoublonnage
    uniq: List[Path] = []
    seen = set()
    for p in out:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate JSON fixtures for manga snapshots (RAG-ready).")
    parser.add_argument("files", nargs="*", help="JSON fixture files to validate.")
    parser.add_argument("--dir", help="Directory containing .json fixtures (e.g., tests/fixtures).")
    parser.add_argument("--strict", action="store_true", help="Fail on unexpected keys and missing recommended fields.")
    parser.add_argument("--max-items", type=int, default=0, help="Max items validated per file (0 = all).")
    parser.add_argument("--generate", action="store_true", help="Generate exports JSON via Kitsu API, then validate them.")
    parser.add_argument("--export-dir", default="exports", help="Directory for generated exports (default: exports).")
    parser.add_argument("--run-id", help="Run id (default: horodaté). Génère dans export-dir/runs/<run-id>/")
    parser.add_argument("--trending-limit", type=int, default=20, help="Trending weekly limit (default: 20).")
    parser.add_argument("--publishing-limit", type=int, default=100, help="Top publishing limit (default: 100).")
    parser.add_argument("--publishing-offset", type=int, default=0, help="Top publishing offset (default: 0).")
    parser.add_argument(
        "--rated-limit",
        type=int,
        default=0,
        help='Top rated limit (default: 0 = "all available").',
    )
    parser.add_argument("--rated-offset", type=int, default=0, help="Top rated offset (default: 0).")
    parser.add_argument("--popular-limit", type=int, default=100, help="Most popular limit (default: 100).")
    parser.add_argument("--popular-offset", type=int, default=0, help="Most popular offset (default: 0).")
    parser.add_argument(
        "--force-top-rated",
        action="store_true",
        help="Overwrite existing exports/top_rated.json when rated-limit=0.",
    )
    args = parser.parse_args(argv)

    if args.generate:
        client = KitsuClient()
        service = MangaService(client)
        from .exporter import _run_id_now, _write_latest_marker

        out_base = Path(args.export_dir)
        run_id = args.run_id or _run_id_now()
        out_dir = out_base / "runs" / run_id
        _write_latest_marker(out_base / "runs", run_id)

        generated = export_all(
            service,
            out_dir=out_dir,
            trending_limit=args.trending_limit,
            publishing_limit=args.publishing_limit,
            publishing_offset=args.publishing_offset,
            rated_limit=args.rated_limit,
            rated_offset=args.rated_offset,
            popular_limit=args.popular_limit,
            popular_offset=args.popular_offset,
            force_top_rated=args.force_top_rated,
        )
        files = [p for p in generated.values() if p.exists()]
        missing = [p for p in generated.values() if not p.exists()]
        for p in missing:
            print(f"[WARN] Skipping missing export (likely in-progress): {p}")
    else:
        files = iter_files_from_args(args.files, args.dir)
    if not files:
        print("No JSON files provided. Example: python validate_fixtures.py --dir tests/fixtures")
        return 2

    total_errors = 0
    total_warns = 0

    for fp in files:
        print(f"\n=== {fp} ===")
        if not fp.exists():
            print(f"[ERROR] {fp}: file not found")
            total_errors += 1
            continue

        issues, n_items = validate_file(fp, strict=args.strict, max_items=args.max_items)
        errs = [i for i in issues if i.level == "ERROR"]
        warns = [i for i in issues if i.level == "WARN"]

        total_errors += len(errs)
        total_warns += len(warns)

        print(f"Items: {n_items} (validated: {n_items if args.max_items <= 0 else min(n_items, args.max_items)})")
        if warns:
            print(f"Warnings: {len(warns)}")
            for w in warns[:50]:
                print(str(w))
            if len(warns) > 50:
                print(f"... {len(warns) - 50} more warnings")
        if errs:
            print(f"Errors: {len(errs)}")
            for e in errs[:50]:
                print(str(e))
            if len(errs) > 50:
                print(f"... {len(errs) - 50} more errors")
        if not errs and not warns:
            print("OK ✅ (no issues)")

    print("\n=== Summary ===")
    print(f"Total warnings: {total_warns}")
    print(f"Total errors  : {total_errors}")

    return 1 if total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
