"""Étage 2 de la cascade : jointure exacte multi-formes MS × référentiel Kitsu.

    uv run python -m identity.etage2_kitsu               # exécute et commit
    uv run python -m identity.etage2_kitsu --dry-run     # exécute, rollback

Deux phases, une transaction :

  PHASE 1 — CALIBRATION (dette 22.4). La fenêtre d'année n'est pas décrétée :
  elle est mesurée sur les 1 689 identités du pont, vérité indépendante des
  dates, et elle est EMPIRIQUE SANS PLANCHER. L'étage 1 imposait un plancher
  [-1, +8] hérité de la prémisse « la VF suit l'original de plusieurs années » ;
  cette prémisse est RÉFUTÉE (médiane d'écart 0), le plancher rendait donc la
  fenêtre plus permissive que les données ne l'exigent.

  PHASE 2 — DÉCISION. Candidats par égalité de forme normalisée, puis matrice
  figée sur trois signaux : le kitsu_id historique de Manga Sanctuary (deux
  sources indépendantes quand une forme le confirme), l'auteur (via
  kitsu_staff) et l'année (via kitsu_meta, disponible à 99,9 %). L'année
  CONFIRME, elle ne décide jamais seule ; une année discordante interdit l'auto.

ANNEXE R — non décisionnelle. Les needs_review présents À L'ENTRÉE (au premier
run : les 665 de l'étage 1) ne reçoivent AUCUNE décision ici — le journal est
append-only. Ils reçoivent un enrichissement de DOSSIER, calculé dans la même
transaction et rejouable, qui sera l'entrée de l'étage R.

Idempotent : une série déjà décidée (v_match_current) est sautée ; re-run = 0
écriture. La logique SQL vit dans sql/etage2_kitsu.sql.
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
ETAGE2_SQL = Path(__file__).resolve().parent / "sql" / "etage2_kitsu.sql"
RAPPORTS = MODULE / "data" / "rapports" / "etage2"

# Le snapshot de l'étage 1 : seule source des LIBELLÉS DE CAS de ses 665
# needs_review. Ces décisions ont été journalisées AVANT l'existence de la
# colonne `details` (migration 009) et le journal est append-only : on ne
# rétro-remplit pas. Les candidats, eux, sont recalculés en base.
SNAPSHOT_ETAGE1 = (
    MODULE / "data" / "rapports" / "etage1" / "20260718T202153Z" / "needs_review.csv"
)

CAS_AUTO = (
    "auto_k_historique_confirme",
    "auto_k_unique_auteur",
    "auto_k_multi_auteur",
    "auto_k_unique_annee",
)
CAS_REVIEW = (
    "review_k_historique_contredit",
    "review_k_sans_signal",
    "review_k_auteur_discordant",
    "review_k_annee_discordante",
    "review_k_multi_annee_seule",
    "review_k_ambiguite",
    "review_k_collision_kitsu",
    "review_k_collision_id",
    "review_k_qid_divergent",
)

LIBELLES = {
    "auto_k_historique_confirme": (
        "kitsu_id historique confirmé par ≥1 forme",
        "AUTO 0.96",
    ),
    "auto_k_unique_auteur": ("candidat unique + auteur concordant", "AUTO 0.95"),
    "auto_k_multi_auteur": ("multi départagé par l'auteur", "AUTO 0.93"),
    "auto_k_unique_annee": ("unique + auteur incomparable + année", "AUTO 0.90"),
    "review_k_historique_contredit": ("kitsu_id historique CONTREDIT", "review"),
    "review_k_sans_signal": ("unique, AUCUN signal", "review « sans signal K »"),
    "review_k_auteur_discordant": ("unique + auteur discordant", "review"),
    "review_k_annee_discordante": ("année discordante", "review"),
    "review_k_multi_annee_seule": ("multi, année seule", "review"),
    "review_k_ambiguite": ("ambiguïté persistante", "review"),
    "review_k_collision_kitsu": ("plusieurs séries → même kitsu_id", "review"),
    "review_k_collision_id": ("qid/mal_id/anilist_id déjà pris", "review"),
    "review_k_qid_divergent": ("chemins QID divergents", "review"),
}

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurEtage2(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurEtage2(
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
        "manga.ms_kitsu_map",
        "manga.ms_kitsu_ambiguous",
        "manga.kitsu_formes",
        "manga.kitsu_mappings",
        "manga.kitsu_staff",
        "manga.kitsu_meta",
        "manga.wd_pivot",
    ):
        cur.execute("SELECT to_regclass(%s)", (objet,))
        if cur.fetchone()[0] is None:
            manquants.append(objet)
    if manquants:
        raise ErreurEtage2(
            "Schéma incomplet, migrations manquantes — STOP. Absents : "
            + ", ".join(manquants)
        )
    # Les méthodes doivent exister dans le CHECK : pas de migration sauvage.
    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid='manga.match_decision'::regclass AND contype='c' "
        "AND conname LIKE %s",
        ("%method%",),
    )
    ligne = cur.fetchone()
    contrainte = ligne[0] if ligne else ""
    for methode in ("'exact_kitsu'", "'exact_kitsu_author'"):
        if methode not in contrainte:
            raise ErreurEtage2(
                f"La méthode {methode} n'est pas autorisée par le CHECK de "
                "match_decision — la migration 009 n'est pas appliquée. STOP, "
                "aucune migration n'est faite ici."
            )
    # La colonne details doit exister : chaque décision doit porter sa case.
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema='manga' "
        "AND table_name='match_decision' AND column_name='details'"
    )
    if cur.fetchone() is None:
        raise ErreurEtage2(
            "match_decision.details est absente — migration 009 non appliquée. STOP."
        )


# --------------------------------------------------------------------------- #
# PHASE 1 — calibration de la fenêtre d'année, SANS PLANCHER (dette 22.4)
# --------------------------------------------------------------------------- #
def ecarts_du_pont(cur) -> list[int]:
    """(annee_ms - annee_kitsu) sur les identités sûres du pont.

    Le pont (étage 0) a rapproché par pures jointures d'identifiants : ses
    1 689 identités sont une vérité INDÉPENDANTE des dates, donc un étalon
    légitime pour calibrer un signal de date.
    """
    cur.execute(
        "SELECT s.series_year - km.annee "
        "FROM manga.work_identity w "
        "JOIN manga.ms_series_enriched s ON s.series_id = w.series_id "
        "JOIN manga.kitsu_meta km ON km.kitsu_id = w.kitsu_id::bigint "
        "WHERE w.kitsu_id IS NOT NULL "
        "  AND s.series_year IS NOT NULL AND km.annee IS NOT NULL"
    )
    return [e for (e,) in cur.fetchall()]


def calibrer(ecarts: list[int]) -> dict:
    """Fenêtre [p5, p95] EMPIRIQUE, sans plancher. Renvoie stats + fenêtre."""
    if not ecarts:
        raise ErreurEtage2(
            "Aucune paire d'années exploitable sur le pont : la fenêtre ne peut "
            "pas être calibrée — STOP."
        )
    tries = sorted(ecarts)

    def pct(p: float) -> float:
        return statistics.quantiles(tries, n=100, method="inclusive")[int(p) - 1]

    p5, p95 = pct(5), pct(95)
    # Arrondi vers l'extérieur, et RIEN d'autre : pas de plancher. La prémisse
    # « VF-après-Japon » qui justifiait [-1, +8] à l'étage 1 est réfutée.
    basse, haute = int(p5 // 1), int(-(-p95 // 1))
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
        "histogramme": {v: sum(1 for e in tries if e == v) for v in range(-5, 11)},
    }


def texte_calibration(c: dict) -> str:
    basse, haute = c["fenetre"]
    lignes = [
        "# Calibration de la fenêtre d'année MS ↔ Kitsu (étage 2)",
        "",
        "Mesurée sur les identités du pont Kitsu (étage 0), vérité indépendante",
        "des dates : seules les paires dont les DEUX années existent comptent.",
        "",
        "**Fenêtre EMPIRIQUE, SANS PLANCHER** — dette 22.4. L'étage 1 imposait un",
        "plancher [-1, +8] hérité de la prémisse « la VF suit l'original de",
        "plusieurs années ». Cette prémisse est réfutée (médiane d'écart 0) : le",
        "plancher rendait la fenêtre plus permissive que les données ne l'exigent.",
        "",
        f"- paires exploitables : **{c['n']}**",
        f"- min={c['min']} · p5={c['p5']:.0f} · p25={c['p25']:.0f} · "
        f"médiane={c['med']:.0f} · p75={c['p75']:.0f} · p95={c['p95']:.0f} · "
        f"max={c['max']}",
        "",
        f"**Fenêtre retenue : [{basse:+d}, {haute:+d}]** "
        f"— couvre {c['couverts']}/{c['n']} paires ({c['couverture']:.1f} %).",
        "",
        "## Histogramme de (année MS − année Kitsu)",
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
# Mesure
# --------------------------------------------------------------------------- #
def compter_perimetre(cur) -> int:
    """Séries sans décision courante. À mesurer AVANT d'écrire le journal."""
    cur.execute(
        "SELECT count(*) FROM manga.ms_series_enriched s WHERE NOT EXISTS "
        "(SELECT 1 FROM manga.v_match_current v WHERE v.series_id=s.series_id)"
    )
    return cur.fetchone()[0]


