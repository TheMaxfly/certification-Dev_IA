from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .client import KitsuClient
from .exporter import (
    _read_latest_marker,
    _run_id_now,
    _write_latest_marker,
    export_top_rated,
)
from .service import MangaService
from .validate_fixtures import validate_file


def _validate_or_raise(path: Path, *, strict: bool, max_items: int) -> None:
    issues, _ = validate_file(path, strict=strict, max_items=max_items)
    errors = [i for i in issues if i.level == "ERROR"]
    for issue in issues:
        print(issue)
    if errors:
        raise SystemExit(1)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Runner: export top rated (optionally all) to JSON."
    )
    parser.add_argument(
        "--out-dir", default="exports", help="Output directory (default: exports)."
    )
    parser.add_argument(
        "--run-id",
        help="Identifiant de run (défaut: reuse LATEST si --resume, sinon horodaté). "
        "Les exports sont écrits dans out-dir/top_rated/<run-id>/",
    )
    parser.add_argument(
        "--rated-limit",
        type=int,
        default=0,
        help='Limit (default: 0 = "all available").',
    )
    parser.add_argument(
        "--rated-offset", type=int, default=0, help="Offset (default: 0)."
    )
    parser.add_argument(
        "--with-authors",
        action="store_true",
        help="Fetch authors (slow for large exports).",
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing top_rated.json."
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resume an in-progress full export (default: enabled).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="For rated-limit=0 only: fetch at most N pages this run (0 = no limit).",
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
    top_rated_root = out_base / "top_rated"

    run_id = args.run_id
    if args.rated_limit <= 0:
        if not run_id and args.resume:
            run_id = _read_latest_marker(top_rated_root)
        if not run_id:
            run_id = _run_id_now()
    else:
        # Pour des exports partiels (top N), on versionne à chaque exécution.
        if not run_id:
            run_id = _run_id_now()
    _write_latest_marker(top_rated_root, run_id)

    run_dir = top_rated_root / run_id

    client = KitsuClient()
    service = MangaService(client)

    out_path = export_top_rated(
        service,
        out_dir=run_dir,
        limit=args.rated_limit,
        offset=args.rated_offset,
        include_authors=args.with_authors,
        force=args.force,
        resume=args.resume,
        max_pages=args.max_pages,
    )
    state_path = run_dir / "top_rated.state.json"
    done = True
    if args.rated_limit <= 0 and state_path.exists():
        try:
            import json

            state = json.loads(state_path.read_text(encoding="utf-8"))
            done = bool(state.get("done") or False)
            written = int(state.get("written") or 0)
            next_offset = int(state.get("next_offset") or 0)
        except Exception:
            done = False
            written = 0
            next_offset = 0

        if done:
            print(f"Export terminé: {out_path} (items: {written})")
        else:
            print(f"Export en cours: items={written}, next_offset={next_offset}")
            print(f"Progress: {state_path} / {run_dir / 'top_rated.ndjson'}")
            print("Relance la commande pour continuer (ou augmente --max-pages).")
    else:
        print(f"Export écrit: {out_path}")

    if not args.no_validate and (args.rated_limit > 0 or done):
        _validate_or_raise(out_path, strict=args.strict, max_items=args.max_items)
    elif not args.no_validate and args.rated_limit <= 0 and not done:
        print("Validation ignorée: export top_rated incomplet.")


if __name__ == "__main__":
    main()
