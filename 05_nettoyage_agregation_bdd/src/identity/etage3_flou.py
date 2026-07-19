"""Étage 3 de la cascade : rapprochement flou pg_trgm — dernier étage mécanique.

    uv run python -m identity.etage3_flou               # exécute et commit
    uv run python -m identity.etage3_flou --dry-run     # exécute, rollback

L'ÉTAGE NE PRODUIT AUCUN AUTO. Tout candidat flou part en `needs_review` avec
son dossier. Le flou PROPOSE, il ne décide jamais : sa raison d'être est que les
orphelins arrivent à l'étage R AVEC des candidats plutôt que sans rien.

  PHASE 1 — CALIBRATION DU SEUIL. 0.85 est une décision humaine ; la
  calibration la CONTRÔLE, elle ne la remplace pas. Sur 500 identités sûres
  tirées du cumul auto, on mesure la similarité de leur meilleure forme croisée.

  ⚠️ Le résultat se lit STRATIFIÉ PAR MÉTHODE, et c'est essentiel. Les méthodes
  `exact*` ont été appariées SUR forme_norm exacte : leur similarité vaut 1.0
  par construction, elles ne peuvent rien valider — les inclure dans une moyenne
  globale fabriquerait un « 100 % » circulaire. Seul le PONT (`kitsu_bridge`),
  apparié par jointures d'identifiants et jamais par titre, constitue une
  population indépendante. Le verdict se fonde sur lui seul.

  PHASE 2 — CANDIDATS. Similarité trigramme forme_norm MS × (wd_formes ∪
  kitsu_formes) via l'opérateur indexable `%`, TOP 3 par série, dédupliqués par
  œuvre-cible. Signaux auteur/année calculés mais purement informatifs.

Les séries sans candidat au-dessus du seuil ne reçoivent AUCUNE décision : elles
restent sans-décision-courante et sont comptées comme « orphelines de cascade ».
Leur sort est une décision produit, pas un needs_review de plus.

Idempotent : une série déjà décidée est sautée ; re-run = 0 écriture.
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

from identity.wikidata_dump import normaliser

MODULE = Path(__file__).resolve().parents[2]
ETAGE3_SQL = Path(__file__).resolve().parent / "sql" / "etage3_flou.sql"
RAPPORTS = MODULE / "data" / "rapports" / "etage3"

SEUIL = 0.85
PERIMETRE_ATTENDU = 5525
TAILLE_ECHANTILLON = 500

# Les méthodes appariées SUR forme_norm exacte : leur similarité vaut 1.0 par
# construction. Circulaires pour une calibration de seuil flou.
METHODES_CIRCULAIRES = ("exact", "exact_author", "exact_kitsu", "exact_kitsu_author")
# La seule population indépendante des titres : le pont, apparié par identifiants.
METHODE_TEMOIN = "kitsu_bridge"

# La fenêtre d'année de l'ÉTAGE 1, nécessaire pour re-dériver ses libellés de
# cas. Figée telle qu'elle était à son run (plancher inclus) — on re-dérive ce
# qui a été décidé, pas ce qu'on déciderait aujourd'hui.
FENETRE_ETAGE1 = (-1, 8)

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurEtage3(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurEtage3(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'"
        )
    return url


def verifier_prerequis(cur) -> None:
    """Le schéma doit préexister : cet étage ne crée aucune structure et
    n'attend AUCUNE migration."""
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
        "manga.kitsu_formes",
        "manga.kitsu_staff",
        "manga.kitsu_meta",
    ):
        cur.execute("SELECT to_regclass(%s)", (objet,))
        if cur.fetchone()[0] is None:
            manquants.append(objet)
    if manquants:
        raise ErreurEtage3(
            "Schéma incomplet, migrations manquantes — STOP. Absents : "
            + ", ".join(manquants)
        )

    # Décision figée n°6 : 'trgm' doit être au CHECK (présent depuis 001).
    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid='manga.match_decision'::regclass AND contype='c' "
        "AND conname LIKE %s",
        ("%method%",),
    )
    ligne = cur.fetchone()
    if "'trgm'" not in (ligne[0] if ligne else ""):
        raise ErreurEtage3(
            "La méthode 'trgm' n'est pas autorisée par le CHECK de "
            "match_decision — STOP, aucune migration n'est faite ici."
        )

    # Les index GIN trigramme sont la condition de faisabilité, pas un confort.
    cur.execute(
        "SELECT count(DISTINCT tablename) FROM pg_indexes "
        "WHERE schemaname='manga' AND indexdef LIKE '%gin_trgm_ops%' "
        "AND tablename IN ('ms_formes','wd_formes','kitsu_formes')"
    )
    if cur.fetchone()[0] != 3:
        raise ErreurEtage3(
            "Les trois index GIN trigramme (ms_formes, wd_formes, kitsu_formes) "
            "ne sont pas tous présents : le rapprochement flou serait un produit "
            "cartésien de ~1,9 milliard de paires — STOP."
        )