def figer_needs_review_etage1(cur) -> int:
    """Fige le périmètre de l'ANNEXE R AVANT que l'étage 2 n'écrive.

    L'annexe éclaire les dossiers DÉJÀ en needs_review à l'entrée — au premier
    run, les 665 de l'étage 1. Or l'étage 2 en produit lui-même : si le
    périmètre était lu après ses INSERT, l'annexe traiterait les deux étages.
    C'est un piège vérifié en dry-run (1 711 dossiers au lieu de 665), pas une
    précaution théorique.

    Au re-run, le périmètre figé vaut légitimement 1 711 : l'annexe éclaire
    alors toute la file consolidée, ce qui est précisément ce dont l'étage R a
    besoin. Le périmètre est donc « les needs_review à l'entrée », pas « ceux
    de l'étage 1 » — la nuance compte dès le deuxième run.
    """
    cur.execute(
        "CREATE TEMP TABLE nr_etage1 ON COMMIT DROP AS "
        "SELECT v.series_id FROM manga.v_match_current v "
        "WHERE v.status = 'needs_review'"
    )
    cur.execute("CREATE INDEX ON nr_etage1 (series_id)")
    cur.execute("SELECT count(*) FROM nr_etage1")
    return cur.fetchone()[0]


def compter_0bis(cur) -> dict[str, int]:
    """La population « étage 0bis » : périmètre portant un kitsu_id historique
    non ambigu. Chiffrée AVANT décision, pour mesurer ensuite sa conversion."""
    cur.execute(
        "WITH perim AS ("
        "  SELECT s.series_id FROM manga.ms_series_enriched s WHERE NOT EXISTS "
        "  (SELECT 1 FROM manga.v_match_current v WHERE v.series_id=s.series_id)),"
        " mk AS ("
        "  SELECT p.series_id, m.kitsu_id FROM perim p "
        "  JOIN manga.ms_kitsu_map m ON m.series_id = p.series_id "
        "  WHERE m.kitsu_id IS NOT NULL AND NOT EXISTS "
        "    (SELECT 1 FROM manga.ms_kitsu_ambiguous a WHERE a.series_id=p.series_id))"
        " SELECT count(*),"
        "   count(*) FILTER (WHERE EXISTS ("
        "     SELECT 1 FROM manga.kitsu_mappings km JOIN manga.wd_pivot wp "
        "       ON wp.mal_id = km.external_id "
        "     WHERE km.kitsu_id = mk.kitsu_id "
        "       AND km.external_site='myanimelist/manga'))"
        " FROM mk"
    )
    total, avec_qid = cur.fetchone()
    return {"population": total, "atteignant_qid": avec_qid}


