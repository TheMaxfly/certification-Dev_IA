"""Étage 1 de la cascade : jointure exacte multi-formes MS × Wikidata.

    uv run python -m identity.etage1_exact               # exécute et commit
    uv run python -m identity.etage1_exact --dry-run     # exécute, rollback

Deux phases, une transaction :

  PHASE 1 — CALIBRATION. La fenêtre d'année n'est pas décrétée : elle est
  mesurée sur les 1 689 identités du pont, vérité indépendante des dates.
  Fenêtre = [p5 arrondi bas, p95 arrondi haut], élargie au minimum à [-1, +8].

  PHASE 2 — DÉCISION. Candidats par égalité de forme normalisée, puis matrice
  figée sur deux signaux : auteur (concordant / discordant / incomparable) et
  année (dans la fenêtre / hors fenêtre / incomparable). L'année CONFIRME, elle
  ne décide jamais seule ; une année discordante interdit l'auto.

Les noms d'auteurs MS sont normalisés ICI par identity.normaliser(), jamais par
du SQL ad hoc : les deux côtés du rapprochement doivent parler la même langue.

Idempotent : une série déjà décidée (v_match_current) est sautée ; re-run = 0
écriture. La logique SQL vit dans sql/etage1_exact.sql.
"""

from __future__ import annotations

import csv
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from identity.wikidata_dump import normaliser

MODULE = Path(__file__).resolve().parents[2]
ETAGE1_SQL = Path(__file__).resolve().parent / "sql" / "etage1_exact.sql"
RAPPORTS = MODULE / "data" / "rapports" / "etage1"

# Plancher imposé : l'édition VF ne précède pas l'originale de plus d'un an et
# peut la suivre de plusieurs années. La calibration ne peut que l'élargir.
FENETRE_PLANCHER = (-1, 8)

CAS_AUTO = ("auto_unique_auteur", "auto_unique_annee", "auto_multi_auteur")
CAS_REVIEW = (
    "review_sans_signal",
    "review_auteur_discordant",
    "review_annee_discordante",
    "review_multi_annee_seule",
    "review_ambiguite",
    "review_collision_qid",
    "review_collision_id",
)

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurEtage1(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurEtage1(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'"
        )
    return url


def verifier_prerequis(cur) -> None:
    """Le schéma doit préexister : cet étage ne crée aucune structure."""
    manquants = []
    for objet in (
        "manga.work_identity",
        "manga.match_decision",
        "manga.v_match_current",
        "manga.ms_formes",
        "manga.wd_formes",
        "manga.wd_pivot",
        "manga.wd_auteurs",
        "manga.wd_auteurs_formes",
    ):
        cur.execute("SELECT to_regclass(%s)", (objet,))
        if cur.fetchone()[0] is None:
            manquants.append(objet)
    if manquants:
        raise ErreurEtage1(
            "Schéma incomplet, migrations manquantes — STOP. Absents : "
            + ", ".join(manquants)
        )
    # La méthode doit exister dans le CHECK de 001 : pas de migration sauvage.
    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid='manga.match_decision'::regclass AND contype='c' "
        "AND conname LIKE %s",
        ("%method%",),
    )
    ligne = cur.fetchone()
    contrainte = ligne[0] if ligne else ""
    for methode in ("'exact'", "'exact_author'"):
        if methode not in contrainte:
            raise ErreurEtage1(
                f"La méthode {methode} n'est pas autorisée par le CHECK de "
                "match_decision — STOP, aucune migration n'est faite ici."
            )


# --------------------------------------------------------------------------- #
# PHASE 1 — calibration de la fenêtre d'année
# --------------------------------------------------------------------------- #
def ecarts_du_pont(cur) -> list[int]:
    """(annee_ms - annee_wd) sur les identités sûres du pont."""
    cur.execute(
        "SELECT s.series_year - p.annee "
        "FROM manga.work_identity w "
        "JOIN manga.ms_series_enriched s ON s.series_id = w.series_id "
        "JOIN manga.wd_pivot p ON p.qid = w.wikidata_qid "
        "WHERE w.wikidata_qid IS NOT NULL "
        "  AND s.series_year IS NOT NULL AND p.annee IS NOT NULL"
    )
    return [e for (e,) in cur.fetchall()]


