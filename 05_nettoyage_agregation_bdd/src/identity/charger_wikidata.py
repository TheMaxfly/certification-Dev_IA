"""Chargement du pivot Wikidata : CSV -> staging -> manga.wd_*.

    uv run python -m identity.charger_wikidata
    uv run python -m identity.charger_wikidata --source chemin/vers/staging

Wikidata est le seul référentiel qui porte à la fois `mal_id` et `anilist_id` :
c'est par lui que les plateformes se rejoignent, faute d'identifiant commun.

`forme_norm` est RECALCULÉE ici par `identity.normaliser()`, alors que les CSV
portent déjà une colonne `forme_normalisee`. Ce n'est pas de la défiance : ces
fichiers ont été produits le 2026-07-14 par une version de la fonction qui n'est
pas forcément celle d'aujourd'hui, et une normalisation figée dans un CSV
vieillit sans prévenir. Recalculer garantit que les deux côtés du rapprochement
(ms_formes et wd_formes) parlent la même langue. L'écart entre la valeur du
fichier et la valeur recalculée est COMPTÉ et rapporté.
"""

from __future__ import annotations

import csv
import os
import sys
import time
from pathlib import Path

import psycopg
import typer

from identity.wikidata_dump import normaliser

csv.field_size_limit(sys.maxsize)