def mesurer(cur, perimetre: int, pop_0bis: dict) -> dict:
    def scalaire(sql: str) -> int:
        cur.execute(sql)
        return cur.fetchone()[0]

    mesures = {
        "perimetre": perimetre,
        "population_0bis": pop_0bis["population"],
        "0bis_atteignant_qid": pop_0bis["atteignant_qid"],
        "candidats": scalaire("SELECT count(*) FROM etage2_candidat"),
        "series_avec_candidat": scalaire(
            "SELECT count(DISTINCT series_id) FROM etage2_candidat"
        ),
    }
    cur.execute("SELECT cas, count(*) FROM etage2_serie GROUP BY cas")
    mesures["cas"] = dict(cur.fetchall())
    cur.execute(
        "SELECT count(*) FILTER (WHERE n_cand=1), count(*) FILTER (WHERE n_cand>1) "
        "FROM etage2_serie"
    )
    mesures["uniques"], mesures["multis"] = cur.fetchone()
    # Identités complètes vs partielles parmi les AUTO : l'étage 0bis réalisé.
    cur.execute(
        "SELECT count(*) FILTER (WHERE i.qid IS NOT NULL), "
        "       count(*) FILTER (WHERE i.qid IS NULL) "
        "FROM etage2_serie s JOIN etage2_ident i ON i.series_id = s.series_id "
        "WHERE s.cas LIKE 'auto%'"
    )
    mesures["auto_complet"], mesures["auto_partiel"] = cur.fetchone()
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


