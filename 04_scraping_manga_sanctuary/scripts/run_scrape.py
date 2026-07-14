#!/usr/bin/env python3
"""Lance ou reprend le crawl Manga Sanctuary sans écraser le dernier export valide.

Même pattern d'exécution que `01_scraping_manganews/scripts/run_scrape.py` :
export dans un dossier de run, validation, puis promotion atomique. Deux
différences tiennent à la structure du module :

- un **seul** spider produit **deux** flux d'items (VolumeItem, ReviewItem) ;
  les deux sont donc exportés et promus ensemble, ou pas du tout ;
- `scrapy.cfg` vit dans `manga_sanctuary/`, pas à la racine du module : c'est
  de là que la commande scrapy est lancée.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRAPY_DIR = PROJECT_DIR / "manga_sanctuary"
RAW_DIR = PROJECT_DIR / "data" / "raw"
SPIDER = "manga_sanctuary_volumes"
MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")

# Le spider émet deux types d'items sur le même crawl : chacun a son fichier,
# sa clé d'unicité et son plancher. Les minimums sont des garde-fous absolus ;
# le plancher réel est calculé face au dernier snapshot (cf. required_counts).
DATASETS = {
    "volumes": {
        "item_class": "manga_sanctuary.items.VolumeItem",
        "filename": "manga_sanctuary_volumes.jsonl",
        "item_key": "volume_url",
        "min_items": 80_000,
    },
    "reviews": {
        "item_class": "manga_sanctuary.items.ReviewItem",
        "filename": "manga_sanctuary_reviews.jsonl",
        "item_key": "review_url",
        "min_items": 6_000,
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


def create_run_dir(month: str, raw_run_dir: str | None) -> Path:
    if raw_run_dir:
        run_dir = resolve_path(raw_run_dir)
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    runs_dir = PROJECT_DIR / "data" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(tempfile.mkdtemp(prefix=f"{month}-{timestamp}-", dir=runs_dir))


def resolve_job_dir(run_dir: Path) -> Path:
    """Un JOBDIR par run, réutilisé par toutes ses reprises.

    C'est `requests.seen`, à l'intérieur, qui fait la reprise : sans lui,
    `start_requests` repartirait des 27 pages d'index et re-crawlerait ce qui
    est déjà collecté (mesuré : 17 URLs redemandées sur 66 lors d'une reprise
    à JOBDIR neuf). Le module 01 donne, lui, un dossier neuf à chaque reprise,
    mais son spider sait sauter les items déjà exportés (`existing_items_file`) ;
    celui-ci n'a pas cet équivalent, la file persistée est donc sa seule mémoire.

    Le cloisonnement par run suffit à écarter le risque du JOBDIR global : un
    nouveau run reçoit un dossier vide, donc un vrai rafraîchissement complet.
    """
    return run_dir / "job"


def previous_snapshot(month: str, filename: str) -> Path | None:
    """Dernier mois collecté avant `month`, s'il existe."""
    if not RAW_DIR.is_dir():
        return None
    candidates = sorted(
        directory
        for directory in RAW_DIR.iterdir()
        if directory.is_dir()
        and MONTH_PATTERN.match(directory.name)
        and directory.name < month
        and (directory / filename).exists()
    )
    return candidates[-1] / filename if candidates else None


def required_counts(month: str, overrides: dict[str, int | None]) -> dict[str, int]:
    """Plancher par dataset : le max entre le garde-fou absolu et 95 % du
    dernier snapshot mensuel. Une collecte qui perdrait plus de 5 % du corpus
    précédent est une régression, pas un rafraîchissement."""
    required = {}
    for dataset, config in DATASETS.items():
        override = overrides.get(dataset)
        if override is not None:
            required[dataset] = override
            continue
        snapshot = previous_snapshot(month, config["filename"])
        baseline_floor = int(count_non_empty_lines(snapshot) * 0.95) if snapshot else 0
        required[dataset] = max(config["min_items"], baseline_floor)
    return required


def build_feeds(destinations: dict[str, Path], resumed: bool) -> dict:
    """Route chaque type d'item vers son fichier de staging."""
    return {
        str(destinations[dataset]): {
            "format": "jsonlines",
            "encoding": "utf8",
            "store_empty": False,
            "overwrite": not resumed,
            "item_export_kwargs": {"ensure_ascii": False},
            "item_classes": [config["item_class"]],
        }
        for dataset, config in DATASETS.items()
    }


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


def staging_paths(run_dir: Path) -> dict[str, Path]:
    return {dataset: run_dir / f"{dataset}.jsonl" for dataset in DATASETS}


