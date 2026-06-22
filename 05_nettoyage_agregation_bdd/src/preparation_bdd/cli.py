from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import orjson
import pandas as pd
import typer
from rich.console import Console
from rich.pretty import Pretty

from .settings import resolve_path
from .textio import open_text

app = typer.Typer(add_completion=False, help="Exploration rapide de JSON/CSV.")
console = Console()


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with open_text(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            value = orjson.loads(line)
            if isinstance(value, dict):
                yield value
            else:
                yield {"_value": value}


def _load_json(path: Path) -> Any:
    data = path.read_bytes()
    return orjson.loads(data)


@app.command("json")
def explore_json(
    file: str = typer.Argument(..., help="Chemin vers un fichier .json ou .jsonl (support .gz)."),
    limit: int = typer.Option(20, "--limit", "-n", help="Nombre max d'objets à afficher."),
    show: bool = typer.Option(True, "--show/--no-show", help="Afficher un aperçu des données."),
) -> None:
    path = resolve_path(file)
    if not path.exists():
        raise typer.BadParameter(f"Fichier introuvable: {path}")

    type_counter: Counter[str] = Counter()
    key_counter: Counter[str] = Counter()
    shown = 0

    if path.suffixes[-2:] == [".jsonl", ".gz"] or path.suffix == ".jsonl":
        records: list[dict[str, Any]] = []
        record_count = 0
        for record in _iter_jsonl(path):
            record_count += 1
            for k, v in record.items():
                key_counter[k] += 1
                type_counter[f"{k}:{_type_name(v)}"] += 1
            if show and shown < limit:
                records.append(record)
                shown += 1
        console.print(f"[bold]JSONL[/bold] {path} — enregistrements: {record_count}")
        console.print("Champs les plus fréquents:", Pretty(key_counter.most_common(10)))
        console.print(
            "Types (champ:type) les plus fréquents:", Pretty(type_counter.most_common(10))
        )
        if show:
            console.print("Aperçu:", Pretty(records))
        return

    value = _load_json(path)
    console.print(f"[bold]JSON[/bold] {path}")
    if isinstance(value, list):
        for item in value[:limit]:
            if isinstance(item, dict):
                key_counter.update(item.keys())
        console.print("Champs les plus fréquents:", Pretty(key_counter.most_common(10)))
    console.print("Type racine:", _type_name(value))
    if show:
        console.print("Aperçu:", Pretty(value if not isinstance(value, list) else value[:limit]))


@app.command("csv")
def explore_csv(
    file: str = typer.Argument(..., help="Chemin vers un fichier .csv (support .gz)."),
    head: int = typer.Option(10, "--head", "-n", help="Nombre de lignes à afficher."),
    sep: str | None = typer.Option(None, "--sep", help="Séparateur; autodétecté si omis."),
    encoding: str = typer.Option("utf-8", "--encoding"),
) -> None:
    path = resolve_path(file)
    if not path.exists():
        raise typer.BadParameter(f"Fichier introuvable: {path}")

    read_kwargs: dict[str, Any] = {"encoding": encoding}
    if sep is None:
        read_kwargs.update({"sep": None, "engine": "python"})
    else:
        read_kwargs["sep"] = sep
    df = pd.read_csv(path, **read_kwargs)
    console.print(f"[bold]CSV[/bold] {path}")
    console.print("Colonnes:", list(df.columns))
    console.print("Dtypes:", {c: str(t) for c, t in df.dtypes.items()})
    console.print(f"Aperçu (head {head}):")
    console.print(df.head(head).to_string(index=False))
    with pd.option_context("display.float_format", "{:,.4f}".format):
        desc = df.describe(include="all").transpose()
    console.print("describe():")
    console.print(desc.to_string())


@app.callback()
def _main() -> None:
    pass
