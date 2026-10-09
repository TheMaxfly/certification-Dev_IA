"""Les œuvres Kitsu du corpus les plus populaires, vraiment absentes du catalogue.

    uv run python -m evaluation.hors_catalogue [--n 20]

Aide à écrire les questions « reconnue hors catalogue » : une requête sur la
VÉRITÉ TERRAIN (le corpus et le catalogue), pas sur la récupération — la règle
du §4 le permet. Candidates : les documents Kitsu du corpus qu'aucune série ne
rattache, par popularité Kitsu. Chacune passe la vérification que fera
`confirmer` (`catalogue.verifier_absence`) : un titre Kitsu ou un identifiant
qui mène au catalogue l'écarte ; un même auteur au catalogue, ou un auteur
inconnu, est signalé. C'est à la personne qui écrit de juger les signalements.

Seul outil de `evaluation` qui lise `bench` — pour la popularité des documents.
La vérification elle-même ne lit que `manga`. Lecture seule.
"""

from __future__ import annotations

import os
import sys
from collections import Counter

import psycopg
import typer

from evaluation import catalogue as cat
from evaluation.atteignabilite import corpus_lu

SQL_CANDIDATES = """
SELECT d.kitsu_id, d.title, d.metadata_json ->> 'subtype',
       (d.metadata_json ->> 'popularity_rank')::int
FROM bench.corpus_docs d
WHERE d.corpus_id = %(corpus)s AND d.source = 'kitsu_synopsis'
  AND NOT EXISTS (SELECT 1 FROM manga.work_identity w
                  WHERE w.kitsu_id = d.kitsu_id::text AND w.series_id IS NOT NULL)
ORDER BY (d.metadata_json ->> 'popularity_rank')::int NULLS LAST, d.kitsu_id
"""

app = typer.Typer(add_completion=False, help=__doc__)


def lister(cx: psycopg.Connection, n: int) -> tuple[list[tuple], Counter]:
    """Les n premières candidates sans bloquant, et ce qui a été écarté."""
    catalogue = cat.Catalogue.charger(cx)
    retenues: list[tuple] = []
    ecartees: Counter = Counter()
    for kitsu_id, titre, sous_type, rang in cx.execute(
        SQL_CANDIDATES, {"corpus": corpus_lu(cx)}
    ).fetchall():
        bloquants, signalements = cat.verifier_absence(cx, catalogue, [kitsu_id])
        if bloquants:
            ecartees[
                "titre" if bloquants[0].startswith("titre") else "identifiant"
            ] += 1
            continue
        retenues.append((rang, kitsu_id, titre, sous_type, signalements))
        if len(retenues) == n:
            break
    return retenues, ecartees


@app.command()
def principal(n: int = 20) -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise typer.BadParameter("DATABASE_URL n'est pas définie.")
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as cx:
        retenues, ecartees = lister(cx, n)
    typer.echo(
        f"écartées en chemin : {ecartees['titre']} par un titre Kitsu,"
        f" {ecartees['identifiant']} par identifiant"
    )
    for rang, kitsu_id, titre, sous_type, signalements in retenues:
        typer.echo(f"{rang:>5}  kitsu {kitsu_id:<6} {sous_type:<7} {titre}")
        for s in signalements:
            typer.echo(f"         ⚠ {s}")


if __name__ == "__main__":
    sys.exit(app())