def prepare_full_run(args: argparse.Namespace) -> tuple[Path, str, bool, dict]:
    if args.resume:
        run_dir = resolve_path(args.resume)
        metadata = load_json(run_dir / "run.json")
        month = metadata.get("month")
        if not isinstance(month, str) or not MONTH_PATTERN.match(month):
            raise ValueError(f"Mois absent ou invalide dans {run_dir / 'run.json'}")
        if args.month and args.month != month:
            raise ValueError("--month ne peut pas changer lors d'une reprise")
        if not any(path.exists() for path in staging_paths(run_dir).values()):
            raise ValueError(f"Export partiel absent dans {run_dir}")
        return run_dir, month, True, metadata

    month = args.month or datetime.now(UTC).strftime("%Y-%m")
    run_dir = create_run_dir(month, args.run_dir)
    metadata = {
        "month": month,
        "target_dir": str(RAW_DIR / month),
        "created_at": datetime.now(UTC).isoformat(),
        "state": "running",
    }
    write_json(run_dir / "run.json", metadata)
    return run_dir, month, False, metadata


def promote(
    run_dir: Path, target_dir: Path, required: dict[str, int]
) -> tuple[dict[str, tuple[int, int]], str | None]:
    """Valide les deux exports puis les remplace atomiquement. Aucun fichier
    n'est promu tant que les deux n'ont pas passé validation et plancher : un
    couple volumes/reviews désynchronisé serait pire qu'un crawl à refaire."""
    staging = staging_paths(run_dir)
    validated: dict[str, Path] = {}
    results: dict[str, tuple[int, int]] = {}
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        for dataset, config in DATASETS.items():
            source = staging[dataset]
            if not source.exists():
                return results, f"export {dataset} absent du dossier de run"

            final = target_dir / config["filename"]
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{final.name}.", suffix=".validated.jsonl", dir=target_dir
            )
            os.close(descriptor)
            validated[dataset] = Path(temporary_name)

            try:
                item_count, duplicate_count = validate_and_deduplicate_jsonl(
                    source, validated[dataset], config["item_key"]
                )
            except (OSError, ValueError) as exc:
                return results, str(exc)

            if item_count < required[dataset]:
                return results, (
                    f"{dataset}: lignes uniques={item_count}, "
                    f"minimum={required[dataset]}"
                )
            results[dataset] = (item_count, duplicate_count)

        for dataset, config in DATASETS.items():
            os.replace(validated.pop(dataset), target_dir / config["filename"])
    finally:
        for leftover in validated.values():
            leftover.unlink(missing_ok=True)

    return results, None