# --------------------------------------------------------------------------- #
# ANNEXE R — enrichissement de dossier, AUCUNE écriture au journal
# --------------------------------------------------------------------------- #
SQL_ANNEXE_R = """
CREATE TEMP TABLE annexe_r ON COMMIT DROP AS
WITH nr AS (
    -- Le périmètre FIGÉ AVANT toute écriture de l'étage 2 (cf.
    -- figer_needs_review_etage1). Lire v_match_current ICI serait un piège :
    -- à ce point de la transaction, l'étage 2 y a déjà inséré ses propres
    -- needs_review, et l'annexe ratisserait les deux étages au lieu du seul
    -- étage 1 — mesuré : 1 711 dossiers au lieu de 665.
    SELECT series_id FROM nr_etage1
),
-- Candidats WIKIDATA, RECALCULÉS : les décisions needs_review de l'étage 1
-- portent wikidata_qid NULL (l'étage n'avait rien conclu), le candidat
-- douteux n'est donc pas dans le journal. On le reconstruit par la même
-- jointure exacte, sur les mêmes colonnes certifiées.
cand_wd AS (
    SELECT DISTINCT nr.series_id, wf.qid
    FROM nr
    JOIN manga.ms_formes mf ON mf.series_id = nr.series_id
     AND mf.forme_norm <> ''
    JOIN manga.wd_formes wf ON wf.forme_norm = mf.forme_norm
),
-- Candidats KITSU par la même mécanique de formes.
cand_k AS (
    SELECT DISTINCT nr.series_id, kf.kitsu_id
    FROM nr
    JOIN manga.ms_formes mf ON mf.series_id = nr.series_id
     AND mf.forme_norm <> ''
    JOIN manga.kitsu_formes kf ON kf.forme_norm = mf.forme_norm
),
-- Le QID que le chemin Kitsu atteint, via mal_id.
k_qid AS (
    SELECT DISTINCT c.series_id, c.kitsu_id, wp.qid
    FROM cand_k c
    JOIN manga.kitsu_mappings km
      ON km.kitsu_id = c.kitsu_id AND km.external_site = 'myanimelist/manga'
    JOIN manga.wd_pivot wp ON wp.mal_id = km.external_id
),
agg AS (
    SELECT nr.series_id,
           (SELECT count(*) FROM cand_wd w WHERE w.series_id = nr.series_id)
               AS n_cand_wd,
           (SELECT count(DISTINCT k.kitsu_id) FROM cand_k k
            WHERE k.series_id = nr.series_id) AS n_cand_kitsu,
           (SELECT count(DISTINCT q.qid) FROM k_qid q
            WHERE q.series_id = nr.series_id) AS n_qid_kitsu,
           (SELECT min(q.qid) FROM k_qid q WHERE q.series_id = nr.series_id)
               AS qid_kitsu_unique
    FROM nr
)
SELECT a.series_id,
       a.n_cand_wd,
       a.n_cand_kitsu,
       a.n_qid_kitsu,
       CASE WHEN a.n_qid_kitsu = 1 THEN a.qid_kitsu_unique END AS qid_kitsu,
       -- Le verdict qui intéresse l'étage R : le chemin Kitsu, indépendant,
       -- confirme-t-il un candidat Wikidata du dossier douteux ?
       CASE
           WHEN a.n_qid_kitsu <> 1 THEN 'pas_de_qid_unique_kitsu'
           WHEN EXISTS (SELECT 1 FROM cand_wd w
                        WHERE w.series_id = a.series_id
                          AND w.qid = a.qid_kitsu_unique) THEN 'CONFIRME'
           ELSE 'CONTREDIT'
       END AS verdict_kitsu
FROM agg a;
"""


