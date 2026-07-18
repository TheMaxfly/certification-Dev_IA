"""Re-parse LOCAL des sitelinks jawiki → manga.wd_pivot.wiki_ja (étape D0-2).

    uv run python -m identity.reparse_jawiki

AUCUN réseau. Les 165 lots d'entités du 2026-07-14 portent déjà
sitelinks['jawiki'] : `parse()` de wikidata_dump n'extrayait que frwiki/enwiki.
Ce module relit ces mêmes fichiers et remplit wiki_ja, sans jamais rien
re-télécharger ni écrire dans le répertoire d'entités.

Idempotent : l'UPDATE ne touche que les qid dont wiki_ja change réellement ; un
re-run n'écrit rien.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import psycopg
import typer

MODULE = Path(__file__).resolve().parents[2]
ENTITES_DEFAUT = MODULE / "data" / "raw" / "wikidata" / "2026-07-14" / "entities"

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurReparse(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurReparse(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'"
        )
    return url


def extraire_jawiki(entites_dir: Path) -> dict[str, str]:
    """{qid: titre jawiki} depuis les lots d'entités déjà sur disque."""
    lots = sorted(entites_dir.glob("entities_*.json"))
    if not lots:
        raise ErreurReparse(
            f"Aucun lot d'entités dans {entites_dir}. Ce module NE télécharge "
            "rien : les entités 2026-07-14 doivent être présentes."
        )
    ja: dict[str, str] = {}
    for fichier in lots:
        data = json.loads(fichier.read_text(encoding="utf-8"))
        for qid, ent in data.get("entities", {}).items():
            if "missing" in ent:
                continue
            titre = ent.get("sitelinks", {}).get("jawiki", {}).get("title")
            if titre:
                ja[qid] = titre
    return ja


@app.command()
def charger(
    entites_dir: Path = typer.Option(  # noqa: B008
        ENTITES_DEFAUT, help="Dossier des lots d'entités (lecture seule)."
    ),
) -> None:
    """Relit les sitelinks jawiki et remplit wd_pivot.wiki_ja."""
    debut = time.monotonic()
    ja = extraire_jawiki(entites_dir)
    typer.echo(f"→ {len(ja)} qid avec un sitelink jawiki sur disque")

    with psycopg.connect(dsn()) as connexion:
        with connexion.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE ja_stage (qid text PRIMARY KEY, wiki_ja text) "
                "ON COMMIT DROP"
            )
            with cur.copy("COPY ja_stage (qid, wiki_ja) FROM STDIN") as copie:
                for qid, titre in ja.items():
                    copie.write_row((qid, titre))
            # Idempotent : ne réécrit pas un wiki_ja déjà correct.
            cur.execute(
                "UPDATE manga.wd_pivot p SET wiki_ja = j.wiki_ja "
                "FROM ja_stage j "
                "WHERE p.qid = j.qid AND p.wiki_ja IS DISTINCT FROM j.wiki_ja"
            )
            maj = cur.rowcount
            cur.execute(
                "SELECT count(*) FILTER (WHERE wiki_ja IS NOT NULL), "
                "       count(*) FILTER (WHERE wiki_fr IS NOT NULL), "
                "       count(*) FILTER (WHERE wiki_en IS NOT NULL), "
                "       count(*), "
                "       count(*) FILTER (WHERE wiki_ja IS NOT NULL "
                "                        AND wiki_fr IS NULL AND wiki_en IS NULL) "
                "FROM manga.wd_pivot"
            )
            n_ja, n_fr, n_en, total, ja_seul = cur.fetchone()
            # qid présents sur disque mais absents de wd_pivot (hors référentiel).
            cur.execute(
                "SELECT count(*) FROM ja_stage j "
                "WHERE NOT EXISTS (SELECT 1 FROM manga.wd_pivot p WHERE p.qid=j.qid)"
            )
            hors_pivot = cur.fetchone()[0]
            exemples = _exemples_ja_seul(cur)
        connexion.commit()

    _rapport(total, n_ja, n_fr, n_en, ja_seul, maj, hors_pivot, exemples)
    typer.echo(f"Terminé en {time.monotonic() - debut:.1f} s.")


def _exemples_ja_seul(cur) -> list[tuple]:
    cur.execute(
        "SELECT qid, wiki_ja, label_principal FROM manga.wd_pivot "
        "WHERE wiki_ja IS NOT NULL AND wiki_fr IS NULL AND wiki_en IS NULL "
        "ORDER BY md5(qid) LIMIT 10"
    )
    return cur.fetchall()


def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:.1f}%" if total else "—"


def _rapport(total, n_ja, n_fr, n_en, ja_seul, maj, hors_pivot, exemples) -> None:
    typer.echo("")
    typer.echo("COUVERTURE DES SITELINKS (wd_pivot)")
    typer.echo("─" * 50)
    typer.echo(f"  wiki_ja : {n_ja:>5} / {total}  ({_pct(n_ja, total)})  ← D0")
    typer.echo(f"  wiki_en : {n_en:>5} / {total}  ({_pct(n_en, total)})")
    typer.echo(f"  wiki_fr : {n_fr:>5} / {total}  ({_pct(n_fr, total)})")
    typer.echo("─" * 50)
    typer.echo(f"  {maj} qid mis à jour ce run ; {hors_pivot} hors référentiel")
    typer.echo(
        f"  {ja_seul} œuvres « ja seulement » (ni fr ni en) — le gisement niche pro"
    )
    if exemples:
        typer.echo("\n  10 exemples « ja seulement » (qid, jawiki, label) :")
        for qid, wiki_ja, label in exemples:
            typer.echo(f"    {qid:12s} {wiki_ja[:30]:30s} {label or ''}")


def main() -> int:
    try:
        app()
    except ErreurReparse as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