def run(args: argparse.Namespace) -> int:
    ephemeral_run = args.smoke

    if args.smoke:
        if args.resume or args.run_dir:
            print(
                "ECHEC: --smoke est incompatible avec --resume/--run-dir.",
                file=sys.stderr,
            )
            return 2
        run_dir = Path(tempfile.mkdtemp(prefix="manga-sanctuary-smoke-"))
        month = args.month or datetime.now(UTC).strftime("%Y-%m")
        resumed = False
        metadata = {}
    else:
        try:
            run_dir, month, resumed, metadata = prepare_full_run(args)
        except (KeyError, OSError, ValueError) as exc:
            print(f"ECHEC: {exc}", file=sys.stderr)
            return 2

    target_dir = RAW_DIR / month
    staging = staging_paths(run_dir)
    job_dir = resolve_job_dir(run_dir)
    status_path = run_dir / "status.json"
    status_path.unlink(missing_ok=True)

    if resumed:
        if not job_dir.exists():
            print(
                f"ECHEC: aucun JOBDIR dans {run_dir} — la file de reprise est "
                "absente, une reprise repartirait des pages d'index et "
                "re-crawlerait ce qui est déjà collecté.",
                file=sys.stderr,
            )
            return 2
        metadata.update(
            {
                "state": "running",
                "resumed_at": datetime.now(UTC).isoformat(),
                "resume_job": str(job_dir),
            }
        )
        write_json(run_dir / "run.json", metadata)

    required = required_counts(
        month, {"volumes": args.min_volumes, "reviews": args.min_reviews}
    )
    command = [
        sys.executable,
        "-m",
        "scrapy",
        "crawl",
        SPIDER,
        "-s",
        f"LOG_LEVEL={args.log_level}",
        "-s",
        f"RUN_STATUS_PATH={status_path}",
        "-s",
        f"JOBDIR={job_dir}",
        "-s",
        f"FEEDS={json.dumps(build_feeds(staging, resumed))}",
    ]
    if args.smoke:
        command.extend(["-s", f"CLOSESPIDER_ITEMCOUNT={args.smoke}"])

    resume_hint = f"  {sys.executable} scripts/run_scrape.py --resume {run_dir}"
    print(f"Dossier du crawl: {run_dir}", flush=True)
    print(f"Cible: {target_dir}", flush=True)
    print("Commande:", " ".join(command), flush=True)
    try:
        completed = subprocess.run(command, cwd=SCRAPY_DIR, check=False)
    except KeyboardInterrupt:
        if not ephemeral_run:
            update_run_state(
                run_dir,
                metadata,
                "interrupted",
                interrupted_at=datetime.now(UTC).isoformat(),
                item_counts={
                    dataset: count_non_empty_lines(path)
                    for dataset, path in staging.items()
                },
            )
        print(
            f"\nCrawl interrompu. Reprise possible avec:\n{resume_hint}",
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
        # Un Ctrl-C laisse Scrapy fermer proprement (reason « shutdown ») : rien
        # n'est promu, mais ce n'est pas une panne — l'état doit le dire, sans
        # quoi run.json ferait passer un arrêt volontaire pour un incident.
        interrupted = finish_reason == "shutdown"
        message = (
            "ARRET: crawl interrompu, export non promu "
            if interrupted
            else "ECHEC: export non promu "
        ) + f"(code={completed.returncode}, fin={finish_reason!r})"
        if status_error:
            message += f"; {status_error}"
        print(message + ".", file=sys.stderr)
        if isinstance(finish_reason, str) and finish_reason.startswith(
            "manga_sanctuary_access_blocked"
        ):
            print(
                "Le site a refusé l'accès. Ne pas relancer immédiatement et ne "
                "rien contourner : vérifier l'autorisation et la politesse "
                "(UA, AUTOTHROTTLE) avant toute reprise.",
                file=sys.stderr,
            )
        if ephemeral_run:
            shutil.rmtree(run_dir, ignore_errors=True)
        else:
            update_run_state(
                run_dir,
                metadata,
                "interrupted" if interrupted else "failed",
                stopped_at=datetime.now(UTC).isoformat(),
                finish_reason=finish_reason,
                item_counts={
                    dataset: count_non_empty_lines(path)
                    for dataset, path in staging.items()
                },
            )
            print(f"Données partielles conservées: {run_dir}", file=sys.stderr)
            print(f"Reprise:\n{resume_hint}", file=sys.stderr)
        return 1

    if args.smoke:
        counts = {
            dataset: count_non_empty_lines(path) for dataset, path in staging.items()
        }
        shutil.rmtree(run_dir, ignore_errors=True)
        print(f"OK: smoke test réussi ({counts}), aucun export promu.")
        return 0

    results, error = promote(run_dir, target_dir, required)
    if error is not None:
        print(f"ECHEC: export non promu: {error}.", file=sys.stderr)
        update_run_state(
            run_dir,
            metadata,
            "invalid",
            failed_at=datetime.now(UTC).isoformat(),
            validation_error=error,
        )
        print(f"Données conservées pour diagnostic: {run_dir}", file=sys.stderr)
        if target_dir.exists():
            print(f"Dernier export conservé: {target_dir}", file=sys.stderr)
        return 1

    update_run_state(
        run_dir,
        metadata,
        "promoted",
        completed_at=datetime.now(UTC).isoformat(),
        item_counts={dataset: count for dataset, (count, _) in results.items()},
        duplicates_removed={
            dataset: duplicates for dataset, (_, duplicates) in results.items()
        },
    )
    for path in staging.values():
        path.unlink(missing_ok=True)
    shutil.rmtree(job_dir, ignore_errors=True)
    for dataset, (item_count, duplicate_count) in results.items():
        print(
            f"OK: {item_count} lignes promues vers "
            f"{target_dir / DATASETS[dataset]['filename']} "
            f"({duplicate_count} doublon(s) retiré(s))."
        )
    return 0


def month_argument(value: str) -> str:
    if not MONTH_PATTERN.match(value):
        raise argparse.ArgumentTypeError(f"Mois attendu au format AAAA-MM: {value!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Crawl Manga Sanctuary reprenable, avec validation avant "
            "remplacement du snapshot mensuel."
        )
    )
    parser.add_argument(
        "--month",
        type=month_argument,
        help="Snapshot cible sous data/raw/<AAAA-MM>/ (défaut: mois courant UTC).",
    )
    parser.add_argument(
        "--smoke",
        type=int,
        nargs="?",
        const=5,
        default=0,
        metavar="N",
        help="Arrête après N items sans rien promouvoir (défaut: 5).",
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
        "--min-volumes",
        type=int,
        help="Seuil de volumes uniques requis avant promotion.",
    )
    parser.add_argument(
        "--min-reviews",
        type=int,
        help="Seuil de critiques uniques requises avant promotion.",
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
