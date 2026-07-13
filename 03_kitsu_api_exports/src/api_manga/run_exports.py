from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .client import KitsuClient
from .exporter import (
    _run_id_now,
    _write_latest_marker,
    export_most_popular,
    export_top_publishing,
    export_trending_weekly,
)
from .service import MangaService
from .validate_fixtures import validate_file


def _validate_or_raise(paths: list[Path], *, strict: bool, max_items: int) -> None:
    total_errors = 0
    for fp in paths:
        issues, _ = validate_file(fp, strict=strict, max_items=max_items)
        total_errors += sum(1 for i in issues if i.level == "ERROR")
        for issue in issues:
            print(issue)
    if total_errors:
        raise SystemExit(1)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Runner: exports trending/publishing/popular to JSON."
    )
    parser.add_argument(
        "--out-dir", default="exports", help="Output directory (default: exports)."
    )
    parser.add_argument(
        "--run-id",
        help=(
            "Identifiant de run (défaut: horodaté). "
            "Les exports sont écrits dans out-dir/runs/<run-id>/"
        ),
    )
    parser.add_argument(
        "--trending-limit",
        type=int,
        default=20,
        help="Trending weekly limit (default: 20).",
    )
    parser.add_argument(
        "--publishing-limit",
        type=int,
        default=100,
        help="Top publishing limit (default: 100).",
    )
    parser.add_argument(
        "--publishing-offset",
        type=int,
        default=0,
        help="Top publishing offset (default: 0).",
    )
    parser.add_argument(
        "--popular-limit",
        type=int,
        default=100,
        help="Most popular limit (default: 100).",
    )
    parser.add_argument(
        "--popular-offset",
        type=int,
        default=0,
        help="Most popular offset (default: 0).",
    )
    parser.add_argument(
        "--no-authors", action="store_true", help="Skip authors fetch (faster)."
    )
    parser.add_argument(
        "--no-validate", action="store_true", help="Skip fixture validation."
    )
    parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Strict validation (default: enabled).",
    )
    parser.add_argument(
        "--max-items", type=int, default=0, help="Max items validated per file (0=all)."
    )
    args = parser.parse_args(argv)

    out_base = Path(args.out_dir)
    run_id = args.run_id or _run_id_now()
    run_dir = out_base / "runs" / run_id
    _write_latest_marker(out_base / "runs", run_id)

    client = KitsuClient()
    service = MangaService(client)

    include_authors = not args.no_authors

    paths = [
        export_trending_weekly(service, out_dir=run_dir, limit=args.trending_limit),
        export_top_publishing(
            service,
            out_dir=run_dir,
            limit=args.publishing_limit,
            offset=args.publishing_offset,
            include_authors=include_authors,
        ),
        export_most_popular(
            service,
            out_dir=run_dir,
            limit=args.popular_limit,
            offset=args.popular_offset,
            include_authors=include_authors,
        ),
    ]

    print("Exports écrits:")
    print(f"- run_id: {run_id}")
    for p in paths:
        print(f"- {p}")

    if not args.no_validate:
        _validate_or_raise(paths, strict=args.strict, max_items=args.max_items)


if __name__ == "__main__":
    main()