def ecrire_annexe_r(cur, dossier: Path) -> dict:
    """Enrichit les dossiers needs_review de l'étage 1. N'ÉCRIT RIEN au journal.

    Ces séries gardent leur décision : le journal est append-only et un
    enrichissement n'est pas une décision. L'étage R les recevra pré-éclairés.
    """
    cur.execute(SQL_ANNEXE_R)
    cur.execute("SELECT verdict_kitsu, count(*) FROM annexe_r GROUP BY 1")
    verdicts = dict(cur.fetchall())

    cur.execute(
        "SELECT a.series_id, s.series_title, "
        "  concat_ws(' / ', s.series_scenariste, s.series_dessinateur), "
        "  s.series_year, a.n_cand_wd, a.n_cand_kitsu, a.n_qid_kitsu, "
        "  a.qid_kitsu, p.label_principal, p.annee, a.verdict_kitsu "
        "FROM annexe_r a "
        "JOIN manga.ms_series_enriched s ON s.series_id = a.series_id "
        "LEFT JOIN manga.wd_pivot p ON p.qid = a.qid_kitsu "
        "ORDER BY a.verdict_kitsu, a.series_id"
    )
    total = ecrire_csv(
        dossier / "enrichissement_r.csv",
        [
            "series_id",
            "titre_ms",
            "auteurs_ms",
            "annee_ms",
            "n_cand_wd",
            "n_cand_kitsu",
            "n_qid_kitsu",
            "qid_kitsu",
            "label_wd_du_qid_kitsu",
            "annee_wd_du_qid_kitsu",
            "verdict_kitsu",
        ],
        cur.fetchall(),
    )
    return {"lignes": total, "verdicts": verdicts}


# --------------------------------------------------------------------------- #
# Livrables
# --------------------------------------------------------------------------- #
def cas_etage1_du_snapshot() -> dict[int, str]:
    """Les libellés de cas des 665 needs_review de l'étage 1.

    Ils ne sont PAS en base : ces décisions ont été journalisées avant que la
    colonne `details` n'existe (migration 009), et le journal est append-only —
    on ne rétro-remplit pas. Le snapshot daté de l'étage 1 en est donc la seule
    source. Son absence dégrade le livrable sans l'invalider.
    """
    if not SNAPSHOT_ETAGE1.is_file():
        return {}
    with SNAPSHOT_ETAGE1.open(encoding="utf-8") as fh:
        return {int(r["series_id"]): r["cas"] for r in csv.DictReader(fh)}


