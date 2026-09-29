"""Arbitrage humain d'un point A de la propagation : plusieurs entrées Kitsu.

    uv run python -m identity.arbitrage_kitsu 52657 36209 --motif "…" --dry-run
    uv run python -m identity.arbitrage_kitsu 52657 36209 --motif "…"

La propagation (`identity.propagation_kitsu`) exclut et liste une série dont
les identifiants MAL / AniList mènent à plusieurs entrées Kitsu : elle ne
choisit pas. Cet outil écrit le choix d'un humain, et lui seul.

La décision est DÉRIVÉE, comme la propagation : elle n'identifie pas l'œuvre
(le QID reste celui de la décision source), elle choisit son entrée Kitsu. Elle
désigne donc sa décision source dans `details.decision_source`, et les mesures
de couverture suivent ce lien. Elle porte toutes les entrées en présence, celle
retenue et le motif : `human_review`, statut `validated`, `decided_by='human'`.

Refus, sans rien écrire : série sans identité décidée, kitsu_id déjà renseigné
(autre que l'entrée retenue), entrée retenue hors des entrées que les
identifiants désignent, entrée déjà rattachée à une autre série. Idempotent :
une série déjà rattachée à l'entrée retenue n'est pas réécrite.
"""

from __future__ import annotations

import json
import os
import sys

import psycopg
import typer

app = typer.Typer(add_completion=False, help=__doc__)

SQL_ENTREES = """
SELECT DISTINCT km.kitsu_id
FROM manga.work_identity w
JOIN manga.kitsu_mappings km
  ON (km.external_site = 'myanimelist/manga' AND km.external_id = w.mal_id)
  OR (km.external_site = 'anilist/manga' AND km.external_id = w.anilist_id)
WHERE w.series_id = %s
ORDER BY 1
"""


class ErreurArbitrage(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def arbitrer(cx: psycopg.Connection, series_id: int, retenue: int, motif: str) -> str:
    """Écrit l'arbitrage dans la transaction courante ; rend ce qui a été fait."""
    if not motif.strip():
        raise ErreurArbitrage("un arbitrage sans motif n'est pas un arbitrage")
    ligne = cx.execute(
        "SELECT w.kitsu_id, v.decision_id, v.method, v.status, v.wikidata_qid "
        "FROM manga.work_identity w "
        "JOIN manga.v_match_current v ON v.series_id = w.series_id "
        "WHERE w.series_id = %s",
        (series_id,),
    ).fetchone()
    if ligne is None:
        raise ErreurArbitrage(f"série {series_id} : aucune identité décidée")
    kitsu_id, source, methode, statut, qid = ligne
    if kitsu_id == str(retenue):
        return f"série {series_id} déjà rattachée à {retenue} : rien à écrire"
    if kitsu_id is not None:
        raise ErreurArbitrage(
            f"série {series_id} : kitsu_id déjà renseigné ({kitsu_id})"
        )
    if statut not in ("auto", "validated"):
        raise ErreurArbitrage(f"série {series_id} : décision courante {statut}")
    entrees = [k for (k,) in cx.execute(SQL_ENTREES, (series_id,))]
    if retenue not in entrees:
        raise ErreurArbitrage(
            f"entrée {retenue} hors de celles que les identifiants désignent : "
            f"{entrees}"
        )
    autre = cx.execute(
        "SELECT series_id FROM manga.work_identity WHERE kitsu_id = %s",
        (str(retenue),),
    ).fetchone()
    if autre:
        raise ErreurArbitrage(f"entrée {retenue} déjà rattachée à la série {autre[0]}")

    details = {
        "case": "arbitrage_plusieurs_entrees_kitsu",
        "kitsu_id": retenue,
        "entrees": entrees,
        "ecartees": [k for k in entrees if k != retenue],
        "motif": motif.strip(),
        "decision_source": source,
        "methode_source": methode,
    }
    cx.execute(
        "INSERT INTO manga.match_decision "
        "(series_id, wikidata_qid, method, score, status, decided_by, details) "
        "VALUES (%s, %s, 'human_review', NULL, 'validated', 'human', %s::jsonb)",
        (series_id, qid, json.dumps(details, ensure_ascii=False)),
    )
    cx.execute(
        "UPDATE manga.work_identity SET kitsu_id = %s, updated_at = now() "
        "WHERE series_id = %s AND kitsu_id IS NULL",
        (str(retenue), series_id),
    )
    return (
        f"série {series_id} → Kitsu {retenue} (écartées : {details['ecartees']}), "
        f"décision source {source} ({methode}), QID {qid}"
    )


@app.command()
def principal(
    series_id: int,
    retenue: int,
    motif: str = typer.Option(..., help="Pourquoi cette entrée."),  # noqa: B008
    dry_run: bool = typer.Option(False, help="Écrit puis ROLLBACK."),  # noqa: B008
) -> None:
    """Rattache la série à l'entrée Kitsu retenue par un humain."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurArbitrage("DATABASE_URL n'est pas définie.")
    with psycopg.connect(url) as cx:
        typer.echo(arbitrer(cx, series_id, retenue, motif))
        if dry_run:
            cx.rollback()
            typer.echo("⚠ DRY-RUN : transaction annulée.")
        else:
            cx.commit()


def main() -> int:
    try:
        app()
    except ErreurArbitrage as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
