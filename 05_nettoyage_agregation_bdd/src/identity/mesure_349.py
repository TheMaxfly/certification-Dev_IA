"""MESURE des 349 « review_k_annee_discordante » — seau adjacent × second signal.

    uv run python -m identity.mesure_349

LECTURE SEULE. Aucune écriture en base, aucune décision, aucun journal. Cette
mesure INSTRUIT une politique éventuelle (« seau adjacent sous second signal »)
qui, si elle est retenue, sera une décision documentée du PROCHAIN run — jamais
rétroactive sur les décisions déjà journalisées.

CE QUE LA MESURE DOIT TRANCHER. L'étage 2 a calibré la fenêtre d'année à
[+0, +2], empirique et sans plancher (dette 22.4). Elle exclut le seau −1, que
le plancher [−1, +8] de l'étage 1 acceptait. 349 séries partent donc en
`review_k_annee_discordante`. Question : faut-il autoriser le seau ADJACENT à la
fenêtre quand un SECOND signal confirme ? La réponse dépend de deux chiffres que
seule la mesure donne — combien de séries sont réellement dans le seau adjacent,
et combien d'entre elles portent un second signal.

DEUX SECONDS SIGNAUX, et ils ne se valent pas :
  - auteur concordant  : auteurs MS normalisés × kitsu_staff.personne_norm ;
  - kitsu_id historique : ms_kitsu_map non ambigu pointant le MÊME kitsu_id que
    le candidat — deux sources indépendantes qui disent la même chose.

UNE SEULE normalisation : identity.normaliser(), en Python, comme partout
ailleurs dans le chemin de décision. Aucune normalisation SQL ad hoc.
"""

from __future__ import annotations

import collections
import csv
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from identity.wikidata_dump import normaliser

MODULE = Path(__file__).resolve().parents[2]
RAPPORTS = MODULE / "data" / "rapports" / "mesure_349"

