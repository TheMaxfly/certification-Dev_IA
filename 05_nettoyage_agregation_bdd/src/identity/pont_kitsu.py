"""Étape C, étage 0 : le pont d'identifiants Kitsu → Wikidata.

    uv run python -m identity.pont_kitsu                # exécute et commit
    uv run python -m identity.pont_kitsu --dry-run      # exécute, rollback
    uv run python -m identity.pont_kitsu --rapport-dir chemin/

PURES JOINTURES D'IDENTIFIANTS (aucune lecture de titre) : ms_kitsu_map →
kitsu_mappings → wd_pivot. Trois responsabilités, une transaction :

  1. semer manga.work_identity (une ligne par série MS) et remplir
     ms_series_enriched.work_uid ;
  2. décider le pont — chemins mal→qid et anilist→qid concordants → 'auto',
     divergents ou en collision d'unicité → 'needs_review' ;
  3. journaliser dans manga.match_decision et remplir l'identité des auto.

Idempotent : une série déjà décidée (v_match_current) est sautée. Le rejeu ne
change aucun compte. La logique SQL vit dans sql/pont_kitsu.sql ; ce module
l'oriente, mesure l'entonnoir et écrit les livrables hors dépôt.
"""

from __future__ import annotations

import csv
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

RACINE = Path(__file__).resolve().parents[3]
PONT_SQL = Path(__file__).resolve().parent / "sql" / "pont_kitsu.sql"
# parents[2] = racine du module (05_...), dont le data/ est gitignoré — jamais
# src/data/, qui ne l'est pas et exposerait titres et QID dans un dépôt public.
RAPPORTS = Path(__file__).resolve().parents[2] / "data" / "rapports" / "pont_kitsu"

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurPont(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurPont(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'"
        )
    return url


def verifier_prerequis(cur) -> None:
    """001 (work_identity, match_decision, v_match_current) et 003 (work_uid)
    doivent préexister : le pont ne crée aucune structure. Un manque = STOP."""
    manquants = []
    for objet in (
        "manga.work_identity",
        "manga.match_decision",
        "manga.v_match_current",
        "manga.ms_kitsu_map",
        "manga.ms_kitsu_ambiguous",
        "manga.kitsu_mappings",
        "manga.wd_pivot",
    ):
        cur.execute("SELECT to_regclass(%s)", (objet,))
        if cur.fetchone()[0] is None:
            manquants.append(objet)
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='manga' AND table_name='ms_series_enriched' "
        "AND column_name='work_uid'"
    )
    if cur.fetchone() is None:
        manquants.append("manga.ms_series_enriched.work_uid (003)")
    if manquants:
        raise ErreurPont(
            "Schéma incomplet, migrations manquantes — STOP. Absents : "
            + ", ".join(manquants)
        )


def mesurer_entonnoir(cur) -> dict[str, int]:
    """Chaque marche chiffrée, depuis les tables source et pont_candidat."""
    m: dict[str, int] = {}

    def scalaire(sql: str) -> int:
        cur.execute(sql)
        return cur.fetchone()[0]

    m["series_total"] = scalaire("SELECT count(*) FROM manga.ms_series_enriched")
    m["work_identity"] = scalaire("SELECT count(*) FROM manga.work_identity")
    m["work_uid_rempli"] = scalaire(
        "SELECT count(*) FROM manga.ms_series_enriched WHERE work_uid IS NOT NULL"
    )
    m["ms_kitsu_map"] = scalaire("SELECT count(*) FROM manga.ms_kitsu_map")
    m["exclus_ambigus"] = scalaire(
        "SELECT count(*) FROM manga.ms_kitsu_map m "
        "WHERE EXISTS (SELECT 1 FROM manga.ms_kitsu_ambiguous a "
        "              WHERE a.series_id=m.series_id)"
    )
    m["exclus_needs_review"] = scalaire(
        "SELECT count(*) FROM manga.ms_kitsu_map m "
        "WHERE EXISTS (SELECT 1 FROM manga.ms_series_enriched s "
        "              WHERE s.series_id=m.series_id AND s.needs_review IS TRUE)"
    )
    m["exclus_union"] = scalaire(
        "SELECT count(*) FROM manga.ms_kitsu_map m WHERE "
        "EXISTS (SELECT 1 FROM manga.ms_kitsu_ambiguous a "
        "        WHERE a.series_id=m.series_id) OR "
        "EXISTS (SELECT 1 FROM manga.ms_series_enriched s "
        "        WHERE s.series_id=m.series_id AND s.needs_review IS TRUE)"
    )
    # pont_candidat = candidats RÉELLEMENT traités par ce run (hors déjà-décidés).
    m["candidats"] = scalaire("SELECT count(*) FROM pont_candidat")
    m["avec_mapping"] = scalaire("SELECT count(*) FROM pont_candidat WHERE a_mapping")
    for statut in ("auto", "divergence", "collision", "hors_pont"):
        m[statut] = scalaire(
            f"SELECT count(*) FROM pont_candidat WHERE statut='{statut}'"
        )
    m["decisions_run"] = m["auto"] + m["divergence"] + m["collision"]
    return m