RACINE = Path(__file__).resolve().parents[3]
SOURCE_DEFAUT = RACINE / "05_nettoyage_agregation_bdd/data/staging"

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurChargement(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurChargement(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'"
        )
    return url


def lire_csv(chemin: Path) -> list[dict]:
    """Les CSV Wikidata pèsent 2 Mo au plus : les lire d'un coup est honnête.

    (Le streaming de B2 existait pour un fichier de 315 Mo ; ici il n'ajouterait
    que du bruit.)
    """
    if not chemin.is_file():
        raise ErreurChargement(f"Fichier introuvable : {chemin}")
    with chemin.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def vide_en_none(valeur: str | None) -> str | None:
    if valeur is None:
        return None
    valeur = valeur.strip()
    return valeur or None


def entier_ou_none(valeur: str | None) -> int | None:
    valeur = vide_en_none(valeur)
    if valeur is None:
        return None
    try:
        return int(valeur)
    except ValueError:
        return None


def charger_pivot(connexion, source: Path) -> dict[str, int]:
    """manga.wd_pivot : fusion de wd_pivot.csv et wd_entities.csv sur le qid.

    Les deux fichiers ont le même grain (une ligne par qid) et les mêmes
    mal_id / anilist_id. wd_entities apporte le label, l'année et les liens.
    """
    pivots = lire_csv(source / "wd_pivot.csv")
    entites = lire_csv(source / "wd_entities.csv")
    par_qid = {e["qid"]: e for e in entites}

    lignes = []
    for p in pivots:
        qid = p["qid"].strip()
        e = par_qid.get(qid, {})
        lignes.append(
            (
                qid,
                vide_en_none(e.get("label_principal")),
                entier_ou_none(e.get("annee")),
                # mal_id / anilist_id : wd_pivot fait foi, wd_entities complète.
                vide_en_none(p.get("mal_id")) or vide_en_none(e.get("mal_id")),
                vide_en_none(p.get("anilist_id")) or vide_en_none(e.get("anilist_id")),
                vide_en_none(e.get("ann_id")),
                vide_en_none(e.get("wiki_fr")),
                vide_en_none(e.get("wiki_en")),
            )
        )

    with connexion.cursor() as curseur:
        curseur.executemany(
            "INSERT INTO manga.wd_pivot (qid, label_principal, annee, mal_id, "
            "  anilist_id, ann_id, wiki_fr, wiki_en) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (qid) DO UPDATE SET "
            "  label_principal = EXCLUDED.label_principal, "
            "  annee = EXCLUDED.annee, mal_id = EXCLUDED.mal_id, "
            "  anilist_id = EXCLUDED.anilist_id, ann_id = EXCLUDED.ann_id, "
            "  wiki_fr = EXCLUDED.wiki_fr, wiki_en = EXCLUDED.wiki_en, "
            "  updated_at = now()",
            lignes,
        )
        curseur.execute("SELECT count(*) FROM manga.wd_pivot")
        total = curseur.fetchone()[0]
        curseur.execute(
            "SELECT count(*) FILTER (WHERE mal_id IS NOT NULL), "
            "       count(*) FILTER (WHERE anilist_id IS NOT NULL) "
            "FROM manga.wd_pivot"
        )
        avec_mal, avec_anilist = curseur.fetchone()
    return {
        "lus": len(pivots),
        "entites": len(entites),
        "total": total,
        "avec_mal_id": avec_mal,
        "avec_anilist_id": avec_anilist,
    }


def charger_formes(connexion, source: Path) -> dict:
    """manga.wd_formes, forme_norm RECALCULÉE par normaliser()."""
    brutes = lire_csv(source / "wd_formes.csv")

    lignes = []
    divergences = []
    sans_norm = 0
    orphelines = 0
    with connexion.cursor() as curseur:
        curseur.execute("SELECT qid FROM manga.wd_pivot")
        connus = {q for (q,) in curseur.fetchall()}

    for f in brutes:
        qid = f["qid"].strip()
        # La FK refuserait la ligne : 28 qid de wd_formes n'ont pas d'entité.
        if qid not in connus:
            orphelines += 1
            continue
        forme = (f.get("forme_originale") or "").strip()
        if not forme:
            continue
        recalculee = normaliser(forme)
        if not recalculee:
            sans_norm += 1
            continue
        du_fichier = (f.get("forme_normalisee") or "").strip()
        if du_fichier != recalculee:
            divergences.append((qid, forme, du_fichier, recalculee))
        lignes.append(
            (
                qid,
                forme,
                recalculee,
                (f.get("type") or "").strip(),
                vide_en_none(f.get("langue")),
            )
        )

    with connexion.cursor() as curseur:
        curseur.executemany(
            "INSERT INTO manga.wd_formes (qid, forme, forme_norm, forme_type, langue) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (qid, forme_norm) DO NOTHING",
            lignes,
        )
        curseur.execute("SELECT count(*) FROM manga.wd_formes")
        total = curseur.fetchone()[0]
    return {
        "lues": len(brutes),
        "orphelines": orphelines,
        "sans_norm": sans_norm,
        "candidates": len(lignes),
        "total": total,
        "divergences": divergences,
    }


def charger_auteurs(connexion, source: Path) -> dict[str, int]:
    """manga.wd_auteurs. La source ne donne qu'un Q-id d'auteur : `auteur` et
    `auteur_norm` restent NULL — un Q-id n'est pas un nom."""
    brutes = lire_csv(source / "wd_auteurs.csv")
    with connexion.cursor() as curseur:
        curseur.execute("SELECT qid FROM manga.wd_pivot")
        connus = {q for (q,) in curseur.fetchall()}

    lignes = []
    orphelins = 0
    for a in brutes:
        qid = a["qid"].strip()
        auteur_qid = (a.get("auteur_qid") or "").strip()
        if not auteur_qid:
            continue
        if qid not in connus:
            orphelins += 1
            continue
        lignes.append((qid, auteur_qid))

    with connexion.cursor() as curseur:
        curseur.executemany(
            "INSERT INTO manga.wd_auteurs (qid, auteur_qid) VALUES (%s, %s) "
            "ON CONFLICT (qid, auteur_qid) DO NOTHING",
            lignes,
        )
        curseur.execute("SELECT count(*) FROM manga.wd_auteurs")
        total = curseur.fetchone()[0]
    return {"lus": len(brutes), "orphelins": orphelins, "total": total}


@app.command()
def charger(
    source: Path = typer.Option(  # noqa: B008
        SOURCE_DEFAUT, help="Dossier des CSV Wikidata (lecture seule)."
    ),
) -> None:
    """Charge les CSV Wikidata vers manga.wd_*."""
    debut = time.monotonic()
    with psycopg.connect(dsn()) as connexion:
        pivot = charger_pivot(connexion, source)
        typer.echo(
            f"  ✓ wd_pivot : {pivot['total']} lignes "
            f"({pivot['lus']} pivot + {pivot['entites']} entités fusionnés) — "
            f"{pivot['avec_mal_id']} mal_id, {pivot['avec_anilist_id']} anilist_id"
        )
        formes = charger_formes(connexion, source)
        typer.echo(
            f"  ✓ wd_formes : {formes['total']} lignes "
            f"({formes['lues']} lues, {formes['orphelines']} sans entité, "
            f"{formes['sans_norm']} sans forme normalisée)"
        )
        if formes["divergences"]:
            typer.echo(
                f"    ⚠️  {len(formes['divergences'])} formes dont la "
                "normalisation du CSV diffère du recalcul"
            )
        auteurs = charger_auteurs(connexion, source)
        typer.echo(
            f"  ✓ wd_auteurs : {auteurs['total']} lignes "
            f"({auteurs['lus']} lus, {auteurs['orphelins']} sans entité)"
        )
        connexion.commit()
    typer.echo(f"Terminé en {time.monotonic() - debut:.1f} s.")


def main() -> int:
    try:
        app()
    except ErreurChargement as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