def calibrer(ecarts: list[int]) -> dict:
    """Fenêtre [p5, p95] élargie au plancher. Renvoie stats + fenêtre."""
    if not ecarts:
        raise ErreurEtage1(
            "Aucune paire d'années exploitable sur le pont : la fenêtre ne peut "
            "pas être calibrée — STOP."
        )
    tries = sorted(ecarts)

    def pct(p: float) -> float:
        return statistics.quantiles(tries, n=100, method="inclusive")[int(p) - 1]

    p5, p95 = pct(5), pct(95)
    basse = min(int(p5 // 1), FENETRE_PLANCHER[0])
    haute = max(-(-p95 // 1), FENETRE_PLANCHER[1])
    basse, haute = int(basse), int(haute)
    couverts = sum(1 for e in tries if basse <= e <= haute)
    return {
        "n": len(tries),
        "min": tries[0],
        "p5": p5,
        "p25": pct(25),
        "med": statistics.median(tries),
        "p75": pct(75),
        "p95": p95,
        "max": tries[-1],
        "fenetre": (basse, haute),
        "couverts": couverts,
        "couverture": 100 * couverts / len(tries),
        "histogramme": {v: sum(1 for e in tries if e == v) for v in range(-3, 16)},
    }


def texte_calibration(c: dict) -> str:
    basse, haute = c["fenetre"]
    lignes = [
        "# Calibration de la fenêtre d'année (étage 1)",
        "",
        "Mesurée sur les identités du pont Kitsu (étage 0), vérité indépendante",
        "des dates : seules les paires dont les DEUX années existent comptent.",
        "",
        f"- paires exploitables : **{c['n']}**",
        f"- min={c['min']} · p5={c['p5']:.0f} · p25={c['p25']:.0f} · "
        f"médiane={c['med']:.0f} · p75={c['p75']:.0f} · p95={c['p95']:.0f} · "
        f"max={c['max']}",
        "",
        f"**Fenêtre retenue : [{basse:+d}, {haute:+d}]** "
        f"— couvre {c['couverts']}/{c['n']} paires ({c['couverture']:.1f} %).",
        "",
        "## Histogramme de (année MS − année Wikidata)",
        "",
        "```",
    ]
    maximum = max(c["histogramme"].values()) or 1
    for valeur, nombre in sorted(c["histogramme"].items()):
        barre = "#" * (nombre * 60 // maximum)
        marque = " " if basse <= valeur <= haute else "  (hors fenêtre)"
        lignes.append(f"{valeur:+4d} : {nombre:5d} {barre}{marque}")
    lignes += ["```", ""]
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
# PHASE 2 — préparation des auteurs MS normalisés
# --------------------------------------------------------------------------- #
def auteurs_ms_normalises(cur) -> list[tuple[int, str]]:
    """(series_id, auteur_norm) pour le périmètre, via identity.normaliser()."""
    cur.execute(
        "SELECT s.series_id, s.series_scenariste, s.series_dessinateur "
        "FROM manga.ms_series_enriched s "
        "WHERE NOT EXISTS (SELECT 1 FROM manga.v_match_current v "
        "                  WHERE v.series_id = s.series_id)"
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


# --------------------------------------------------------------------------- #
# Mesure et livrables
# --------------------------------------------------------------------------- #
def compter_perimetre(cur) -> int:
    """Séries sans décision courante. À mesurer AVANT d'écrire le journal :
    une fois les décisions insérées, v_match_current les inclut et le périmètre
    mesuré serait celui d'après, pas celui d'entrée."""
    cur.execute(
        "SELECT count(*) FROM manga.ms_series_enriched s WHERE NOT EXISTS "
        "(SELECT 1 FROM manga.v_match_current v WHERE v.series_id=s.series_id)"
    )
    return cur.fetchone()[0]


def mesurer(cur, perimetre: int) -> dict:
    def scalaire(sql: str) -> int:
        cur.execute(sql)
        return cur.fetchone()[0]

    mesures = {
        "perimetre": perimetre,
        "candidats": scalaire("SELECT count(*) FROM etage1_candidat"),
        "series_avec_candidat": scalaire(
            "SELECT count(DISTINCT series_id) FROM etage1_candidat"
        ),
    }
    cur.execute("SELECT cas, count(*) FROM etage1_serie GROUP BY cas")
    mesures["cas"] = dict(cur.fetchall())
    cur.execute(
        "SELECT count(*) FILTER (WHERE n_cand=1), count(*) FILTER (WHERE n_cand>1) "
        "FROM etage1_serie"
    )
    mesures["uniques"], mesures["multis"] = cur.fetchone()
    return mesures


def ecrire_csv(chemin: Path, entetes: list[str], lignes) -> int:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", encoding="utf-8", newline="") as fh:
        writeur = csv.writer(fh)
        writeur.writerow(entetes)
        nombre = 0
        for ligne in lignes:
            writeur.writerow(ligne)
            nombre += 1
    return nombre


def ecrire_livrables(cur, dossier: Path, calib: dict, mesures: dict) -> None:
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "calibration_annees.md").write_text(
        texte_calibration(calib), encoding="utf-8"
    )

    # Échantillon AUTO : la matière de l'étage R (pré-remplissage + arbitrage).
    cur.execute(
        "SELECT e.series_id, s.series_title, "
        "  concat_ws(' / ', s.series_scenariste, s.series_dessinateur), "
        "  s.series_year, e.qid, p.label_principal, "
        "  (SELECT string_agg(DISTINCT a.auteur, ' / ') FROM manga.wd_auteurs a "
        "   WHERE a.qid = e.qid), p.annee, "
        "  CASE e.cas WHEN 'auto_unique_auteur' THEN 0.97 "
        "             WHEN 'auto_multi_auteur' THEN 0.95 ELSE 0.93 END, e.cas "
        "FROM etage1_serie e "
        "JOIN manga.ms_series_enriched s ON s.series_id = e.series_id "
        "JOIN manga.wd_pivot p ON p.qid = e.qid "
        "WHERE e.cas LIKE 'auto%' ORDER BY md5(e.series_id::text) LIMIT 100"
    )
    ecrire_csv(
        dossier / "echantillon_auto.csv",
        [
            "series_id",
            "titre_ms",
            "auteurs_ms",
            "annee_ms",
            "qid",
            "label_wd",
            "auteurs_wd",
            "annee_wd",
            "score",
            "cas",
        ],
        cur.fetchall(),
    )

    # needs_review : l'entrée de l'étage R, avec les candidats en clair.
    cur.execute(
        "SELECT e.series_id, s.series_title, "
        "  concat_ws(' / ', s.series_scenariste, s.series_dessinateur), "
        "  s.series_year, e.cas, e.n_cand, "
        "  (SELECT string_agg(c.qid || '=' || coalesce(w.label_principal,'?'), ' | ' "
        "                     ORDER BY c.qid) "
        "   FROM etage1_candidat c JOIN manga.wd_pivot w ON w.qid = c.qid "
        "   WHERE c.series_id = e.series_id) "
        "FROM etage1_serie e "
        "JOIN manga.ms_series_enriched s ON s.series_id = e.series_id "
        "WHERE e.cas LIKE 'review%' ORDER BY e.series_id"
    )
    ecrire_csv(
        dossier / "needs_review.csv",
        [
            "series_id",
            "titre_ms",
            "auteurs_ms",
            "annee_ms",
            "cas",
            "n_cand",
            "candidats",
        ],
        cur.fetchall(),
    )

    # La case « sans signal », isolée : décision de politique au prochain run.
    cur.execute(
        "SELECT e.series_id, s.series_title, e.qid, p.label_principal, "
        "  s.series_year, p.annee "
        "FROM etage1_serie e "
        "JOIN manga.ms_series_enriched s ON s.series_id = e.series_id "
        "LEFT JOIN manga.wd_pivot p ON p.qid = e.qid "
        "WHERE e.cas = 'review_sans_signal' ORDER BY e.series_id"
    )
    ecrire_csv(
        dossier / "sans_signal.csv",
        ["series_id", "titre_ms", "qid", "label_wd", "annee_ms", "annee_wd"],
        cur.fetchall(),
    )

    (dossier / "entonnoir.md").write_text(
        texte_entonnoir(mesures, calib), encoding="utf-8"
    )


def texte_entonnoir(m: dict, calib: dict) -> str:
    cas = m["cas"]
    auto = sum(cas.get(c, 0) for c in CAS_AUTO)
    review = sum(cas.get(c, 0) for c in CAS_REVIEW)
    basse, haute = calib["fenetre"]
    lignes = [
        "# Entonnoir de l'étage 1 (jointure exacte MS × Wikidata)",
        "",
        f"Fenêtre d'année calibrée : **[{basse:+d}, {haute:+d}]** "
        f"({calib['couverture']:.1f} % des paires du pont).",
        "",
        f"- périmètre d'entrée (sans décision courante) : **{m['perimetre']}**",
        f"- séries avec ≥1 candidat par jointure exacte : **"
        f"{m['series_avec_candidat']}**",
        f"- paires (série, qid) candidates : **{m['candidats']}**",
        f"  - séries à candidat unique : {m['uniques']}",
        f"  - séries multi-candidats : {m['multis']}",
        "",
        "## Cases de la matrice",
        "",
        "| Cas | Décision | Séries |",
        "|---|---|---:|",
    ]
    libelles = {
        "auto_unique_auteur": ("candidat unique + auteur concordant", "AUTO 0.97"),
        "auto_multi_auteur": ("multi départagé par l'auteur", "AUTO 0.95"),
        "auto_unique_annee": ("unique + auteur incomparable + année", "AUTO 0.93"),
        "review_sans_signal": ("unique, AUCUN signal", "review « sans signal »"),
        "review_auteur_discordant": ("unique + auteur discordant", "review"),
        "review_annee_discordante": ("année discordante", "review"),
        "review_multi_annee_seule": ("multi, année seule", "review"),
        "review_ambiguite": ("ambiguïté persistante", "review"),
        "review_collision_qid": ("plusieurs séries → même QID", "review"),
        "review_collision_id": ("mal_id/anilist_id déjà pris", "review"),
    }
    for cle, (libelle, decision) in libelles.items():
        lignes.append(f"| {libelle} | {decision} | {cas.get(cle, 0)} |")
    lignes += [
        "",
        f"**AUTO total : {auto}** · **needs_review total : {review}**",
        "",
        f"La case « sans signal » ({cas.get('review_sans_signal', 0)}) est isolée "
        "dans `sans_signal.csv` : aucune politique n'est décidée ici.",
        "",
    ]
    return "\n".join(lignes)


def afficher(m: dict, calib: dict) -> None:
    for ligne in texte_entonnoir(m, calib).splitlines():
        typer.echo(ligne)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@app.command()
def construire(
    dry_run: bool = typer.Option(  # noqa: B008
        False, help="Exécute puis ROLLBACK : rien n'est écrit en base."
    ),
    rapport_dir: str = typer.Option(  # noqa: B008
        None, help="Dossier des livrables (défaut : data/rapports/etage1/<ts>)."
    ),
) -> None:
    """Calibre la fenêtre, applique la matrice, journalise et remplit."""
    horodatage = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dossier = Path(rapport_dir) if rapport_dir else RAPPORTS / horodatage
    debut = time.monotonic()

    with psycopg.connect(dsn()) as connexion:
        with connexion.cursor() as cur:
            verifier_prerequis(cur)
            perimetre = compter_perimetre(cur)

            calib = calibrer(ecarts_du_pont(cur))
            basse, haute = calib["fenetre"]
            typer.echo(
                f"→ fenêtre calibrée [{basse:+d}, {haute:+d}] sur {calib['n']} "
                f"paires du pont ({calib['couverture']:.1f} % couvertes)"
            )

            cur.execute(
                "CREATE TEMP TABLE etage1_param "
                "(borne_basse int, borne_haute int) ON COMMIT DROP"
            )
            cur.execute("INSERT INTO etage1_param VALUES (%s, %s)", (basse, haute))
            cur.execute(
                "CREATE TEMP TABLE ms_auteur_norm "
                "(series_id bigint, auteur_norm text) ON COMMIT DROP"
            )
            auteurs = auteurs_ms_normalises(cur)
            with cur.copy(
                "COPY ms_auteur_norm (series_id, auteur_norm) FROM STDIN"
            ) as copie:
                for ligne in auteurs:
                    copie.write_row(ligne)
            cur.execute("CREATE INDEX ON ms_auteur_norm (series_id)")
            cur.execute("CREATE INDEX ON ms_auteur_norm (auteur_norm)")
            cur.execute("ANALYZE ms_auteur_norm")
            typer.echo(f"→ {len(auteurs)} noms d'auteurs MS normalisés")

            cur.execute(ETAGE1_SQL.read_text(encoding="utf-8"))
            mesures = mesurer(cur, perimetre)
            ecrire_livrables(cur, dossier, calib, mesures)

        if dry_run:
            connexion.rollback()
        else:
            connexion.commit()

    afficher(mesures, calib)
    typer.echo(f"Livrables : {dossier}")
    if dry_run:
        typer.echo("⚠ DRY-RUN : transaction annulée, aucune écriture en base.")
    typer.echo(f"Terminé en {time.monotonic() - debut:.1f} s.")


def main() -> int:
    try:
        app()
    except ErreurEtage1 as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
