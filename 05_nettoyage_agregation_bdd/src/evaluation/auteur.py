"""Remonter d'un auteur à ses séries, telles que le catalogue les nomme.

    uv run python -m evaluation.auteur "Yasunobu Yamauchi" ["Autre Nom" …]

POURQUOI CET OUTIL. Une réponse attendue s'écrit telle que le catalogue la
nomme ; quand on ne connaît que le titre anglais ou abrégé, l'auteur est la
voie pour retrouver le titre français — sans jamais chercher avec les mots de
la question (§4 de la spec). La recherche est l'égalité stricte de la
confirmation : `normaliser(nom)` = dessinateur ou scénariste normalisé. Aucune
distance, aucun préfixe : un nom mal écrit ne renvoie rien, et c'est voulu.

Lecture seule, dès la première requête ; ne lit que `manga`.
"""

from __future__ import annotations

import os
import sys

import psycopg
import typer

from evaluation import catalogue as cat

app = typer.Typer(add_completion=False, help=__doc__)


def lister(catalogue: cat.Catalogue, nom: str) -> list[tuple[int, str, str]]:
    """(series_id, titre du catalogue, rôle) des séries de cet auteur."""
    return [
        (i, catalogue.series[i].titre, role)
        for i, role in sorted(
            catalogue.par_auteur(nom).items(),
            key=lambda x: (catalogue.series[x[0]].titre or "", x[0]),
        )
    ]


@app.command()
def principal(noms: list[str]) -> None:
    """Liste les séries du catalogue de chaque auteur donné."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise typer.BadParameter("DATABASE_URL n'est pas définie.")
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as cx:
        catalogue = cat.Catalogue.charger(cx)
    for nom in noms:
        series = lister(catalogue, nom)
        typer.echo(f"\n{nom} — {len(series)} série(s)")
        if not series:
            typer.echo(
                "  aucune : le nom s'écrit comme le catalogue l'écrit (prénom NOM)"
            )
        for series_id, titre, role in series:
            typer.echo(f"  {series_id:>6}  {titre}  ({role})")


if __name__ == "__main__":
    sys.exit(app())
