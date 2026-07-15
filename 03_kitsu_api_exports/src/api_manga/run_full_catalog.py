"""CLI de collecte exhaustive et reprenable du catalogue manga Kitsu."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .client import KitsuClient
from .full_catalog import (
    DEFAULT_RELATIONS,
    FullCatalogCollector,
    parse_ids_file,
    parse_relations,
    run_id_now,
)
from .validate_fixtures import validate_file


def _latest_marker(root: Path) -> Path:
    return root / "LATEST"


def _read_latest(root: Path) -> str | None:
    marker = _latest_marker(root)
    if not marker.exists():
        return None
    value = marker.read_text(encoding="utf-8").strip()
    return value or None


def _write_latest(root: Path, run_id: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    temporary = _latest_marker(root).with_suffix(".tmp")
    temporary.write_text(run_id + "\n", encoding="utf-8")
    temporary.replace(_latest_marker(root))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collecte exhaustive Kitsu: catalogue brut, top rated complet et "
            "relations mappings/staff/personnages/chapitres."
        )
    )
    parser.add_argument("--out-dir", default="exports")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reprendre le run LATEST ou le --run-id indiqué (défaut: oui).",
    )
    parser.add_argument(
        "--relations",
        default=",".join(DEFAULT_RELATIONS),
        help=(
            "Liste parmi mappings,staff,characters,chapters ou 'all'. "
            "Les chapitres sont exclus par défaut car très volumineux."
        ),
    )
    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help="Collecter le catalogue sans lancer les relations.",
    )
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument(
        "--request-interval",
        type=float,
        default=0.5,
        help="Délai minimal entre requêtes en secondes (défaut: 0.5 = 2 req/s).",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument(
        "--max-catalog-pages",
        type=int,
        default=0,
        help="Limiter les pages catalogue de cette exécution (0 = illimité).",
    )
    parser.add_argument(
        "--max-relation-manga",
        type=int,
        default=0,
        help="Limiter les mangas traités par relation et par exécution.",
    )
    parser.add_argument(
        "--max-relation-pages",
        type=int,
        default=0,
        help="Limiter les pages par relation et par exécution.",
    )
    parser.add_argument(
        "--no-validate-top-rated",
        action="store_true",
        help="Ne pas valider top_rated.json lorsque le catalogue est terminé.",
    )
    parser.add_argument(
        "--ids-file",
        type=Path,
        help=(
            "Restreindre le run à une liste de kitsu_id (un par ligne, '#' et "
            "lignes vides ignorés). Même run_dir, même état : les IDs déjà "
            "couverts sont sautés sans requête."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "N'émettre AUCUNE requête : reconstruire l'état, appliquer le "
            "filtrage, puis afficher le plan (done / restant / requêtes / durée)."
        ),
    )
    return parser


def _apply_targets(
    collector: FullCatalogCollector, targeted_ids: Sequence[str]
) -> dict:
    """Restreint le périmètre et signale les IDs absents du catalogue."""
    report = collector.restrict_targets(targeted_ids)
    unknown = report["unknown"]
    print(
        f"[ciblage] {report['requested']} ID(s) demandé(s) -> "
        f"{report['known']} présent(s) au catalogue"
    )
    if unknown:
        apercu = ", ".join(unknown[:20])
        suite = f" ... (+{len(unknown) - 20})" if len(unknown) > 20 else ""
        print(
            f"[ciblage] AVERTISSEMENT: {len(unknown)} ID(s) inconnu(s) du "
            f"catalogue, ignoré(s): {apercu}{suite}"
        )
    return report


def _dry_run(
    collector: FullCatalogCollector,
    args: argparse.Namespace,
    relations: Sequence[str],
    targeted_ids: Sequence[str] | None,
    run_id: str,
    run_dir: Path,
) -> None:
    """Affiche le plan sans émettre la moindre requête."""
    target_report = None
    if targeted_ids is not None:
        target_report = _apply_targets(collector, targeted_ids)

    plan = collector.plan(relations, request_interval=args.request_interval)
    catalog_state = collector.state["catalog"]

    print(f"\n=== DRY-RUN (aucune requête émise) — run {run_id} ===")
    print(f"  run_dir            : {run_dir}")
    print(
        f"  catalogue          : {plan['catalog_items']} mangas "
        f"(done={catalog_state.get('done')})"
    )
    print(f"  périmètre du run   : {plan['targets']} manga(s)")
    if target_report is not None:
        print(
            f"  ciblage            : {target_report['known']} retenu(s) / "
            f"{target_report['requested']} demandé(s), "
            f"{len(target_report['unknown'])} inconnu(s)"
        )
    if not relations:
        print("  relations          : aucune (--catalog-only)")
    for relation, info in plan["relations"].items():
        minutes = info["estimated_seconds_min"] / 60.0
        print(
            f"  {relation:<11} déjà done {info['targets_done']}/{info['targets']} "
            f"| restant {info['targets_remaining']} "
            f"| >= {info['estimated_requests_min']} req "
            f"(~{minutes:.0f} min à {args.request_interval}s) "
            f"| done catalogue complet {info['done_full_catalog']}"
        )
    print(f"  requêtes émises    : {collector.client.request_count} (attendu: 0)")
    print(json.dumps({"dry_run": True, "run_id": run_id, "plan": plan}, indent=2))


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.max_catalog_pages < 0:
        raise SystemExit("--max-catalog-pages doit être positif ou nul")
    if args.max_relation_manga < 0 or args.max_relation_pages < 0:
        raise SystemExit("Les limites de relations doivent être positives ou nulles")

    try:
        relations = () if args.catalog_only else parse_relations(args.relations)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    targeted_ids: list[str] | None = None
    if args.ids_file is not None:
        if not args.ids_file.exists():
            raise SystemExit(f"Fichier d'IDs introuvable: {args.ids_file}")
        targeted_ids = parse_ids_file(args.ids_file)
        if not targeted_ids:
            raise SystemExit(f"Fichier d'IDs vide: {args.ids_file}")

    output_root = Path(args.out_dir) / "full_catalog"
    run_id = args.run_id
    if run_id is None and args.resume:
        run_id = _read_latest(output_root)
    if run_id is None:
        run_id = run_id_now()
    run_dir = output_root / run_id
    if run_dir.exists() and not args.resume and any(run_dir.iterdir()):
        raise SystemExit(
            f"Le run {run_id} existe déjà; utiliser --resume ou un autre --run-id"
        )
    if not args.dry_run:
        _write_latest(output_root, run_id)

    client = KitsuClient(
        timeout=args.timeout,
        max_retries=args.retries,
        min_interval=args.request_interval,
    )
    collector = FullCatalogCollector(client, run_dir, page_size=args.page_size)
    for note in collector.migration_notes:
        print(f"[migration] {note}")

    if args.dry_run:
        _dry_run(collector, args, relations, targeted_ids, run_id, run_dir)
        return

    collector.collect_catalog(max_pages=args.max_catalog_pages)
    if targeted_ids is not None:
        _apply_targets(collector, targeted_ids)
    if relations:
        collector.collect_relations(
            relations,
            max_manga=args.max_relation_manga,
            max_pages=args.max_relation_pages,
        )
    manifest = collector.finalize(relations)

    top_rated = run_dir / "top_rated.json"
    if (
        top_rated.exists()
        and not args.no_validate_top_rated
        and manifest["catalog"].get("done")
    ):
        issues, item_count = validate_file(top_rated, strict=True, max_items=0)
        errors = [issue for issue in issues if issue.level == "ERROR"]
        if errors:
            for issue in errors[:50]:
                print(issue)
            raise SystemExit(1)
        print(f"top_rated validé: {item_count} entrées")

    print(
        f"[réseau] pages sautées (déjà collectées, 0 requête): "
        f"{collector.pages_skipped} | requêtes réellement émises: "
        f"{client.request_count}"
    )

    summary = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": manifest["status"],
        "request_count": manifest["request_count"],
        "pages_skipped": collector.pages_skipped,
        "requests_emitted": client.request_count,
        "targets": len(collector.target_ids),
        "catalog_items": manifest["catalog"].get("items"),
        "catalog_reported_total": manifest["catalog"].get("reported_total"),
        "catalog_next_offset": manifest["catalog"].get("next_offset"),
        "relations": {
            relation: {
                "manga_completed": state.get("manga_completed"),
                "items": state.get("items"),
                "done": state.get("done"),
            }
            for relation, state in manifest["relations"].items()
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
