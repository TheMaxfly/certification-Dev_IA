"""Contrôle de couverture sur les têtes du catalogue — le rappel là où il se voit.

    uv run python -m identity.controle_tetes          # lecture seule ; 1 si échec

Les cinquante séries les plus populaires du catalogue (`series_popularity_rank`
croissant, départage par `series_id`) ont un `kitsu_id`, ou une raison
documentée de ne pas en avoir, dans `database/donnees/tetes_sans_kitsu_id.csv`.

MOTIF (2026-09-30). Le bloc 1 avait prouvé la PRÉCISION de la cascade — 100
décisions arbitrées, 100 confirmées —, jamais son RAPPEL là où l'échec est le
plus visible : One Piece, Naruto, Death Note, Monster étaient identifiées sans
kitsu_id, 19 des 50 têtes. Ce contrôle est permanent pour que cela se voie.

Une raison documentée devient PÉRIMÉE quand la série a trouvé son kitsu_id ou
a quitté les têtes : le contrôle échoue aussi, pour que le fichier reste vrai.
"""

from __future__ import annotations

import csv
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import typer

TETES = 50
EXCEPTIONS = (
    Path(__file__).resolve().parents[3]
    / "database"
    / "donnees"
    / "tetes_sans_kitsu_id.csv"
)

SQL_TETES = """
SELECT s.series_id, s.series_title, s.series_popularity_rank, w.kitsu_id
FROM manga.ms_series_enriched s
LEFT JOIN manga.work_identity w ON w.series_id = s.series_id
WHERE s.series_popularity_rank IS NOT NULL
ORDER BY s.series_popularity_rank, s.series_id
LIMIT %(n)s
"""

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurControle(Exception):
    """Erreur attendue : message lisible, pas de trace."""


@dataclass
class Bilan:
    tetes: list[tuple[int, str, int, str | None]] = field(default_factory=list)
    documentees: dict[int, str] = field(default_factory=dict)
    non_documentees: list[tuple[int, str, int]] = field(default_factory=list)
    perimees: dict[int, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.non_documentees and not self.perimees


def lire_exceptions(chemin: Path = EXCEPTIONS) -> dict[int, str]:
    """series_id → raison. Une raison vide ou une série en double : refus."""
    exceptions: dict[int, str] = {}
    with chemin.open(encoding="utf-8", newline="") as fichier:
        for ligne in csv.DictReader(fichier):
            series_id = int(ligne["series_id"])
            raison = (ligne.get("raison") or "").strip()
            if not raison:
                raise ErreurControle(f"série {series_id} : raison vide")
            if series_id in exceptions:
                raise ErreurControle(f"série {series_id} documentée deux fois")
            exceptions[series_id] = raison
    return exceptions


def controler(
    cx: psycopg.Connection, exceptions: dict[int, str], n: int = TETES
) -> Bilan:
    bilan = Bilan(tetes=cx.execute(SQL_TETES, {"n": n}).fetchall())
    sans_kitsu = {s for s, _, _, k in bilan.tetes if k is None}
    for series_id, titre, rang, kitsu_id in bilan.tetes:
        if kitsu_id is not None:
            continue
        if series_id in exceptions:
            bilan.documentees[series_id] = exceptions[series_id]
        else:
            bilan.non_documentees.append((series_id, titre, rang))
    bilan.perimees = {
        s: raison for s, raison in exceptions.items() if s not in sans_kitsu
    }
    return bilan


def resume(bilan: Bilan) -> str:
    avec = sum(k is not None for *_, k in bilan.tetes)
    lignes = [
        f"Têtes du catalogue : {len(bilan.tetes)} · avec kitsu_id : {avec} · "
        f"documentées : {len(bilan.documentees)} · "
        f"non documentées : {len(bilan.non_documentees)} · "
        f"raisons périmées : {len(bilan.perimees)}"
    ]
    lignes += [
        f"  ✗ rang {rang} — {titre} ({s}) : ni kitsu_id, ni raison documentée"
        for s, titre, rang in bilan.non_documentees
    ]
    lignes += [
        f"  ✗ raison périmée pour la série {s} (kitsu_id trouvé ou hors des "
        f"têtes) : {raison}"
        for s, raison in bilan.perimees.items()
    ]
    return "\n".join(lignes)


@app.command()
def principal(n: int = typer.Option(TETES, help="Nombre de têtes.")) -> None:  # noqa: B008
    """Contrôle les têtes du catalogue, en session lecture seule."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurControle("DATABASE_URL n'est pas définie.")
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as cx:
        bilan = controler(cx, lire_exceptions(), n)
    typer.echo(resume(bilan))
    if not bilan.ok:
        raise ErreurControle("couverture des têtes en défaut")


def main() -> int:
    try:
        app()
    except (ErreurControle, OSError, ValueError, KeyError) as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