def compter_perimetre(cur) -> int:
    cur.execute(
        "SELECT count(*) FROM manga.ms_series_enriched s WHERE NOT EXISTS "
        "(SELECT 1 FROM manga.v_match_current v WHERE v.series_id=s.series_id)"
    )
    return cur.fetchone()[0]


def figer_perimetre(cur) -> int:
    """Fige le périmètre AVANT toute écriture (leçon de l'annexe R).

    Une fois les décisions insérées, `v_match_current` les inclut : tout compte
    postérieur mesurerait le périmètre de sortie, pas celui d'entrée.
    """
    cur.execute(
        "CREATE TEMP TABLE e3_perimetre ON COMMIT DROP AS "
        "SELECT s.series_id FROM manga.ms_series_enriched s WHERE NOT EXISTS "
        "(SELECT 1 FROM manga.v_match_current v WHERE v.series_id=s.series_id)"
    )
    cur.execute("CREATE INDEX ON e3_perimetre (series_id)")
    cur.execute("SELECT count(*) FROM e3_perimetre")
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# PHASE 1 — calibration du seuil
# --------------------------------------------------------------------------- #
SQL_CALIBRATION = """
WITH sures AS (
    SELECT v.series_id, v.method, w.wikidata_qid, w.kitsu_id
    FROM manga.v_match_current v
    JOIN manga.work_identity w ON w.series_id = v.series_id
    WHERE v.status = 'auto'
    ORDER BY md5(v.series_id::text)
    LIMIT %s
)
SELECT s.method,
       GREATEST(
           coalesce((SELECT max(similarity(mf.forme_norm, wf.forme_norm))
                     FROM manga.ms_formes mf, manga.wd_formes wf
                     WHERE mf.series_id = s.series_id
                       AND wf.qid = s.wikidata_qid), 0),
           coalesce((SELECT max(similarity(mf.forme_norm, kf.forme_norm))
                     FROM manga.ms_formes mf, manga.kitsu_formes kf
                     WHERE mf.series_id = s.series_id
                       AND kf.kitsu_id = s.kitsu_id::bigint), 0)
       ) AS sim_max
FROM sures s
"""


def calibrer_seuil(cur) -> dict:
    """Contrôle le seuil sur des identités sûres, STRATIFIÉ par méthode."""
    cur.execute(SQL_CALIBRATION, (TAILLE_ECHANTILLON,))
    lignes = cur.fetchall()
    if not lignes:
        raise ErreurEtage3("Aucune identité sûre pour calibrer le seuil — STOP.")

    par_methode: dict[str, list[float]] = {}
    for methode, sim in lignes:
        par_methode.setdefault(methode, []).append(float(sim))

    def stats(valeurs: list[float]) -> dict:
        n = len(valeurs)
        return {
            "n": n,
            "moyenne": sum(valeurs) / n,
            "min": min(valeurs),
            "exacts": sum(1 for v in valeurs if v >= 0.999),
            "au_seuil": sum(1 for v in valeurs if v >= SEUIL),
            "pct": 100 * sum(1 for v in valeurs if v >= SEUIL) / n,
        }

    detail = {m: stats(v) for m, v in par_methode.items()}
    temoin = detail.get(METHODE_TEMOIN)
    if temoin is None:
        raise ErreurEtage3(
            f"Aucune identité de méthode '{METHODE_TEMOIN}' dans l'échantillon : "
            "la seule population non circulaire manque, le seuil ne peut pas "
            "être contrôlé — STOP."
        )

    # Le verdict se fonde sur le TÉMOIN seul. Un seuil qui laisserait filer une
    # part notable d'identités vraies et indépendamment vérifiées serait une
    # décision humaine à reprendre, pas un paramètre à ajuster en passant.
    verdict_ok = temoin["pct"] >= 90.0
    return {
        "detail": detail,
        "temoin": temoin,
        "verdict_ok": verdict_ok,
        "circulaires": sum(
            d["n"] for m, d in detail.items() if m in METHODES_CIRCULAIRES
        ),
        "total": len(lignes),
    }