def ecrire_csv(chemin: Path, entetes: list[str], lignes: list[tuple]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(entetes)
        writer.writerows(lignes)


def ecrire_livrables(cur, dossier: Path) -> dict[str, int]:
    """Échantillon de contrôle + listes (divergences, collisions, exclus)."""
    tailles: dict[str, int] = {}

    # 50 décisions auto tirées « au hasard » de façon déterministe (md5), avec
    # le label Wikidata (wd_formes.forme_type='label') pour vérification humaine.
    cur.execute(
        "SELECT p.series_id, mk.ms_title, p.qid, lbl.label, p.kitsu_id "
        "FROM pont_candidat p "
        "JOIN manga.ms_kitsu_map mk ON mk.series_id = p.series_id "
        "LEFT JOIN (SELECT qid, min(forme) AS label FROM manga.wd_formes "
        "           WHERE forme_type='label' GROUP BY qid) lbl ON lbl.qid = p.qid "
        "WHERE p.statut='auto' "
        "ORDER BY md5(p.series_id::text) LIMIT 50"
    )
    echantillon = cur.fetchall()
    ecrire_csv(
        dossier / "echantillon_auto.csv",
        ["series_id", "ms_title", "qid", "wd_label", "kitsu_id"],
        echantillon,
    )
    tailles["echantillon"] = len(echantillon)

    cur.execute(
        "SELECT series_id, kitsu_id, mal_id, anilist_id, n_qid "
        "FROM pont_candidat WHERE statut='divergence' ORDER BY series_id"
    )
    divergences = cur.fetchall()
    ecrire_csv(
        dossier / "divergences.csv",
        ["series_id", "kitsu_id", "mal_id", "anilist_id", "n_qid"],
        divergences,
    )
    tailles["divergences"] = len(divergences)

    cur.execute(
        "SELECT series_id, kitsu_id, mal_id, anilist_id, qid "
        "FROM pont_candidat WHERE statut='collision' ORDER BY series_id"
    )
    collisions = cur.fetchall()
    ecrire_csv(
        dossier / "collisions.csv",
        ["series_id", "kitsu_id", "mal_id", "anilist_id", "qid"],
        collisions,
    )
    tailles["collisions"] = len(collisions)

    cur.execute(
        "SELECT m.series_id, m.kitsu_id, "
        "  EXISTS (SELECT 1 FROM manga.ms_kitsu_ambiguous a "
        "          WHERE a.series_id=m.series_id) AS ambigu, "
        "  EXISTS (SELECT 1 FROM manga.ms_series_enriched s "
        "          WHERE s.series_id=m.series_id AND s.needs_review IS TRUE) "
        "    AS needs_review "
        "FROM manga.ms_kitsu_map m WHERE "
        "  EXISTS (SELECT 1 FROM manga.ms_kitsu_ambiguous a "
        "          WHERE a.series_id=m.series_id) OR "
        "  EXISTS (SELECT 1 FROM manga.ms_series_enriched s "
        "          WHERE s.series_id=m.series_id AND s.needs_review IS TRUE) "
        "ORDER BY m.series_id"
    )
    exclus = cur.fetchall()
    ecrire_csv(
        dossier / "exclus.csv",
        ["series_id", "kitsu_id", "ambigu", "needs_review"],
        exclus,
    )
    tailles["exclus"] = len(exclus)
    return tailles


def afficher_entonnoir(m: dict[str, int], tailles: dict[str, int]) -> None:
    lignes = [
        "",
        "ENTONNOIR DU PONT (étage 0)",
        "─" * 58,
        f"  {m['series_total']:>6}  séries MS (moyeu semé)",
        f"  {m['work_identity']:>6}  lignes work_identity",
        f"  {m['work_uid_rempli']:>6}  ms_series_enriched.work_uid renseignés",
        "  " + "─" * 40,
        f"  {m['ms_kitsu_map']:>6}  séries avec kitsu_id (ms_kitsu_map)",
        f"  -{m['exclus_union']:>5}  exclues → cascade "
        f"({m['exclus_ambigus']} ambiguës, "
        f"{m['exclus_needs_review']} needs_review, "
        f"∩ {m['exclus_ambigus'] + m['exclus_needs_review'] - m['exclus_union']})",
        f"  {m['candidats']:>6}  CANDIDATES au pont (traitées ce run)",
        f"  {m['avec_mapping']:>6}  avec un mapping externe (mal / anilist)",
        "  " + "─" * 40,
        f"  {m['auto']:>6}  → AUTO (1 QID, concordant)  [identité remplie]",
        f"  {m['divergence']:>6}  → needs_review (QID divergents)",
        f"  {m['collision']:>6}  → needs_review (collision d'unicité)",
        f"  {m['hors_pont']:>6}  → hors pont (aucun QID) → cascade",
        "  " + "─" * 40,
        f"  {m['decisions_run']:>6}  décisions écrites ce run (auto + needs_review)",
        f"  {tailles['echantillon']:>6}  lignes d'échantillon de contrôle (CSV)",
        "",
    ]
    for ligne in lignes:
        typer.echo(ligne)


@app.command()
def construire(
    dry_run: bool = typer.Option(  # noqa: B008
        False, help="Exécute puis ROLLBACK : rien n'est écrit en base."
    ),
    rapport_dir: str = typer.Option(  # noqa: B008
        None, help="Dossier des livrables (défaut : data/rapports/pont_kitsu/<ts>)."
    ),
) -> None:
    """Sème le moyeu, décide le pont, journalise et remplit l'identité."""
    horodatage = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dossier = Path(rapport_dir) if rapport_dir else RAPPORTS / horodatage
    debut = time.monotonic()

    with psycopg.connect(dsn()) as connexion:
        with connexion.cursor() as cur:
            verifier_prerequis(cur)
            cur.execute(PONT_SQL.read_text(encoding="utf-8"))
            entonnoir = mesurer_entonnoir(cur)
            tailles = ecrire_livrables(cur, dossier)

        if dry_run:
            connexion.rollback()
        else:
            connexion.commit()

    afficher_entonnoir(entonnoir, tailles)
    typer.echo(f"Livrables : {dossier}")
    if dry_run:
        typer.echo("⚠ DRY-RUN : transaction annulée, aucune écriture en base.")
    typer.echo(f"Terminé en {time.monotonic() - debut:.1f} s.")


def main() -> int:
    try:
        app()
    except ErreurPont as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
