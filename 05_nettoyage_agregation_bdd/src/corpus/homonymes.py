"""Régénère les homonymes Kitsu de la liste D5 — mécaniquement, par provenance.

    uv run python -m corpus.homonymes            # compte ce qui changerait
    uv run python -m corpus.homonymes --ecrire   # réécrit corpus_homonymes_admis.csv

POURQUOI UNE RÉGÉNÉRATION ET NON UNE LECTURE. Les occurrences d'un pseudonyme
dans un texte Kitsu sont des homonymes PAR PROVENANCE : un synopsis Kitsu n'est
pas écrit par un membre de Manga Sanctuary (R5 de la règle du 2026-09-29). Leur
qualification ne demande donc pas de lecture, et elle doit suivre les textes :
quand la part Kitsu change, la liste change, et une entrée qui ne correspond
plus à rien arrêterait le chargement.

CE QUE L'OUTIL NE TOUCHE PAS : les homonymes des critiques (qualifiés à la
lecture), ni la liste de masquage. Les documents Kitsu sont construits par le
même code que le chargeur (`kitsu.documents`), et les occurrences cherchées
dans les mêmes champs que le test de non-fuite : texte, titre, métadonnées.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import psycopg
import typer

from corpus import kitsu, pseudonymes
from corpus.construire import DONNEES_DEFAUT, SQL_KITSU_RATTACHEES

app = typer.Typer(add_completion=False, help=__doc__)


def occurrences_kitsu(
    docs: list[dict], pseudos: dict[str, str]
) -> list[tuple[str, str]]:
    """(doc_key, empreinte) de chaque pseudonyme présent dans un document Kitsu."""
    motifs = {e: pseudonymes.motif(p) for e, p in pseudos.items()}
    trouves = []
    for d in docs:
        champs = (d["doc_text"], d["title"] or "", d["metadata_json"] or "")
        for e, m in motifs.items():
            if any(m.search(c) for c in champs):
                trouves.append((d["doc_key"], e))
    return trouves


def cle_tri(entree: pseudonymes.Entree) -> tuple:
    prefixe, identifiant = entree.doc_key.split(":", 1)
    return (prefixe, int(identifiant), entree.empreinte)


def regenerer(
    homonymes: list[pseudonymes.Entree], trouves: list[tuple[str, str]]
) -> list[pseudonymes.Entree]:
    gardes = [e for e in homonymes if not e.doc_key.startswith("kitsu:")]
    nouveaux = [pseudonymes.Entree(d, e, "provenance_kitsu") for d, e in trouves]
    return sorted(gardes + nouveaux, key=cle_tri)


@app.command()
def principal(
    # noqa: B008 — `typer.Option()` en défaut est l'API de Typer, pas un oubli.
    ecrire: bool = typer.Option(False, "--ecrire", help="Réécrire la liste."),  # noqa: B008
    donnees: Path = typer.Option(DONNEES_DEFAUT, help="Dossier des listes D5."),  # noqa: B008
    run_kitsu: Path = typer.Option(kitsu.RUN_DEFAUT, "--kitsu", help="Run Kitsu."),  # noqa: B008
) -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise typer.BadParameter("DATABASE_URL n'est pas définie.")
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as cx:
        pseudos = {
            pseudonymes.empreinte(p): p
            for (p,) in cx.execute(
                "SELECT DISTINCT review_author FROM manga.ms_reviews_all"
                " WHERE review_author IS NOT NULL"
            )
        }
        rattachees = {k for (k,) in cx.execute(SQL_KITSU_RATTACHEES)}
    docs, _ = kitsu.documents(run_kitsu, rattachees)
    _, homonymes = pseudonymes.lire_listes(donnees)
    avant = {
        (e.doc_key, e.empreinte) for e in homonymes if e.doc_key.startswith("kitsu:")
    }
    trouves = occurrences_kitsu(docs, pseudos)
    apres = set(trouves)
    typer.echo(
        f"homonymes Kitsu : {len(avant)} → {len(apres)} "
        f"(+{len(apres - avant)} nouveaux, −{len(avant - apres)} disparus, "
        f"{len(avant & apres)} inchangés) sur {len(docs)} documents"
    )
    if ecrire:
        chemin = donnees / pseudonymes.FICHIER_HOMONYMES
        with chemin.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["doc_key", "empreinte_pseudo", "motif"])
            for e in regenerer(homonymes, trouves):
                w.writerow([e.doc_key, e.empreinte, e.valeur])
        typer.echo(f"écrit : {chemin}")


if __name__ == "__main__":
    sys.exit(app())