def texte_calibration(c: dict) -> str:
    lignes = [
        "# Calibration du seuil trigramme (étage 3)",
        "",
        f"Seuil contrôlé : **{SEUIL}** — c'est une **décision humaine**. La",
        "calibration la contrôle, elle ne la remplace pas.",
        "",
        f"Échantillon : **{c['total']}** identités sûres tirées du cumul auto.",
        "",
        "## ⚠️ Pourquoi le résultat se lit stratifié",
        "",
        f"**{c['circulaires']}** des identités de l'échantillon proviennent de",
        "méthodes `exact*`, appariées **sur `forme_norm` exacte**. Leur",
        "similarité vaut 1.0 **par construction** : elles ne peuvent rien",
        "valider. Les fondre dans une moyenne globale produirait un « 100 % »",
        "circulaire, c'est-à-dire un chiffre faux.",
        "",
        f"Seule la méthode **`{METHODE_TEMOIN}`** — le pont, apparié par",
        "jointures d'identifiants et **jamais par titre** — constitue une",
        "population indépendante. Le verdict se fonde sur elle seule.",
        "",
        "## Distribution par méthode",
        "",
        "| Méthode | n | moyenne | min | exacts (1.0) | ≥ seuil | % | rôle |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for methode, d in sorted(c["detail"].items(), key=lambda x: -x[1]["n"]):
        role = (
            "**TÉMOIN**"
            if methode == METHODE_TEMOIN
            else ("circulaire" if methode in METHODES_CIRCULAIRES else "—")
        )
        lignes.append(
            f"| `{methode}` | {d['n']} | {d['moyenne']:.3f} | {d['min']:.3f} | "
            f"{d['exacts']} | {d['au_seuil']} | {d['pct']:.1f} % | {role} |"
        )
    t = c["temoin"]
    lignes += [
        "",
        "## Verdict",
        "",
        f"Sur le témoin `{METHODE_TEMOIN}` : **{t['au_seuil']}/{t['n']} "
        f"({t['pct']:.1f} %)** des identités vraies et indépendamment vérifiées",
        f"atteignent le seuil {SEUIL}.",
        "",
        (
            f"✅ **Le seuil {SEUIL} est confirmé** — il capture la très grande "
            "majorité des identités vraies."
            if c["verdict_ok"]
            else f"⛔ **Le seuil {SEUIL} est CONTREDIT** par la calibration — STOP."
        ),
        "",
        "### Ce que cette mesure dit AUSSI, et qui vaut avertissement",
        "",
        f"Sur le témoin, {t['exacts']}/{t['n']} identités atteignent **1.0** :",
        "des identités vraies trouvées **sans regarder les titres** ont, dans",
        "leur immense majorité, une forme normalisée **strictement identique**",
        "de part et d'autre. Or les étages 1 et 2 ont déjà consommé toutes les",
        "correspondances exactes. Le périmètre de l'étage 3 est donc, par",
        "construction, le résidu où aucune forme ne coïncide — il faut en",
        "attendre un **rendement modeste**, et ce n'est pas un défaut du seuil.",
        "",
    ]
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
# PHASE 2 — auteurs MS normalisés (signal informatif)
# --------------------------------------------------------------------------- #
def auteurs_ms_normalises(cur) -> list[tuple[int, str]]:
    cur.execute(
        "SELECT s.series_id, s.series_scenariste, s.series_dessinateur "
        "FROM manga.ms_series_enriched s "
        "WHERE EXISTS (SELECT 1 FROM e3_perimetre p WHERE p.series_id=s.series_id)"
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
def mesurer(cur, perimetre: int, secondes_flou: float) -> dict:
    def scalaire(sql: str) -> int:
        cur.execute(sql)
        return cur.fetchone()[0]

    mesures = {
        "perimetre": perimetre,
        "secondes_flou": secondes_flou,
        "paires_brutes": scalaire("SELECT count(*) FROM e3_paire"),
        "candidats_dedup": scalaire("SELECT count(*) FROM e3_cand"),
        "avec_candidat": scalaire("SELECT count(DISTINCT series_id) FROM e3_cand"),
    }
    mesures["orphelines"] = perimetre - mesures["avec_candidat"]

    cur.execute(
        "SELECT CASE WHEN sim_max >= 0.95 THEN '>= 0.95' "
        "            WHEN sim_max >= 0.90 THEN '0.90-0.95' "
        "            ELSE '0.85-0.90' END AS tranche, count(DISTINCT series_id) "
        "FROM e3_top GROUP BY 1"
    )
    mesures["tranches"] = dict(cur.fetchall())

    cur.execute("SELECT cible_type, count(*) FROM e3_top WHERE rang = 1 GROUP BY 1")
    mesures["meilleur_par_source"] = dict(cur.fetchall())

    cur.execute("SELECT signal_auteur, count(*) FROM e3_top WHERE rang = 1 GROUP BY 1")
    mesures["auteur_du_meilleur"] = dict(cur.fetchall())
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
# Dérivation des libellés de cas de l'ÉTAGE 1 — depuis la BASE
# --------------------------------------------------------------------------- #
SQL_DERIVER_ETAGE1 = """
CREATE TEMP TABLE e3_cas_e1 ON COMMIT DROP AS
WITH nr AS (
    -- Les needs_review journalisés par l'étage 1 : méthodes 'exact' et
    -- 'exact_author', décisions ANTÉRIEURES à la colonne details.
    SELECT v.series_id FROM manga.v_match_current v
    JOIN manga.match_decision d ON d.decision_id = v.decision_id
    WHERE v.status = 'needs_review' AND d.method IN ('exact', 'exact_author')
),
cand AS (
    SELECT DISTINCT nr.series_id, wf.qid
    FROM nr
    JOIN manga.ms_formes mf ON mf.series_id = nr.series_id AND mf.forme_norm <> ''
    JOIN manga.wd_formes wf ON wf.forme_norm = mf.forme_norm
),
sig AS (
    SELECT c.series_id, c.qid,
        CASE
            WHEN NOT EXISTS (SELECT 1 FROM ms_auteur_norm_e1 m
                             WHERE m.series_id = c.series_id)
              OR NOT EXISTS (SELECT 1 FROM manga.wd_auteurs wa
                             JOIN manga.wd_auteurs_formes waf
                               ON waf.auteur_qid = wa.auteur_qid
                             WHERE wa.qid = c.qid)
            THEN 'incomparable'
            WHEN EXISTS (SELECT 1 FROM manga.wd_auteurs wa
                         JOIN manga.wd_auteurs_formes waf
                           ON waf.auteur_qid = wa.auteur_qid
                         JOIN ms_auteur_norm_e1 m
                           ON m.auteur_norm = waf.forme_norm
                         WHERE wa.qid = c.qid AND m.series_id = c.series_id)
            THEN 'concordant' ELSE 'discordant'
        END AS signal_auteur,
        CASE
            WHEN s.series_year IS NULL OR p.annee IS NULL THEN 'incomparable'
            WHEN (s.series_year - p.annee) BETWEEN %s AND %s THEN 'concordant'
            ELSE 'discordant'
        END AS signal_annee
    FROM cand c
    JOIN manga.ms_series_enriched s ON s.series_id = c.series_id
    JOIN manga.wd_pivot p ON p.qid = c.qid
),
agg AS (
    SELECT series_id, count(*) AS n_cand,
           count(*) FILTER (WHERE signal_auteur='concordant'
                              AND signal_annee <> 'discordant') AS n_auteur_ok,
           count(*) FILTER (WHERE signal_annee='concordant')    AS n_annee_ok
    FROM sig GROUP BY series_id
),
gagnant AS (
    SELECT DISTINCT ON (s.series_id) s.*
    FROM sig s JOIN agg a USING (series_id)
    WHERE a.n_cand = 1
       OR (a.n_cand > 1 AND a.n_auteur_ok = 1
           AND s.signal_auteur='concordant' AND s.signal_annee <> 'discordant')
    ORDER BY s.series_id, (s.signal_auteur='concordant') DESC, s.qid
)
SELECT a.series_id, a.n_cand,
    CASE
        WHEN g.signal_annee = 'discordant' THEN 'review_annee_discordante'
        WHEN a.n_cand = 1 AND g.signal_auteur = 'incomparable'
             AND g.signal_annee = 'incomparable' THEN 'review_sans_signal'
        WHEN a.n_cand = 1 AND g.signal_auteur = 'discordant'
            THEN 'review_auteur_discordant'
        WHEN a.n_annee_ok = 1 THEN 'review_multi_annee_seule'
        ELSE 'review_ambiguite'
    END AS cas_derive
FROM agg a LEFT JOIN gagnant g USING (series_id)
"""


def deriver_cas_etage1(cur) -> dict:
    """Re-dérive les libellés de cas de l'étage 1 DEPUIS LA BASE.

    Ces décisions ont été journalisées avant l'existence de `details` ; leur cas
    ne vivait donc que dans un CSV gitignoré. La dérivation lève cette
    dépendance — mais elle a une LIMITE à énoncer : les cas de COLLISION
    (`review_collision_qid`, `review_collision_id`) dépendaient de l'état de
    `work_identity` AU MOMENT du run de l'étage 1. Cet état a changé depuis
    (l'étage 2 y a écrit 4 576 kitsu_id et 40 QID). Ils ne sont donc pas
    re-dérivables fidèlement, et la dérivation ne prétend pas les reproduire :
    elle les rendra sous un autre libellé, ce que le contrôle de fidélité
    ci-dessous chiffre au lieu de le masquer.
    """
    cur.execute(
        "SELECT s.series_id, s.series_scenariste, s.series_dessinateur "
        "FROM manga.ms_series_enriched s "
        "JOIN manga.v_match_current v ON v.series_id = s.series_id "
        "JOIN manga.match_decision d ON d.decision_id = v.decision_id "
        "WHERE v.status='needs_review' AND d.method IN ('exact','exact_author')"
    )
    lignes: set[tuple[int, str]] = set()
    for series_id, scenariste, dessinateur in cur.fetchall():
        for brut in (scenariste, dessinateur):
            if brut and (norme := normaliser(brut)):
                lignes.add((series_id, norme))
    cur.execute(
        "CREATE TEMP TABLE ms_auteur_norm_e1 "
        "(series_id bigint, auteur_norm text) ON COMMIT DROP"
    )
    with cur.copy("COPY ms_auteur_norm_e1 (series_id, auteur_norm) FROM STDIN") as cp:
        for ligne in sorted(lignes):
            cp.write_row(ligne)
    cur.execute("CREATE INDEX ON ms_auteur_norm_e1 (series_id)")
    cur.execute("ANALYZE ms_auteur_norm_e1")

    cur.execute(SQL_DERIVER_ETAGE1, FENETRE_ETAGE1)
    cur.execute("SELECT cas_derive, count(*) FROM e3_cas_e1 GROUP BY 1 ORDER BY 2 DESC")
    return dict(cur.fetchall())


def controler_fidelite_derivation(cur, snapshot: Path) -> dict | None:
    """Confronte la dérivation au snapshot CSV de l'étage 1.

    Le CSV n'est PAS une dépendance ici : il sert de TÉMOIN pour chiffrer la
    fidélité de la dérivation. Une fois ce contrôle passé, la dérivation seule
    suffit — c'est ce qui lève la dépendance à un artefact gitignoré.
    """
    if not snapshot.is_file():
        return None
    with snapshot.open(encoding="utf-8") as fh:
        attendu = {int(r["series_id"]): r["cas"] for r in csv.DictReader(fh)}
    cur.execute("SELECT series_id, cas_derive FROM e3_cas_e1")
    obtenu = dict(cur.fetchall())

    communs = set(attendu) & set(obtenu)
    accords = sum(1 for s in communs if attendu[s] == obtenu[s])
    desaccords: dict[tuple[str, str], int] = {}
    for s in communs:
        if attendu[s] != obtenu[s]:
            cle = (attendu[s], obtenu[s])
            desaccords[cle] = desaccords.get(cle, 0) + 1
    return {
        "temoin": len(attendu),
        "derives": len(obtenu),
        "communs": len(communs),
        "accords": accords,
        "taux": 100 * accords / len(communs) if communs else 0.0,
        "desaccords": sorted(desaccords.items(), key=lambda x: -x[1]),
    }


# --------------------------------------------------------------------------- #
# Livrables
# --------------------------------------------------------------------------- #
def ecrire_livrables(cur, dossier: Path, calib: dict, mesures: dict) -> dict:
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "calibration_seuil.md").write_text(
        texte_calibration(calib), encoding="utf-8"
    )

    # Orphelines : périmètre sans aucun candidat au-dessus du seuil.
    cur.execute(
        "SELECT p.series_id, s.series_title, "
        "  concat_ws(' / ', s.series_scenariste, s.series_dessinateur), "
        "  s.series_year, "
        "  (SELECT count(*) FROM manga.ms_formes mf WHERE mf.series_id=p.series_id) "
        "FROM e3_perimetre p "
        "JOIN manga.ms_series_enriched s ON s.series_id = p.series_id "
        "WHERE NOT EXISTS (SELECT 1 FROM e3_cand c WHERE c.series_id = p.series_id) "
        "ORDER BY p.series_id"
    )
    n_orphelines = ecrire_csv(
        dossier / "orphelines.csv",
        ["series_id", "titre_ms", "auteurs_ms", "annee_ms", "n_formes"],
        cur.fetchall(),
    )

    cas_e1 = deriver_cas_etage1(cur)
    fidelite = controler_fidelite_derivation(
        cur,
        MODULE
        / "data"
        / "rapports"
        / "etage1"
        / "20260718T202153Z"
        / "needs_review.csv",
    )

    # needs_review CONSOLIDÉ FINAL — l'entrée de l'étage R.
    # Les cas viennent de la BASE : details pour les étages 2-3, dérivation
    # pour l'étage 1. Plus aucune lecture de CSV dans le chemin du livrable.
    cur.execute(
        """
        SELECT v.series_id,
               CASE WHEN d.method = 'trgm' THEN 'etage3'
                    WHEN d.method LIKE 'exact_kitsu%' THEN 'etage2'
                    ELSE 'etage1' END AS origine,
               s.series_title,
               concat_ws(' / ', s.series_scenariste, s.series_dessinateur),
               s.series_year,
               coalesce(d.details->>'case', e1.cas_derive, '') AS cas,
               CASE WHEN d.details ? 'case' THEN 'base:details'
                    WHEN e1.cas_derive IS NOT NULL THEN 'base:derivation'
                    ELSE 'indisponible' END AS provenance_cas,
               coalesce(d.details->>'n', d.details->>'n_cand',
                        e1.n_cand::text, '') AS n_candidats,
               coalesce(d.details->>'top', '') AS candidats,
               d.method, d.score
        FROM manga.v_match_current v
        JOIN manga.match_decision d ON d.decision_id = v.decision_id
        JOIN manga.ms_series_enriched s ON s.series_id = v.series_id
        LEFT JOIN e3_cas_e1 e1 ON e1.series_id = v.series_id
        WHERE v.status = 'needs_review'
        ORDER BY origine, v.series_id
        """
    )
    n_consolide = ecrire_csv(
        dossier / "needs_review_consolide_final.csv",
        [
            "series_id",
            "origine",
            "titre_ms",
            "auteurs_ms",
            "annee_ms",
            "cas",
            "provenance_cas",
            "n_candidats",
            "candidats",
            "method",
            "score",
        ],
        cur.fetchall(),
    )
    return {
        "orphelines": n_orphelines,
        "consolide": n_consolide,
        "cas_e1": cas_e1,
        "fidelite": fidelite,
    }


def texte_entonnoir(m: dict, calib: dict, liv: dict) -> str:
    t = calib["temoin"]
    lignes = [
        "# Entonnoir de l'étage 3 (rapprochement flou pg_trgm)",
        "",
        f"Seuil : **{SEUIL}**, contrôlé sur le témoin `{METHODE_TEMOIN}` "
        f"({t['pct']:.1f} % des identités vraies au-dessus).",
        "",
        "**Cet étage ne produit AUCUN AUTO.** Tout candidat part en "
        "`needs_review` avec son dossier.",
        "",
        f"- périmètre d'entrée (figé avant écriture) : **{m['perimetre']}**",
        f"- paires floues brutes (opérateur `%`, index GIN) : **{m['paires_brutes']}**",
        f"- candidats après déduplication par œuvre-cible : **{m['candidats_dedup']}**",
        f"- séries avec ≥1 candidat : **{m['avec_candidat']}**",
        f"- **orphelines de cascade** (aucun candidat ≥ seuil) : **{m['orphelines']}**",
        "",
        f"⏱ rapprochement flou exécuté en **{m['secondes_flou']:.1f} s**.",
        "",
        "## Séries par tranche de similarité du meilleur candidat",
        "",
        "| Tranche | Séries |",
        "|---|---:|",
    ]
    for tranche in ("0.85-0.90", "0.90-0.95", ">= 0.95"):
        lignes.append(f"| {tranche} | {m['tranches'].get(tranche, 0)} |")
    lignes += [
        "",
        "## Source du meilleur candidat",
        "",
        "| Source | Séries |",
        "|---|---:|",
    ]
    for source, nombre in sorted(m["meilleur_par_source"].items()):
        lignes.append(f"| {source} | {nombre} |")
    lignes += [
        "",
        "## Signal auteur du meilleur candidat (informatif, non décisionnel)",
        "",
        "| Signal | Séries |",
        "|---|---:|",
    ]
    for signal, nombre in sorted(m["auteur_du_meilleur"].items()):
        lignes.append(f"| {signal} | {nombre} |")

    lignes += [
        "",
        "## Les orphelines de cascade",
        "",
        f"**{liv['orphelines']}** séries n'ont aucun candidat au-dessus du "
        "seuil, ni exact ni flou. Elles ne reçoivent **aucune décision** et "
        "restent sans-décision-courante.",
        "",
        "Leur sort est une **décision produit** — v2 internationale, MADB, ou "
        "hors périmètre de matching — pas un `needs_review` de plus. Les "
        "empiler dans la file du juge reviendrait à lui demander d'arbitrer "
        "des dossiers vides.",
        "",
        "## Libellés de cas de l'étage 1, re-dérivés depuis la base",
        "",
        "| Cas dérivé | Séries |",
        "|---|---:|",
    ]
    for cas, nombre in sorted(liv["cas_e1"].items(), key=lambda x: -x[1]):
        lignes.append(f"| `{cas}` | {nombre} |")

    fid = liv["fidelite"]
    if fid:
        lignes += [
            "",
            f"**Contrôle de fidélité** contre le snapshot de l'étage 1 "
            f"(témoin, pas dépendance) : **{fid['accords']}/{fid['communs']} "
            f"({fid['taux']:.1f} %)** d'accord.",
            "",
        ]
        if fid["desaccords"]:
            lignes += ["| Attendu (snapshot) | Dérivé | n |", "|---|---|---:|"]
            for (attendu, obtenu), nombre in fid["desaccords"]:
                lignes.append(f"| `{attendu}` | `{obtenu}` | {nombre} |")
            lignes += [
                "",
                "⚠️ Les cas de **collision** ne sont pas re-dérivables : ils "
                "dépendaient de l'état de `work_identity` au moment du run de "
                "l'étage 1, état que l'étage 2 a depuis modifié (4 576 kitsu_id "
                "et 40 QID écrits). La dérivation ne prétend pas les "
                "reproduire ; l'écart est chiffré plutôt que masqué.",
                "",
            ]

    lignes += [
        "",
        f"`needs_review_consolide_final.csv` : **{liv['consolide']}** dossiers "
        "(étages 1 + 2 + 3) — **l'entrée de l'étage R**. Les libellés de cas "
        "viennent tous de la BASE (`details` pour les étages 2-3, dérivation "
        "pour l'étage 1) : plus aucune lecture de CSV dans le chemin du "
        "livrable.",
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
        None, help="Dossier des livrables (défaut : data/rapports/etage3/<ts>)."
    ),
    perimetre_attendu: int = typer.Option(  # noqa: B008
        PERIMETRE_ATTENDU, help="Périmètre attendu ; un écart arrête le run."
    ),
) -> None:
    """Calibre le seuil, rapproche en flou, journalise en needs_review."""
    horodatage = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dossier = Path(rapport_dir) if rapport_dir else RAPPORTS / horodatage
    debut = time.monotonic()

    with psycopg.connect(dsn()) as connexion:
        with connexion.cursor() as cur:
            verifier_prerequis(cur)

            perimetre = compter_perimetre(cur)
            if perimetre != perimetre_attendu:
                raise ErreurEtage3(
                    f"Périmètre mesuré {perimetre}, attendu {perimetre_attendu} "
                    "— écart inexpliqué, STOP. (Passer --perimetre-attendu pour "
                    "un rejeu délibéré sur un autre état.)"
                )
            typer.echo(f"→ périmètre vérifié : {perimetre} séries")

            calib = calibrer_seuil(cur)
            t = calib["temoin"]
            typer.echo(
                f"→ calibration : témoin `{METHODE_TEMOIN}` "
                f"{t['au_seuil']}/{t['n']} ({t['pct']:.1f} %) au-dessus de "
                f"{SEUIL} — {calib['circulaires']} identités circulaires écartées"
            )
            if not calib["verdict_ok"]:
                dossier.mkdir(parents=True, exist_ok=True)
                (dossier / "calibration_seuil.md").write_text(
                    texte_calibration(calib), encoding="utf-8"
                )
                raise ErreurEtage3(
                    f"La calibration CONTREDIT le seuil {SEUIL} : seulement "
                    f"{t['pct']:.1f} % des identités du témoin l'atteignent. "
                    "Le seuil est une décision humaine — STOP et rapport. "
                    f"Détail écrit dans {dossier}/calibration_seuil.md"
                )

            figer_perimetre(cur)
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

            # Portée TRANSACTION (3e argument = true), et non session : le
            # seuil ne doit pas survivre au run. `SET LOCAL` n'accepte pas de
            # paramètre lié — set_config() est son équivalent fonctionnel et
            # évite d'interpoler une valeur dans du SQL.
            cur.execute(
                "SELECT set_config('pg_trgm.similarity_threshold', %s, true)",
                (str(SEUIL),),
            )
            cur.execute("SHOW pg_trgm.similarity_threshold")
            applique = cur.fetchone()[0]
            if abs(float(applique) - SEUIL) > 1e-9:
                raise ErreurEtage3(
                    f"Seuil demandé {SEUIL}, seuil appliqué {applique} — le "
                    "rapprochement ne filtrerait pas ce qu'on croit. STOP."
                )
            depart_flou = time.monotonic()
            cur.execute(ETAGE3_SQL.read_text(encoding="utf-8"))
            secondes_flou = time.monotonic() - depart_flou
            typer.echo(f"→ rapprochement flou terminé en {secondes_flou:.1f} s")

            mesures = mesurer(cur, perimetre, secondes_flou)
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
    except ErreurEtage3 as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