# La fenêtre retenue par l'étage 2. Le seau ADJACENT est celui qui la borde
# immédiatement de part et d'autre : c'est lui, et lui seul, que la politique
# envisagée toucherait.
FENETRE = (0, 2)
ADJACENTS = (FENETRE[0] - 1, FENETRE[1] + 1)  # -1 et +3

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurMesure(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurMesure(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'"
        )
    return url


def zone(ecart: int) -> str:
    if FENETRE[0] <= ecart <= FENETRE[1]:
        return "fenetre"
    if ecart in ADJACENTS:
        return "ADJACENT"
    return "lointain"


def charger_auteurs_normalises(cur, series_ids: list[int]) -> int:
    """Table temporaire (series_id, auteur_norm) via identity.normaliser()."""
    cur.execute(
        "SELECT s.series_id, s.series_scenariste, s.series_dessinateur "
        "FROM manga.ms_series_enriched s WHERE s.series_id = ANY(%s)",
        (series_ids,),
    )
    lignes: set[tuple[int, str]] = set()
    for series_id, scenariste, dessinateur in cur.fetchall():
        for brut in (scenariste, dessinateur):
            if not brut:
                continue
            norme = normaliser(brut)
            if norme:
                lignes.add((series_id, norme))
    cur.execute(
        "CREATE TEMP TABLE m349_auteur (series_id bigint, auteur_norm text) "
        "ON COMMIT DROP"
    )
    with cur.copy("COPY m349_auteur (series_id, auteur_norm) FROM STDIN") as copie:
        for ligne in sorted(lignes):
            copie.write_row(ligne)
    cur.execute("CREATE INDEX ON m349_auteur (series_id)")
    cur.execute("ANALYZE m349_auteur")
    return len(lignes)


SQL_PAIRES = """
SELECT
    c.series_id,
    s.series_title,
    c.kitsu_id,
    (s.series_year - km.annee)          AS ecart,
    s.series_year,
    km.annee,
    -- Second signal n°1 : l'auteur.
    EXISTS (SELECT 1 FROM manga.kitsu_staff ks
            JOIN m349_auteur a ON a.auteur_norm = ks.personne_norm
            WHERE ks.kitsu_id = c.kitsu_id AND a.series_id = c.series_id)
        AS auteur_concordant,
    -- Second signal n°2 : le kitsu_id historique NON AMBIGU pointant le MÊME
    -- candidat. Deux sources indépendantes.
    EXISTS (SELECT 1 FROM manga.ms_kitsu_map mk
            WHERE mk.series_id = c.series_id AND mk.kitsu_id = c.kitsu_id
              AND NOT EXISTS (SELECT 1 FROM manga.ms_kitsu_ambiguous amb
                              WHERE amb.series_id = c.series_id))
        AS historique_confirme
FROM (
    SELECT DISTINCT s2.series_id, kf.kitsu_id
    FROM manga.ms_series_enriched s2
    JOIN manga.ms_formes mf
      ON mf.series_id = s2.series_id AND mf.forme_norm <> ''
    JOIN manga.kitsu_formes kf ON kf.forme_norm = mf.forme_norm
    WHERE s2.series_id = ANY(%s)
) c
JOIN manga.ms_series_enriched s ON s.series_id = c.series_id
JOIN manga.kitsu_meta km ON km.kitsu_id = c.kitsu_id
WHERE s.series_year IS NOT NULL AND km.annee IS NOT NULL
"""


def signaux(auteur: bool, historique: bool) -> str:
    if auteur and historique:
        return "les_deux"
    if auteur:
        return "auteur"
    if historique:
        return "historique"
    return "aucun"


def distance_fenetre(ecart: int) -> int:
    """0 dans la fenêtre, 1 dans le seau adjacent, puis croissant."""
    if FENETRE[0] <= ecart <= FENETRE[1]:
        return 0
    return min(abs(ecart - FENETRE[0]), abs(ecart - FENETRE[1]))


def texte_tableau(par_serie: dict) -> list[str]:
    """Le croisement demandé : écart × second signal."""
    tab = collections.Counter(
        (p["ecart"], signaux(p["auteur_concordant"], p["historique_confirme"]))
        for p in par_serie.values()
    )
    ecarts = sorted({e for e, _ in tab})
    lignes = [
        "",
        "=== Écart d'année × second signal (une ligne par SÉRIE, "
        "candidat le plus proche de la fenêtre) ===",
        "",
        f"{'écart':>7} | {'auteur':>7} | {'histor.':>8} | {'les deux':>9} | "
        f"{'aucun':>6} | {'total':>6} | zone",
        "-" * 74,
    ]
    for e in ecarts:
        a = tab[(e, "auteur")]
        h = tab[(e, "historique")]
        d = tab[(e, "les_deux")]
        n = tab[(e, "aucun")]
        lignes.append(
            f"{e:>+7} | {a:>7} | {h:>8} | {d:>9} | {n:>6} | "
            f"{a + h + d + n:>6} | {zone(e)}"
        )
    return lignes


def texte_histogramme(par_serie: dict) -> list[str]:
    compte = collections.Counter(p["ecart"] for p in par_serie.values())
    maximum = max(compte.values()) if compte else 1
    lignes = [
        "",
        "=== Histogramme de l'écart (série au candidat le plus proche) ===",
        "",
    ]
    for e in sorted(compte):
        barre = "#" * (compte[e] * 50 // maximum)
        marque = {"ADJACENT": "  <== SEAU ADJACENT", "lointain": "", "fenetre": ""}[
            zone(e)
        ]
        lignes.append(f"{e:>+5} : {compte[e]:>4} {barre}{marque}")
    return lignes


@app.command()
def mesurer() -> None:
    """Mesure les 349, écrit le CSV, n'écrit RIEN en base."""
    horodatage = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dossier = RAPPORTS / horodatage
    debut = time.monotonic()

    with psycopg.connect(dsn()) as connexion:
        with connexion.cursor() as cur:
            # Le périmètre vient du JOURNAL : la base fait foi, pas les CSV.
            cur.execute(
                "SELECT v.series_id FROM manga.v_match_current v "
                "JOIN manga.match_decision d ON d.decision_id = v.decision_id "
                "WHERE v.status = 'needs_review' "
                "  AND d.details->>'case' = 'review_k_annee_discordante'"
            )
            cibles = [s for (s,) in cur.fetchall()]
            if not cibles:
                raise ErreurMesure(
                    "Aucune décision 'review_k_annee_discordante' au journal — "
                    "l'étage 2 a-t-il été exécuté ? STOP."
                )
            typer.echo(f"→ périmètre : {len(cibles)} séries (depuis le journal)")

            n_auteurs = charger_auteurs_normalises(cur, cibles)
            typer.echo(f"→ {n_auteurs} noms d'auteurs MS normalisés")

            cur.execute(SQL_PAIRES, (cibles,))
            paires = [
                {
                    "series_id": r[0],
                    "titre": r[1],
                    "kitsu_id": r[2],
                    "ecart": r[3],
                    "annee_ms": r[4],
                    "annee_kitsu": r[5],
                    "auteur_concordant": r[6],
                    "historique_confirme": r[7],
                }
                for r in cur.fetchall()
            ]

            # CONTRÔLE : la matrice évalue « historique confirmé » AVANT le test
            # d'année. Un historique confirmé peut donc passer AUTO malgré une
            # année discordante — écart à la règle « année discordante interdit
            # l'AUTO ». On le chiffre plutôt que de le supposer.
            cur.execute(
                "SELECT count(*) FROM manga.match_decision d "
                "JOIN manga.ms_series_enriched s ON s.series_id = d.series_id "
                "JOIN manga.work_identity w ON w.series_id = d.series_id "
                "JOIN manga.kitsu_meta km ON km.kitsu_id = w.kitsu_id::bigint "
                "WHERE d.details->>'case' = 'auto_k_historique_confirme' "
                "  AND s.series_year IS NOT NULL AND km.annee IS NOT NULL "
                "  AND (s.series_year - km.annee) NOT BETWEEN %s AND %s",
                FENETRE,
            )
            auto_hors_fenetre = cur.fetchone()[0]

        connexion.rollback()  # LECTURE SEULE : rien n'est écrit, jamais.

    # Une ligne par série : le candidat le plus PROCHE de la fenêtre, départagé
    # par la force du second signal. Règle explicite, pas un hasard de tri.
    par_serie: dict[int, dict] = {}
    for p in paires:
        rang = (
            distance_fenetre(p["ecart"]),
            0 if p["historique_confirme"] else (1 if p["auteur_concordant"] else 2),
        )
        if p["series_id"] not in par_serie or rang < par_serie[p["series_id"]]["_rang"]:
            par_serie[p["series_id"]] = {**p, "_rang": rang}

    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / "seau_adjacent.csv"
    with chemin.open("w", encoding="utf-8", newline="") as fh:
        writeur = csv.writer(fh)
        writeur.writerow(
            [
                "series_id",
                "titre_ms",
                "kitsu_id_candidat",
                "ecart",
                "annee_ms",
                "annee_kitsu",
                "zone",
                "auteur_concordant",
                "historique_confirme",
                "second_signal",
            ]
        )
        for p in sorted(paires, key=lambda x: (x["series_id"], x["kitsu_id"])):
            writeur.writerow(
                [
                    p["series_id"],
                    p["titre"],
                    p["kitsu_id"],
                    p["ecart"],
                    p["annee_ms"],
                    p["annee_kitsu"],
                    zone(p["ecart"]),
                    p["auteur_concordant"],
                    p["historique_confirme"],
                    signaux(p["auteur_concordant"], p["historique_confirme"]),
                ]
            )

    for ligne in texte_histogramme(par_serie):
        typer.echo(ligne)
    for ligne in texte_tableau(par_serie):
        typer.echo(ligne)

    adjacentes = [p for p in par_serie.values() if zone(p["ecart"]) == "ADJACENT"]
    avec_signal = [
        p for p in adjacentes if p["auteur_concordant"] or p["historique_confirme"]
    ]
    typer.echo("")
    typer.echo("=== Ce que la politique envisagée débloquerait ===")
    typer.echo(f"  séries mesurées (écart calculable)  : {len(par_serie)}")
    typer.echo(f"  dans le seau ADJACENT ({ADJACENTS}) : {len(adjacentes)}")
    typer.echo(f"  dont portant un SECOND SIGNAL       : {len(avec_signal)}")
    typer.echo("")
    typer.echo(
        f"⚠ CONTRÔLE : {auto_hors_fenetre} décisions 'auto_k_historique_confirme' "
        "ont une année HORS fenêtre"
    )
    typer.echo("")
    typer.echo(f"Livrable : {chemin}")
    typer.echo("LECTURE SEULE — aucune écriture en base, aucune décision prise.")
    typer.echo(f"Terminé en {time.monotonic() - debut:.1f} s.")


def main() -> int:
    try:
        app()
    except ErreurMesure as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