def ecrire_livrables(cur, dossier: Path, calib: dict, mesures: dict) -> dict:
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "calibration_annees_kitsu.md").write_text(
        texte_calibration(calib), encoding="utf-8"
    )

    # Échantillon AUTO — la matière de l'étage R et de l'échantillon C3.
    cur.execute(
        "SELECT e.series_id, s.series_title, "
        "  concat_ws(' / ', s.series_scenariste, s.series_dessinateur), "
        "  s.series_year, e.kitsu_id, "
        "  (SELECT string_agg(DISTINCT ks.personne, ' / ') "
        "   FROM manga.kitsu_staff ks WHERE ks.kitsu_id = e.kitsu_id), "
        "  km.annee, i.qid, "
        "  CASE e.cas WHEN 'auto_k_historique_confirme' THEN 0.96 "
        "             WHEN 'auto_k_unique_auteur' THEN 0.95 "
        "             WHEN 'auto_k_multi_auteur' THEN 0.93 ELSE 0.90 END, e.cas "
        "FROM etage2_serie e "
        "JOIN manga.ms_series_enriched s ON s.series_id = e.series_id "
        "LEFT JOIN manga.kitsu_meta km ON km.kitsu_id = e.kitsu_id "
        "LEFT JOIN etage2_ident i ON i.series_id = e.series_id "
        "WHERE e.cas LIKE 'auto%' ORDER BY md5(e.series_id::text) LIMIT 100"
    )
    ecrire_csv(
        dossier / "echantillon_auto.csv",
        [
            "series_id",
            "titre_ms",
            "auteurs_ms",
            "annee_ms",
            "kitsu_id",
            "auteurs_kitsu",
            "annee_kitsu",
            "qid",
            "score",
            "cas",
        ],
        cur.fetchall(),
    )

    # La case « sans signal K », isolée : politique non tranchée ici.
    cur.execute(
        "SELECT e.series_id, s.series_title, e.kitsu_id, km.annee, s.series_year "
        "FROM etage2_serie e "
        "JOIN manga.ms_series_enriched s ON s.series_id = e.series_id "
        "LEFT JOIN manga.kitsu_meta km ON km.kitsu_id = e.kitsu_id "
        "WHERE e.cas = 'review_k_sans_signal' ORDER BY e.series_id"
    )
    ecrire_csv(
        dossier / "sans_signal_k.csv",
        ["series_id", "titre_ms", "kitsu_id", "annee_kitsu", "annee_ms"],
        cur.fetchall(),
    )

    annexe = ecrire_annexe_r(cur, dossier)

    # needs_review CONSOLIDÉ — l'entrée unique de l'étage R.
    cas_e1 = cas_etage1_du_snapshot()
    cur.execute(
        "SELECT e.series_id, s.series_title, "
        "  concat_ws(' / ', s.series_scenariste, s.series_dessinateur), "
        "  s.series_year, e.cas, e.n_cand, "
        "  (SELECT string_agg(DISTINCT c.kitsu_id::text, ' | ') "
        "   FROM etage2_candidat c WHERE c.series_id = e.series_id) "
        "FROM etage2_serie e "
        "JOIN manga.ms_series_enriched s ON s.series_id = e.series_id "
        "WHERE e.cas LIKE 'review%' ORDER BY e.series_id"
    )
    lignes_e2 = [
        (sid, "etage2", titre, auteurs, annee, cas, n, cand, "")
        for sid, titre, auteurs, annee, cas, n, cand in cur.fetchall()
    ]

    cur.execute(
        "SELECT a.series_id, s.series_title, "
        "  concat_ws(' / ', s.series_scenariste, s.series_dessinateur), "
        "  s.series_year, a.n_cand_wd, a.qid_kitsu, a.verdict_kitsu "
        "FROM annexe_r a "
        "JOIN manga.ms_series_enriched s ON s.series_id = a.series_id "
        "ORDER BY a.series_id"
    )
    lignes_e1 = [
        (
            sid,
            "etage1",
            titre,
            auteurs,
            annee,
            cas_e1.get(sid, ""),
            n_wd,
            qid_k or "",
            verdict,
        )
        for sid, titre, auteurs, annee, n_wd, qid_k, verdict in cur.fetchall()
    ]

    total = ecrire_csv(
        dossier / "needs_review_consolide.csv",
        [
            "series_id",
            "origine",
            "titre_ms",
            "auteurs_ms",
            "annee_ms",
            "cas",
            "n_candidats",
            "candidats_ou_qid_kitsu",
            "verdict_kitsu_annexe_r",
        ],
        sorted(lignes_e1 + lignes_e2),
    )
    return {
        "annexe": annexe,
        "consolide": total,
        "cas_e1_resolus": len(cas_e1),
    }


