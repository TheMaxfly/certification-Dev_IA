"""MESURE — par quel SCRIPT passent les concordances d'auteur de l'étage 1.

    DATABASE_URL='postgresql://manga_api@localhost:5432/apimanga' \
        uv run python -m identity.mesure_formes_auteur

LECTURE SEULE. Un seul SELECT, aucune table temporaire, aucune écriture, aucune
décision. La session est posée en `default_transaction_read_only = on` : le
refus vient du serveur, pas d'une intention.

POURQUOI CETTE MESURE EXISTE (dette 22.3)
-----------------------------------------
La documentation du projet citait « ~99,6 % des concordances d'auteur passent
par des formes latines ». Ce chiffre porte un argument : l'appariement d'auteurs
repose sur des graphies occidentales, ce qui explique les orphelines et fonde le
positionnement sur les titres à documentation japonaise.

Or il venait d'une **requête de diagnostic jetable, à normalisation SQL
approchée**, explicitement notée « indicative » — et cette requête n'a pas été
conservée. Un chiffre qui porte un argument doit être solide, ou disparaître.
Celui-ci a donc été recalculé, avec sa définition écrite AVANT la mesure. La
valeur mesurée le 2026-08-25 est **100,00 %** (1 058 / 1 058) — l'écart avec
99,6 % est de 0,4 point, et la documentation porte désormais la valeur mesurée.

LA DÉFINITION, ARRÊTÉE AVANT DE MESURER
---------------------------------------
- **Concordance d'auteur** : un couple (série, forme Wikidata) tel que
  `normaliser(series_scenariste | series_dessinateur)` égale
  `wd_auteurs_formes.forme_norm`, pour un auteur rattaché au QID retenu. C'est
  le prédicat exact de `sql/etage1_exact.sql`.
- **Dénominateur** : les séries dont la décision courante est
  `method='exact_author'`, `status='auto'` — celles dont l'identité existe
  parce que l'auteur a tranché. Persistées, donc rejouables à l'identique.
- **Forme latine** : `forme_norm` ne contient aucun idéogramme ni kana. Une
  romanisation étiquetée `ja` compte comme latine — c'est la GRAPHIE qui est
  mesurée, pas la déclaration de langue. Les deux ne coïncident pas : 85 formes
  `ja` sont en ASCII, 11 formes `en`/`fr` ne le sont pas.

UNE SEULE normalisation : `identity.normaliser()`, en Python, comme dans tout le
chemin de décision. La réécrire en SQL rejouerait la faute que cette dette
corrige.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from identity.wikidata_dump import normaliser

MODULE = Path(__file__).resolve().parents[2]
REQUETE = Path(__file__).resolve().parent / "sql" / "mesure_formes_auteur.sql"
RAPPORTS = MODULE / "data" / "rapports" / "formes_auteur"

# Cas témoins de la classe « non latin ». Ils ne testent pas PostgreSQL : ils
# testent que la classe de caractères du fichier SQL est arrivée intacte. Un
# fichier réencodé de travers donnerait un chiffre plausible et faux — c'est
# précisément le genre d'accident que cette dette existe pour ne pas répéter.
TEMOINS_LATINS = ("fukuchi tsubasa", "osamu tezuka", "naoki urasawa", "jiro taniguchi")
TEMOINS_NON_LATINS = ("福地翼", "フクチ ツバサ", "ふくち つばさ", "宮崎 駿", "ﾌｸﾁ")

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurMesure(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurMesure(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://manga_api@localhost:5432/apimanga'"
        )
    return url


def classe_non_latine(sql: str) -> str:
    """La classe de caractères, extraite du SQL — pas redéclarée ici.

    La reproduire en Python créerait deux sources de vérité qui dériveraient.
    """
    marqueur = "forme_norm !~ '["
    debut = sql.index(marqueur) + len(marqueur)
    return sql[debut : sql.index("]'", debut)]


def verifier_la_classe(cur, sql: str) -> None:
    """Le serveur classe-t-il les témoins comme attendu ?"""
    classe = classe_non_latine(sql)
    cur.execute(
        "SELECT f, f !~ ('[' || %s || ']') FROM unnest(%s::text[]) AS t(f)",
        (classe, list(TEMOINS_LATINS + TEMOINS_NON_LATINS)),
    )
    verdicts = dict(cur.fetchall())
    fautifs = [f"{f!r} classé non-latin" for f in TEMOINS_LATINS if not verdicts[f]]
    fautifs += [f"{f!r} classé latin" for f in TEMOINS_NON_LATINS if verdicts[f]]
    if fautifs:
        raise ErreurMesure(
            "La classe de caractères du SQL ne se comporte pas comme attendu — "
            "STOP, aucune mesure n'est produite. " + " ; ".join(fautifs)
        )


def auteurs_ms_normalises(cur) -> list[tuple[int, str]]:
    """(series_id, auteur_norm) pour les séries décidées par l'auteur.

    Même lecture et même normalisation qu'`etage1_exact.auteurs_ms_normalises`,
    sur le périmètre des séries DÉCIDÉES au lieu du périmètre non décidé.
    """
    cur.execute(
        "SELECT s.series_id, s.series_scenariste, s.series_dessinateur "
        "  FROM manga.ms_series_enriched s "
        "  JOIN manga.v_match_current v ON v.series_id = s.series_id "
        " WHERE v.method = 'exact_author' AND v.status = 'auto'"
    )
    lignes: set[tuple[int, str]] = set()
    for series_id, scenariste, dessinateur in cur.fetchall():
        for brut in (scenariste, dessinateur):
            if not brut:
                continue
            norme = normaliser(brut)
            if norme:
                lignes.add((series_id, norme))
    return sorted(lignes)


def mesurer(cur) -> dict:
    sql = REQUETE.read_text(encoding="utf-8")
    verifier_la_classe(cur, sql)

    auteurs = auteurs_ms_normalises(cur)
    cur.execute(
        sql,
        {
            "series_ids": [s for s, _ in auteurs],
            "auteurs_norm": [a for _, a in auteurs],
        },
    )
    colonnes = [d[0] for d in cur.description]
    mesure = dict(zip(colonnes, cur.fetchone(), strict=True))
    mesure["auteurs_ms_normalises"] = len(auteurs)
    return mesure


def pourcentage(numerateur: int, denominateur: int) -> float:
    return 100.0 * numerateur / denominateur if denominateur else 0.0


def afficher(m: dict) -> None:
    ligne = typer.echo
    ligne("")
    ligne("=== Concordances d'auteur de l'étage 1, par script ===")
    ligne(
        f"  séries décidées par l'auteur (exact_author/auto) : {m['series_decidees']}"
    )
    ligne(
        f"  dont la concordance est retrouvée aujourd'hui    : "
        f"{m['series_concordantes']}"
    )
    ligne(
        f"  couples (série, forme) concordants               : "
        f"{m['couples_serie_forme']}"
    )
    ligne("")
    ligne("  --- par série (au moins une forme du script) ---")
    ligne(
        f"  avec une forme LATINE      : {m['series_avec_latine']:>5}  "
        f"({pourcentage(m['series_avec_latine'], m['series_concordantes']):.2f} %)"
    )
    ligne(
        f"  sans aucune forme latine   : {m['series_sans_latine']:>5}  "
        f"({pourcentage(m['series_sans_latine'], m['series_concordantes']):.2f} %)"
    )
    ligne(f"    dont latine SEULEMENT    : {m['series_latine_seule']:>5}")
    ligne(f"    dont mixtes              : {m['series_mixtes']:>5}")
    ligne("")
    ligne("  --- par couple (série, forme) ---")
    ligne(
        f"  formes latines             : {m['couples_latins']:>5}  "
        f"({pourcentage(m['couples_latins'], m['couples_serie_forme']):.2f} %)"
    )
    ligne(
        f"  formes non latines         : {m['couples_non_latins']:>5}  "
        f"({pourcentage(m['couples_non_latins'], m['couples_serie_forme']):.2f} %)"
    )
    ligne("")
    ligne("  --- contrefactuel : sans la table des formes multiples ---")
    ligne(f"  concordantes sur le seul nom retenu en D0 : {m['series_d0_seul']:>5}")
    ligne(
        f"  apport de wd_auteurs_formes               : "
        f"{m['series_concordantes'] - m['series_d0_seul']:>5}"
    )
    ligne("")
    ligne("  CHIFFRE CERTIFIÉ — part des séries dont la concordance d'auteur")
    ligne("  passe par au moins une forme latine :")
    ligne(
        f"      {pourcentage(m['series_avec_latine'], m['series_concordantes']):.2f} %"
        f"  ({m['series_avec_latine']} / {m['series_concordantes']})"
    )


def ecrire_rapport(m: dict) -> Path:
    RAPPORTS.mkdir(parents=True, exist_ok=True)
    horodatage = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    chemin = RAPPORTS / f"{horodatage}.json"
    charge = dict(m)
    charge["part_series_avec_forme_latine_pct"] = round(
        pourcentage(m["series_avec_latine"], m["series_concordantes"]), 2
    )
    charge["part_couples_latins_pct"] = round(
        pourcentage(m["couples_latins"], m["couples_serie_forme"]), 2
    )
    charge["mesure_le"] = horodatage
    chemin.write_text(
        json.dumps(charge, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return chemin


@app.command()
def executer(
    rapport: bool = typer.Option(True, help="Écrire le rapport JSON horodaté."),
) -> None:
    """Mesure la répartition par script, en lecture seule."""
    with psycopg.connect(dsn(), connect_timeout=10) as connexion:
        # Le refus d'écriture vient du serveur : une faute de frappe dans cette
        # mesure ne peut pas toucher apimanga.
        connexion.execute("SET default_transaction_read_only = on")
        with connexion.cursor() as cur:
            mesure = mesurer(cur)
        connexion.rollback()

    afficher(mesure)
    if rapport:
        typer.echo(f"\nRapport : {ecrire_rapport(mesure)}")


def main() -> int:
    try:
        app()
    except ErreurMesure as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR base : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
