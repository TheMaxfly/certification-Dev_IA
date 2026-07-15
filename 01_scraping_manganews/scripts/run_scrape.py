#!/usr/bin/env python3
"""Lance ou reprend un crawl sans écraser le dernier export valide."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]

DATASETS = {
    "series": {
        "spider": "manganews_series",
        "target": Path("data/enriched/manganews_series.jsonl"),
        "item_key": "url",
        "min_items": 10_000,
        "smoke_args": [
            "-a",
            "detail_url=https://www.manga-news.com/index.php/serie/Manga",
        ],
    },
    "populaires": {
        "spider": "manganews_populaires",
        "target": Path("data/enriched/populaires.jsonl"),
        "item_key": "serie_url",
        "min_items": 40,
        "smoke_args": [],
    },
}


def count_non_empty_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def resolve_path(raw_path: str | Path) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path.resolve()


def resolve_target(raw_target: str | None, default: Path) -> Path:
    return resolve_path(raw_target or default)


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Fichier d'état illisible: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Objet JSON attendu dans {path}")
    return data


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def update_run_state(run_dir: Path, metadata: dict, state: str, **details) -> None:
    metadata.update({"state": state, **details})
    write_json(run_dir / "run.json", metadata)


def create_run_dir(dataset: str, raw_run_dir: str | None) -> Path:
    if raw_run_dir:
        run_dir = resolve_path(raw_run_dir)
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    runs_dir = PROJECT_DIR / "data" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(tempfile.mkdtemp(prefix=f"{dataset}-{timestamp}-", dir=runs_dir))


def resolve_job_dir(run_dir: Path, resumed: bool) -> Path:
    """Utilise une file neuve à chaque reprise pour éviter un JOBDIR orphelin."""
    if not resumed:
        return run_dir / "job"

    attempt = 1
    while True:
        candidate = run_dir / f"job-resume-{attempt:03d}"
        if not candidate.exists():
            return candidate
        attempt += 1


def validate_and_deduplicate_jsonl(
    source: Path, destination: Path, item_key: str
) -> tuple[int, int]:
    """Valide le JSONL et conserve la dernière occurrence de chaque URL."""
    records: dict[str, dict] = {}
    input_count = 0

    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            input_count += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"JSON invalide ligne {line_number} dans {source}: {exc}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(
                    f"Objet JSON attendu ligne {line_number} dans {source}"
                )
            key = record.get(item_key)
            if not isinstance(key, str) or not key.strip():
                raise ValueError(
                    f"Clé {item_key!r} manquante ligne {line_number} dans {source}"
                )
            records[key] = record

    with destination.open("w", encoding="utf-8") as stream:
        for record in records.values():
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    return len(records), input_count - len(records)


def prepare_full_run(
    args: argparse.Namespace, config: dict
) -> tuple[Path, Path, bool, dict]:
    if args.resume:
        run_dir = resolve_path(args.resume)
        metadata = load_json(run_dir / "run.json")
        if metadata.get("dataset") != args.dataset:
            raise ValueError(
                "Le dataset demandé ne correspond pas au dossier de reprise: "
                f"{metadata.get('dataset')!r}"
            )
        target = Path(metadata["target"]).resolve()
        if args.output and resolve_target(args.output, config["target"]) != target:
            raise ValueError("--output ne peut pas changer lors d'une reprise")
        if not (run_dir / "items.jsonl").exists():
            raise ValueError(f"Export partiel absent dans {run_dir}")
        return run_dir, target, True, metadata

    target = resolve_target(args.output, config["target"])
    run_dir = create_run_dir(args.dataset, args.run_dir)
    metadata = {
        "dataset": args.dataset,
        "target": str(target),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state": "running",
    }
    write_json(run_dir / "run.json", metadata)
    return run_dir, target, False, metadata


def required_item_count(args: argparse.Namespace, config: dict, target: Path) -> int:
    if args.smoke:
        return 1
    if args.min_items is not None:
        return args.min_items

    # Refuser par défaut une régression de plus de 5 % face au dernier snapshot.
    baseline = count_non_empty_lines(target)
    baseline_floor = int(baseline * 0.95)
    return max(config["min_items"], baseline_floor)


def run(args: argparse.Namespace) -> int:
    config = DATASETS[args.dataset]
    ephemeral_run = args.smoke

    if args.smoke:
        if args.resume or args.run_dir:
            print(
                "ECHEC: --smoke est incompatible avec --resume/--run-dir.",
                file=sys.stderr,
            )
            return 2
        run_dir = Path(tempfile.mkdtemp(prefix=f"manganews-{args.dataset}-smoke-"))
        target = resolve_target(args.output, config["target"])
        resumed = False
        metadata = {}
    else:
        try:
            run_dir, target, resumed, metadata = prepare_full_run(args, config)
        except (KeyError, OSError, ValueError) as exc:
            print(f"ECHEC: {exc}", file=sys.stderr)
            return 2

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = run_dir / "items.jsonl"
    job_dir = resolve_job_dir(run_dir, resumed)
    status_path = run_dir / "status.json"
    status_path.unlink(missing_ok=True)

    if resumed:
        metadata.update(
            {
                "state": "running",
                "resumed_at": datetime.now(timezone.utc).isoformat(),
                "resume_job": str(job_dir),
            }
        )
        write_json(run_dir / "run.json", metadata)

    required_items = required_item_count(args, config, target)
    command = [
        sys.executable,
        "-m",
        "scrapy",
        "crawl",
        config["spider"],
        "-s",
        f"LOG_LEVEL={args.log_level}",
        "-s",
        f"RUN_STATUS_PATH={status_path}",
    ]
    if not args.smoke:
        command.extend(["-s", f"JOBDIR={job_dir}"])
    if resumed and args.dataset == "series":
        command.extend(["-a", f"existing_items_file={staging}"])
    command.extend(["-o" if resumed else "-O", str(staging)])

    if args.smoke:
        command.extend(config["smoke_args"])
        command.extend(["-s", "CLOSESPIDER_ITEMCOUNT=1"])

    print(f"Dossier du crawl: {run_dir}", flush=True)
    print("Commande:", " ".join(command), flush=True)
    try:
        completed = subprocess.run(command, cwd=PROJECT_DIR, check=False)
    except KeyboardInterrupt:
        if not ephemeral_run:
            update_run_state(
                run_dir,
                metadata,
                "interrupted",
                interrupted_at=datetime.now(timezone.utc).isoformat(),
                item_count=count_non_empty_lines(staging),
            )
        print(
            "\nCrawl interrompu. Reprise possible avec:\n"
            f"  {sys.executable} scripts/run_scrape.py --dataset {args.dataset} "
            f"--resume {run_dir}",
            file=sys.stderr,
        )
        return 130

    try:
        status = load_json(status_path)
    except ValueError as exc:
        status = {"reason": None}
        status_error = str(exc)
    else:
        status_error = None

    finish_reason = status.get("reason")
    allowed_reasons = {"finished"}
    if args.smoke:
        allowed_reasons.add("closespider_itemcount")

    if completed.returncode != 0 or finish_reason not in allowed_reasons:
        message = (
            "ECHEC: export non promu "
            f"(code={completed.returncode}, fin={finish_reason!r})"
        )
        if status_error:
            message += f"; {status_error}"
        print(message + ".", file=sys.stderr)
        if ephemeral_run:
            shutil.rmtree(run_dir, ignore_errors=True)
        else:
            update_run_state(
                run_dir,
                metadata,
                "failed",
                failed_at=datetime.now(timezone.utc).isoformat(),
                finish_reason=finish_reason,
                item_count=count_non_empty_lines(staging),
            )
            print(f"Données partielles conservées: {run_dir}", file=sys.stderr)
            print(
                "Reprise: "
                f"{sys.executable} scripts/run_scrape.py --dataset {args.dataset} "
                f"--resume {run_dir}",
                file=sys.stderr,
            )
        if target.exists():
            print(f"Dernier export conservé: {target}", file=sys.stderr)
        return 1

    descriptor, validated_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".validated.jsonl", dir=target.parent
    )
    os.close(descriptor)
    validated = Path(validated_name)
    try:
        item_count, duplicate_count = validate_and_deduplicate_jsonl(
            staging, validated, config["item_key"]
        )
    except (OSError, ValueError) as exc:
        validated.unlink(missing_ok=True)
        print(f"ECHEC: export non promu: {exc}", file=sys.stderr)
        if ephemeral_run:
            shutil.rmtree(run_dir, ignore_errors=True)
        else:
            update_run_state(
                run_dir,
                metadata,
                "invalid",
                failed_at=datetime.now(timezone.utc).isoformat(),
                validation_error=str(exc),
            )
        return 1

    if item_count < required_items:
        validated.unlink(missing_ok=True)
        print(
            "ECHEC: export non promu "
            f"(lignes uniques={item_count}, minimum={required_items}).",
            file=sys.stderr,
        )
        if ephemeral_run:
            shutil.rmtree(run_dir, ignore_errors=True)
        else:
            update_run_state(
                run_dir,
                metadata,
                "incomplete",
                failed_at=datetime.now(timezone.utc).isoformat(),
                item_count=item_count,
                required_items=required_items,
            )
            print(f"Données conservées pour diagnostic: {run_dir}", file=sys.stderr)
        if target.exists():
            print(f"Dernier export conservé: {target}", file=sys.stderr)
        return 1

    if args.smoke:
        validated.unlink(missing_ok=True)
        shutil.rmtree(run_dir, ignore_errors=True)
        print(f"OK: smoke test réussi avec {item_count} élément(s).")
        return 0

    os.replace(validated, target)
    update_run_state(
        run_dir,
        metadata,
        "promoted",
        completed_at=datetime.now(timezone.utc).isoformat(),
        item_count=item_count,
        duplicates_removed=duplicate_count,
    )
    staging.unlink(missing_ok=True)
    shutil.rmtree(job_dir, ignore_errors=True)
    print(
        f"OK: {item_count} lignes promues atomiquement vers {target} "
        f"({duplicate_count} doublon(s) retiré(s))."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Crawl Manga-News reprenable avec validation avant remplacement "
            "de l'export précédent."
        )
    )
    parser.add_argument("--dataset", choices=DATASETS, required=True)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Teste un échantillon sans modifier l'export canonique.",
    )
    run_group = parser.add_mutually_exclusive_group()
    run_group.add_argument(
        "--run-dir",
        help="Dossier neuf où conserver l'état reprenable du crawl.",
    )
    run_group.add_argument(
        "--resume",
        help="Dossier d'un crawl interrompu à reprendre.",
    )
    parser.add_argument(
        "--output",
        help="Chemin de destination du snapshot validé.",
    )
    parser.add_argument(
        "--min-items",
        type=int,
        help="Seuil de lignes uniques requis avant promotion.",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