def texte_entonnoir(m: dict, calib: dict, livrables: dict) -> str:
    cas = m["cas"]
    auto = sum(cas.get(c, 0) for c in CAS_AUTO)
    review = sum(cas.get(c, 0) for c in CAS_REVIEW)
    basse, haute = calib["fenetre"]
    verdicts = livrables["annexe"]["verdicts"]
    lignes = [
        "# Entonnoir de l'étage 2 (jointure exacte MS × Kitsu)",
        "",
        f"Fenêtre d'année calibrée : **[{basse:+d}, {haute:+d}]** "
        f"— empirique, SANS plancher (dette 22.4), "
        f"{calib['couverture']:.1f} % des {calib['n']} paires du pont.",
        "",
        f"- périmètre d'entrée (sans décision courante) : **{m['perimetre']}**",
        f"- séries avec ≥1 candidat par jointure exacte : **"
        f"{m['series_avec_candidat']}**",
        f"- paires (série, kitsu_id) candidates : **{m['candidats']}**",
        f"  - séries à candidat unique : {m['uniques']}",
        f"  - séries multi-candidats : {m['multis']}",
        "",
        "## Cases de la matrice",
        "",
        "| Cas | Décision | Séries |",
        "|---|---|---:|",
    ]
    for cle, (libelle, decision) in LIBELLES.items():
        lignes.append(f"| {libelle} | {decision} | {cas.get(cle, 0)} |")
    lignes += [
        "",
        f"**AUTO total : {auto}** · **needs_review total : {review}**",
        "",
        "## Population « étage 0bis » (kitsu_id historique non ambigu)",
        "",
        f"- au périmètre d'entrée : **{m['population_0bis']}** séries",
        f"  - dont atteignant un QID par la voie MAL : {m['0bis_atteignant_qid']}",
        f"- identités AUTO complètes (avec QID) : **{m['auto_complet']}**",
        f"- identités AUTO **partielles** (kitsu_id sans QID) : "
        f"**{m['auto_partiel']}**",
        "",
        "Une identité partielle est un résultat LÉGITIME : Wikidata ne couvre que",
        "la tête du catalogue. Les colonnes de work_identity sont indépendantes.",
        "",
        "## ANNEXE R — enrichissement des dossiers déjà en needs_review",
        "",
        "Non décisionnel : **aucune écriture au journal**. Ces séries gardent",
        "leur décision `needs_review` ; elles reçoivent un éclairage.",
        "",
        "| Verdict du chemin Kitsu | Dossiers |",
        "|---|---:|",
    ]
    for verdict, nombre in sorted(verdicts.items(), key=lambda x: -x[1]):
        lignes.append(f"| {verdict} | {nombre} |")
    lignes += [
        "",
        f"`needs_review_consolide.csv` : **{livrables['consolide']}** dossiers "
        "(étage 1 + étage 2) — l'entrée unique de l'étage R.",
        "",
        f"La case « sans signal K » ({cas.get('review_k_sans_signal', 0)}) est "
        "isolée dans `sans_signal_k.csv` : aucune politique n'est décidée ici.",
        "",
    ]
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@app.command()
def construire(
    dry_run: bool = typer.Option(  # noqa: B008
        False, help="Exécute puis ROLLBACK : rien n'est écrit en base."
    ),
    rapport_dir: str = typer.Option(  # noqa: B008
        None, help="Dossier des livrables (défaut : data/rapports/etage2/<ts>)."
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
            pop_0bis = compter_0bis(cur)
            # Figé AVANT toute écriture — l'ordre est le garde-fou.
            nr_etage1 = figer_needs_review_etage1(cur)
            typer.echo(
                f"→ ANNEXE R : {nr_etage1} dossiers needs_review à l'entrée, "
                "périmètre figé avant écriture"
            )

            calib = calibrer(ecarts_du_pont(cur))
            basse, haute = calib["fenetre"]
            typer.echo(
                f"→ fenêtre calibrée [{basse:+d}, {haute:+d}] sur {calib['n']} "
                f"paires du pont ({calib['couverture']:.1f} % couvertes) "
                "— empirique, sans plancher"
            )

            cur.execute(
                "CREATE TEMP TABLE etage2_param "
                "(borne_basse int, borne_haute int) ON COMMIT DROP"
            )
            cur.execute("INSERT INTO etage2_param VALUES (%s, %s)", (basse, haute))
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

            cur.execute(ETAGE2_SQL.read_text(encoding="utf-8"))
            mesures = mesurer(cur, perimetre, pop_0bis)
            livrables = ecrire_livrables(cur, dossier, calib, mesures)

            (dossier / "entonnoir.md").write_text(
                texte_entonnoir(mesures, calib, livrables), encoding="utf-8"
            )

        if dry_run:
            connexion.rollback()
        else:
            connexion.commit()

    for ligne in texte_entonnoir(mesures, calib, livrables).splitlines():
        typer.echo(ligne)
    typer.echo(f"Livrables : {dossier}")
    if dry_run:
        typer.echo("⚠ DRY-RUN : transaction annulée, aucune écriture en base.")
    typer.echo(f"Terminé en {time.monotonic() - debut:.1f} s.")


def main() -> int:
    try:
        app()
    except ErreurEtage2 as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
