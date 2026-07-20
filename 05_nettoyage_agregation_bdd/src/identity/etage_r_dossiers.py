"""Étage R, jalon R-a : les dossiers, l'étalonnage, l'échantillon, le coût.

    uv run python -m identity.etage_r_dossiers assembler
    uv run python -m identity.etage_r_dossiers etalonnage
    uv run python -m identity.etage_r_dossiers echantillon
    uv run python -m identity.etage_r_dossiers volumetrie

LECTURE SEULE, ZÉRO RÉSEAU. Ce module ne contacte aucune API et n'écrit rien
en base : il prépare la matière que le juge lira au jalon R-b. Le régime
avis-seulement commence ici — même la préparation n'a pas le droit d'écrire.

  assembler   — les 1 932 dossiers de la file (périmètre FIGÉ avant tout calcul)
  etalonnage  — 60 cas fabriqués depuis les identités sûres : 30 vrais,
                30 faux. Tirage seedé, donc reproductible.
  echantillon — 100 décisions AUTO stratifiées, pour l'échantillon C3.
  volumetrie  — taille réelle des dossiers et coût prévisionnel du run.

POURQUOI LES DOSSIERS SE FABRIQUENT AVANT D'AVOIR LA CLÉ. Le juge est un
paramètre du run, pas sa matière. Tout ce qui se mesure sans lui — la taille
des dossiers, la difficulté des cas, la stratification de l'échantillon — se
mesure et se teste maintenant. Ce qui reste au jalon R-b est l'appel lui-même.
"""

from __future__ import annotations

import csv
import json
import os
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from identity.wikidata_dump import normaliser

MODULE = Path(__file__).resolve().parents[2]
SQL_DOSSIERS = Path(__file__).resolve().parent / "sql" / "etage_r_dossiers.sql"
RAPPORTS = MODULE / "data" / "rapports" / "etage_r"

FILE_ATTENDUE = 1932
TAILLE_ETALONNAGE = 60
TAILLE_ECHANTILLON = 100

# Tirage SEEDÉ : deux exécutions produisent les mêmes cas. Sans cela, un
# étalonnage raté serait irreproductible et le juge impossible à comparer d'un
# run à l'autre.
GRAINE = 20260719

# Le synopsis est tronqué : au-delà, il coûte des tokens sans départager deux
# œuvres homonymes — c'est le titre, l'auteur et l'année qui tranchent.
SYNOPSIS_MAX = 600

# La fenêtre d'année calibrée par l'étage 2. Le seau ADJACENT est celui qui la
# borde immédiatement : c'est lui que la politique des bandes (§26) touche.
FENETRE_ANNEE = (0, 2)

# --------------------------------------------------------------------------- #
# Stratification de l'échantillon C3 — la composition est une DÉCISION, pas un
# hasard. Chaque strate répond à une question distincte :
#   standard    : la précision d'ensemble de la cascade (≥ 95 % attendu)
#   historique  : les 340 auto à année hors fenêtre, sous surveillance (§26.3)
#   score_bas   : les décisions les moins confiantes (0.90 / 0.93)
#   pont        : témoin — apparié par identifiants, précision attendue ~100 %.
#                 Une strate dont on connaît la réponse mesure l'ARBITRE.
# --------------------------------------------------------------------------- #
STRATES = {"standard": 40, "historique": 25, "score_bas": 20, "pont": 15}

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurEtageR(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurEtageR(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'"
        )
    return url


def verifier_prerequis(cur) -> None:
    manquants = []
    for objet in (
        "manga.llm_avis",
        "manga.v_match_current",
        "manga.match_decision",
        "manga.ms_formes",
        "manga.wd_formes",
        "manga.wd_pivot",
        "manga.wd_auteurs_formes",
        "manga.kitsu_formes",
        "manga.kitsu_staff",
        "manga.kitsu_meta",
        "manga.kitsu_mappings",
    ):
        cur.execute("SELECT to_regclass(%s)", (objet,))
        if cur.fetchone()[0] is None:
            manquants.append(objet)
    if manquants:
        raise ErreurEtageR(
            "Schéma incomplet — la migration 010 est-elle appliquée ? Absents : "
            + ", ".join(manquants)
        )


def charger_auteurs_ms(cur, series_ids: list[int]) -> int:
    """Table temporaire (series_id, auteur_norm) via identity.normaliser()."""
    cur.execute(
        "SELECT s.series_id, s.series_scenariste, s.series_dessinateur "
        "FROM manga.ms_series_enriched s WHERE s.series_id = ANY(%s)",
        (series_ids,),
    )
    lignes: set[tuple[int, str]] = set()
    for series_id, scenariste, dessinateur in cur.fetchall():
        for brut in (scenariste, dessinateur):
            if brut and (norme := normaliser(brut)):
                lignes.add((series_id, norme))
    cur.execute(
        "CREATE TEMP TABLE ms_auteur_norm (series_id bigint, auteur_norm text) "
        "ON COMMIT DROP"
    )
    with cur.copy("COPY ms_auteur_norm (series_id, auteur_norm) FROM STDIN") as copie:
        for ligne in sorted(lignes):
            copie.write_row(ligne)
    cur.execute("CREATE INDEX ON ms_auteur_norm (series_id)")
    cur.execute("CREATE INDEX ON ms_auteur_norm (auteur_norm)")
    cur.execute("ANALYZE ms_auteur_norm")
    return len(lignes)


# --------------------------------------------------------------------------- #
# Dossiers à contexte incomplet — CANDIDAT CONTESTÉ AUJOURD'HUI
# --------------------------------------------------------------------------- #
# ⚠️ CETTE POPULATION N'EST PAS « LES 52 COLLISIONS DE L'ÉTAGE 1 ». Mesuré :
# 84 séries ici, dont 14 seulement parmi les 52 historiques — ce n'est même pas
# un sur-ensemble. L'écart est attendu et il CONFIRME le §28.1 : les collisions
# de l'étage 1 dépendaient de l'état de work_identity à son run, état que
# l'étage 2 a modifié en écrivant 4 576 kitsu_id et 40 QID. Elles ne sont pas
# re-dérivables, et cette requête ne prétend pas les reproduire.
#
# Ce qu'elle marque à la place, et qui est dérivable de la BASE seule : un
# dossier dont le candidat QID est déjà porté par une AUTRE série aujourd'hui.
# C'est la même nature d'information — « le contexte de ce dossier est
# incomplet, n'accorde pas une confiance haute à ce qui manque » — établie sur
# l'état courant plutôt que sur un instantané perdu.
#
# Le snapshot daté de l'étage 1 reste en annexe : conformément au §28.1, il
# DOCUMENTE, il ne PILOTE pas. Le lire ici pour retrouver les 52 le ferait
# piloter. Point ouvert soumis à décision (cf. rapport R-a).
SQL_COLLISIONS = """
SELECT f.series_id
FROM r_file f
WHERE f.origine = 'etage1'
  AND EXISTS (
      SELECT 1 FROM manga.work_identity w
      WHERE w.wikidata_qid IS NOT NULL
        AND w.wikidata_qid IN (
            SELECT c.candidat_id FROM r_candidat c
            WHERE c.series_id = f.series_id AND c.candidat_type = 'qid')
        AND w.series_id <> f.series_id)
"""


def _executer_sql_dossiers(cur) -> None:
    cur.execute(SQL_DOSSIERS.read_text(encoding="utf-8"))


def construire_dossiers(cur, file_attendue: int) -> tuple[list[dict], dict]:
    # 1) La file, figée d'abord — avant les auteurs, avant les candidats.
    cur.execute(
        "SELECT count(*) FROM manga.v_match_current WHERE status = 'needs_review'"
    )
    taille = cur.fetchone()[0]
    if taille != file_attendue:
        raise ErreurEtageR(
            f"File mesurée {taille}, attendue {file_attendue} — écart "
            "inexpliqué, STOP. (--file-attendue pour un rejeu délibéré.)"
        )

    cur.execute(
        "SELECT v.series_id FROM manga.v_match_current v "
        "WHERE v.status = 'needs_review'"
    )
    series_ids = [s for (s,) in cur.fetchall()]
    charger_auteurs_ms(cur, series_ids)
    _executer_sql_dossiers(cur)

    cur.execute(SQL_COLLISIONS)
    collisions = {s for (s,) in cur.fetchall()}

    # 2) Le seau adjacent SOUS SECOND SIGNAL — pré-validation des bandes (§26.4).
    #    Définition reprise telle quelle de identity.mesure_349 : écart d'année
    #    dans le seau qui BORDE la fenêtre [+0,+2], ET auteur concordant. La
    #    case entière (349) serait une population différente, qui ne mesurerait
    #    pas la politique adoptée.
    cur.execute(
        "SELECT DISTINCT f.series_id "
        "FROM r_file f "
        "JOIN manga.match_decision d ON d.decision_id = f.decision_id "
        "JOIN r_candidat c ON c.series_id = f.series_id "
        "WHERE d.details->>'case' = 'review_k_annee_discordante' "
        "  AND c.ecart_annee IN (%s, %s) "
        "  AND c.signal_auteur = 'concordant'",
        (FENETRE_ANNEE[0] - 1, FENETRE_ANNEE[1] + 1),
    )
    seau = {s for (s,) in cur.fetchall()}

    # 3) Le corps des dossiers.
    cur.execute(
        """
        SELECT f.series_id, f.origine, f.method, f.score,
               coalesce(f.details->>'case', '') AS cas,
               s.series_title,
               concat_ws(' / ', s.series_scenariste, s.series_dessinateur),
               s.series_year,
               left(coalesce(s.series_synopsis, ''), %s),
               (SELECT string_agg(DISTINCT mf.forme, ' | ' ORDER BY mf.forme)
                FROM manga.ms_formes mf WHERE mf.series_id = f.series_id)
        FROM r_file f
        JOIN manga.ms_series_enriched s ON s.series_id = f.series_id
        ORDER BY f.series_id
        """,
        (SYNOPSIS_MAX,),
    )
    entetes = cur.fetchall()

    cur.execute(
        "SELECT series_id, candidat_type, candidat_id, cible_label, cible_annee, "
        "       cible_contexte, cible_formes, cible_auteurs, signal_auteur, "
        "       ecart_annee, confirme_par_kitsu "
        "FROM r_candidat ORDER BY series_id, candidat_type, candidat_id"
    )
    par_serie: dict[int, list[dict]] = {}
    for r in cur.fetchall():
        par_serie.setdefault(r[0], []).append(
            {
                "type": r[1],
                "id": r[2],
                "label": r[3],
                "annee": r[4],
                "contexte": r[5],
                "formes": r[6],
                "auteurs": r[7],
                "signal_auteur": r[8],
                "ecart_annee": r[9],
                "confirme_par_kitsu": r[10],
            }
        )

    dossiers = []
    for (
        series_id,
        origine,
        method,
        score,
        cas,
        titre,
        auteurs,
        annee,
        synopsis,
        formes,
    ) in entetes:
        dossiers.append(
            {
                "series_id": series_id,
                "origine": origine,
                "method": method,
                "score": score,
                "cas": cas,
                "ms": {
                    "titre": titre,
                    "formes": formes,
                    "auteurs": auteurs,
                    "annee": annee,
                    "synopsis": synopsis,
                },
                "candidats": par_serie.get(series_id, []),
                "dossier_partiel": series_id in collisions,
                "pre_validation_bandes": series_id in seau,
            }
        )

    mesures = {
        "file": len(dossiers),
        "sans_candidat": sum(1 for d in dossiers if not d["candidats"]),
        "candidats": sum(len(d["candidats"]) for d in dossiers),
        "partiels": sum(1 for d in dossiers if d["dossier_partiel"]),
        "seau_adjacent": sum(1 for d in dossiers if d["pre_validation_bandes"]),
        "par_origine": {},
    }
    for d in dossiers:
        o = mesures["par_origine"].setdefault(
            d["origine"], {"dossiers": 0, "candidats": 0}
        )
        o["dossiers"] += 1
        o["candidats"] += len(d["candidats"])
    return dossiers, mesures


# --------------------------------------------------------------------------- #
# PHASE 0 — fabrication des cas d'étalonnage
# --------------------------------------------------------------------------- #
def fabriquer_etalonnage(cur, taille: int, graine: int) -> list[dict]:
    """30 paires CORRECTES + 30 paires FAUSSES, depuis les identités sûres.

    MÉTHODE DE FABRICATION DES FAUX — c'est elle qui fait la valeur de la
    mesure. Un faux tiré au hasard (série A × candidat d'une série sans rapport)
    est trivial à rejeter : un juge qui n'y arrive pas est cassé, mais un juge
    qui y arrive n'a rien prouvé. On fabrique donc des faux DIFFICILES en
    appariant chaque série au candidat d'une AUTRE série du même voisinage :
    même décennie de publication d'abord, à défaut n'importe quelle autre. Le
    juge doit alors distinguer deux œuvres plausiblement confondables, ce qui
    est exactement la tâche de la vraie file.
    """
    moitie = taille // 2
    cur.execute(
        """
        SELECT v.series_id, s.series_title,
               concat_ws(' / ', s.series_scenariste, s.series_dessinateur),
               s.series_year, w.wikidata_qid, w.kitsu_id
        FROM manga.v_match_current v
        JOIN manga.work_identity w ON w.series_id = v.series_id
        JOIN manga.ms_series_enriched s ON s.series_id = v.series_id
        WHERE v.status = 'auto'
          AND (w.wikidata_qid IS NOT NULL OR w.kitsu_id IS NOT NULL)
          AND s.series_year IS NOT NULL
        ORDER BY md5(v.series_id::text)
        LIMIT %s
        """,
        (taille * 6,),
    )
    sures = [
        {
            "series_id": r[0],
            "titre": r[1],
            "auteurs": r[2],
            "annee": r[3],
            "qid": r[4],
            "kitsu_id": r[5],
        }
        for r in cur.fetchall()
    ]
    if len(sures) < taille * 2:
        raise ErreurEtageR(
            f"Seulement {len(sures)} identités sûres exploitables, il en faut "
            f"au moins {taille * 2} pour fabriquer {taille} cas — STOP."
        )

    tirage = random.Random(graine)  # noqa: S311 — reproductibilité, pas sécurité
    tirage.shuffle(sures)

    cas: list[dict] = []
    for s in sures[:moitie]:
        cas.append({**s, "attendu": "same_work", "fabrication": "identite_sure"})

    # Faux difficiles : apparier avec une autre série de la MÊME décennie.
    reste = sures[moitie:]
    par_decennie: dict[int, list[dict]] = {}
    for s in reste:
        par_decennie.setdefault(s["annee"] // 10, []).append(s)

    faux = 0
    for s in reste:
        if faux >= taille - moitie:
            break
        voisins = [v for v in par_decennie.get(s["annee"] // 10, []) if v is not s]
        difficulte = "meme_decennie"
        if not voisins:
            voisins = [v for v in reste if v is not s]
            difficulte = "quelconque"
        if not voisins:
            continue
        leurre = tirage.choice(voisins)
        cas.append(
            {
                **s,
                "qid": leurre["qid"],
                "kitsu_id": leurre["kitsu_id"],
                "leurre_de": leurre["series_id"],
                "leurre_titre": leurre["titre"],
                "attendu": "different_work",
                "fabrication": f"leurre_{difficulte}",
            }
        )
        faux += 1

    tirage.shuffle(cas)  # ordre mêlé : le juge ne doit pas lire une séquence
    return cas


# --------------------------------------------------------------------------- #
# Échantillon C3 — stratifié, seedé
# --------------------------------------------------------------------------- #
def echantillonner_c3(cur, strates: dict[str, int], graine: int) -> list[dict]:
    requetes = {
        "historique": (
            "SELECT v.series_id, v.method, v.score, "
            "       coalesce(d.details->>'case','') "
            "FROM manga.v_match_current v "
            "JOIN manga.match_decision d ON d.decision_id = v.decision_id "
            "WHERE v.status='auto' "
            "  AND d.details->>'case' = 'auto_k_historique_confirme'"
        ),
        "score_bas": (
            "SELECT v.series_id, v.method, v.score, "
            "       coalesce(d.details->>'case','') "
            "FROM manga.v_match_current v "
            "JOIN manga.match_decision d ON d.decision_id = v.decision_id "
            "WHERE v.status='auto' AND v.score IN (0.90, 0.93)"
        ),
        "pont": (
            "SELECT v.series_id, v.method, v.score, '' "
            "FROM manga.v_match_current v "
            "WHERE v.status='auto' AND v.method='kitsu_bridge'"
        ),
        "standard": (
            "SELECT v.series_id, v.method, v.score, "
            "       coalesce(d.details->>'case','') "
            "FROM manga.v_match_current v "
            "JOIN manga.match_decision d ON d.decision_id = v.decision_id "
            "WHERE v.status='auto' AND v.method <> 'kitsu_bridge' "
            "  AND coalesce(d.details->>'case','') <> "
            "      'auto_k_historique_confirme' "
            "  AND v.score NOT IN (0.90, 0.93)"
        ),
    }
    tirage = random.Random(graine)  # noqa: S311 — reproductibilité, pas sécurité
    echantillon: list[dict] = []
    deja: set[int] = set()
    manques: list[str] = []

    for strate, cible in strates.items():
        cur.execute(requetes[strate])
        population = [r for r in cur.fetchall() if r[0] not in deja]
        if len(population) < cible:
            manques.append(f"{strate} : {len(population)} disponibles < {cible}")
            continue
        for series_id, method, score, cas in tirage.sample(population, cible):
            deja.add(series_id)
            echantillon.append(
                {
                    "series_id": series_id,
                    "strate": strate,
                    "method": method,
                    "score": score,
                    "cas": cas,
                }
            )
    if manques:
        raise ErreurEtageR(
            "Strates insuffisantes pour l'échantillon C3 — STOP : "
            + " ; ".join(manques)
        )
    return echantillon


# --------------------------------------------------------------------------- #
# Volumétrie et coût
# --------------------------------------------------------------------------- #
def texte_dossier(dossier: dict) -> str:
    """La forme exacte que le juge lira. Sert AUSSI de base à la volumétrie —
    mesurer autre chose que ce qui sera envoyé donnerait un chiffre faux."""
    ms = dossier["ms"]
    lignes = [
        "## Série à identifier (source : Manga Sanctuary)",
        f"- titre : {ms['titre']}",
        f"- autres formes : {ms['formes'] or '(aucune)'}",
        f"- auteurs : {ms['auteurs'] or '(inconnu)'}",
        f"- année : {ms['annee'] if ms['annee'] is not None else '(inconnue)'}",
    ]
    if ms["synopsis"]:
        lignes.append(f"- synopsis (tronqué) : {ms['synopsis']}")
    if dossier["dossier_partiel"]:
        lignes.append(
            "- ⚠ DOSSIER PARTIEL : le contexte de collision d'origine n'est pas "
            "reconstituable. Juge sur ce qui est présent, et n'accorde pas une "
            "confiance haute à ce qui manque."
        )
    lignes.append("")
    lignes.append("## Candidats")
    if not dossier["candidats"]:
        lignes.append("(aucun candidat — répondre undecidable)")
    for i, c in enumerate(dossier["candidats"], 1):
        lignes += [
            f"### Candidat {i} — {c['type']}:{c['id']}",
            f"- label : {c['label'] or '(sans label)'}",
            f"- formes connues : {c['formes'] or '(aucune)'}",
            f"- auteurs : {c['auteurs'] or '(inconnu)'}",
            f"- année : {c['annee'] if c['annee'] is not None else '(inconnue)'}",
            f"- contexte : {c['contexte'] or '(aucun)'}",
            f"- signal auteur (calculé) : {c['signal_auteur']}",
            f"- écart d'année (calculé) : "
            f"{c['ecart_annee'] if c['ecart_annee'] is not None else 'incalculable'}",
        ]
        if c["confirme_par_kitsu"]:
            lignes.append(
                "- ✔ un second référentiel (Kitsu) désigne indépendamment ce "
                "même candidat"
            )
    return "\n".join(lignes)


def volumetrie(dossiers: list[dict]) -> dict:
    """Mesure la taille RÉELLE des dossiers, en caractères.

    ⚠️ POURQUOI DES CARACTÈRES ET NON DES TOKENS. Compter les tokens de Claude
    exige l'endpoint `count_tokens`, donc une clé API — indisponible au jalon
    R-a. Et il n'existe pas de substitut acceptable : `tiktoken` est le
    tokenizer d'OpenAI, il sous-compte les tokens de Claude d'environ 15-20 %
    sur du texte courant et bien davantage sur du japonais — c'est-à-dire sur
    l'essentiel de nos formes. Un chiffre faux serait pire qu'un chiffre absent,
    parce qu'il servirait à budgéter.

    On mesure donc EXACTEMENT ce qui est mesurable (caractères, structure) et
    on borne le reste par un ratio explicite. Le chiffre exact se prend en une
    requête `count_tokens` au jalon R-b, sur un échantillon de dossiers réels.
    """
    tailles = [len(texte_dossier(d)) for d in dossiers]
    tailles.sort()
    n = len(tailles)
    return {
        "dossiers": n,
        "caracteres_total": sum(tailles),
        "caracteres_moyen": sum(tailles) / n if n else 0,
        "caracteres_median": tailles[n // 2] if n else 0,
        "caracteres_p95": tailles[int(n * 0.95)] if n else 0,
        "caracteres_max": tailles[-1] if n else 0,
    }


def texte_volumetrie(v: dict, prompt_systeme: int) -> list[str]:
    """Le coût prévisionnel, avec ses bornes assumées."""
    # Bornes du ratio caractères → tokens. La borne basse correspond à du texte
    # latin dense, la haute à du CJK et à des identifiants — les deux sont
    # présents dans nos formes. On rapporte un INTERVALLE, pas un point.
    ratio_bas, ratio_haut = 3.5, 2.0
    entree_chars = v["caracteres_total"] + prompt_systeme * v["dossiers"]
    tok_bas = entree_chars / ratio_bas
    tok_haut = entree_chars / ratio_haut
    # Sortie : verdict + confiance + une phrase. Bornée par le schéma contraint.
    sortie_bas, sortie_haut = 40 * v["dossiers"], 120 * v["dossiers"]

    # Tarif Sonnet 5, introduction en vigueur jusqu'au 2026-08-31, RÉDUIT DE
    # MOITIÉ par la Batch API.
    prix_in, prix_out = 2.0 / 2 / 1e6, 10.0 / 2 / 1e6

    return [
        "# Volumétrie des dossiers et coût prévisionnel (jalon R-a)",
        "",
        "## Ce qui est mesuré exactement",
        "",
        f"- dossiers assemblés : **{v['dossiers']}**",
        f"- caractères, total : **{v['caracteres_total']:,}**".replace(",", " "),
        f"- caractères par dossier : moyenne **{v['caracteres_moyen']:.0f}**, "
        f"médiane **{v['caracteres_median']}**, p95 **{v['caracteres_p95']}**, "
        f"max **{v['caracteres_max']}**",
        f"- prompt système : **{prompt_systeme}** caractères, répétés à chaque "
        "dossier (aucune mise en cache supposée — hypothèse prudente)",
        "",
        "## ⚠️ Ce qui n'est PAS mesuré, et pourquoi",
        "",
        "Le comptage exact des tokens de Claude exige l'endpoint "
        "`count_tokens`, donc une clé API — indisponible au jalon R-a.",
        "",
        "Il n'existe pas de substitut acceptable : `tiktoken` est le tokenizer "
        "**d'OpenAI**. Il sous-compte les tokens de Claude d'environ 15-20 % "
        "sur du texte courant, et bien davantage sur du japonais — c'est-à-dire "
        "sur une part importante de nos formes. **Un chiffre faux serait pire "
        "qu'un chiffre absent**, parce qu'il servirait à budgéter.",
        "",
        "L'estimation ci-dessous est donc donnée en **intervalle**, borné par "
        f"un ratio caractères/token de {ratio_bas} (latin dense) à {ratio_haut} "
        "(CJK et identifiants). Le chiffre exact se prend en **une** requête "
        "`count_tokens` au jalon R-b, sur un échantillon de dossiers réels.",
        "",
        "## Coût prévisionnel — Sonnet 5, Batch API",
        "",
        "| Poste | Borne basse | Borne haute |",
        "|---|---:|---:|",
        f"| tokens d'entrée | {tok_bas:,.0f} | {tok_haut:,.0f} |".replace(",", " "),
        f"| tokens de sortie | {sortie_bas:,} | {sortie_haut:,} |".replace(",", " "),
        f"| **coût** | **{tok_bas * prix_in + sortie_bas * prix_out:.2f} $** | "
        f"**{tok_haut * prix_in + sortie_haut * prix_out:.2f} $** |",
        "",
        "Tarif retenu : Sonnet 5 en introduction (2 $/10 $ par MTok jusqu'au "
        "2026-08-31), **divisé par deux** par la Batch API → 1 $/5 $ par MTok.",
        "",
        "Ce périmètre couvre la **file seule**. L'étalonnage (60 cas) et "
        "l'échantillon C3 (100) ajoutent ~8 % de dossiers, donc un ordre de "
        "grandeur inchangé.",
        "",
    ]


# --------------------------------------------------------------------------- #
# Sorties
# --------------------------------------------------------------------------- #
def ecrire_jsonl(chemin: Path, lignes: list[dict]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", encoding="utf-8") as fh:
        for ligne in lignes:
            fh.write(json.dumps(ligne, ensure_ascii=False) + "\n")


def ecrire_csv(chemin: Path, entetes: list[str], lignes) -> int:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(entetes)
        n = 0
        for ligne in lignes:
            w.writerow(ligne)
            n += 1
    return n


def _dossier_sortie(rapport_dir: str | None) -> Path:
    if rapport_dir:
        return Path(rapport_dir)
    return RAPPORTS / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _connexion_lecture(fn):
    """Exécute fn(cur) puis ROLLBACK : lecture seule, toujours."""
    with psycopg.connect(dsn()) as connexion:
        with connexion.cursor() as cur:
            verifier_prerequis(cur)
            resultat = fn(cur)
        connexion.rollback()
    return resultat


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@app.command()
def assembler(
    rapport_dir: str = typer.Option(None),  # noqa: B008
    file_attendue: int = typer.Option(FILE_ATTENDUE),  # noqa: B008
) -> None:
    """Assemble les 1 932 dossiers de la file. Lecture seule."""
    debut = time.monotonic()
    dossier = _dossier_sortie(rapport_dir)
    dossiers, mesures = _connexion_lecture(
        lambda cur: construire_dossiers(cur, file_attendue)
    )
    ecrire_jsonl(dossier / "dossiers_file.jsonl", dossiers)

    typer.echo(f"file             : {mesures['file']}")
    typer.echo(f"candidats totaux : {mesures['candidats']}")
    typer.echo(f"sans candidat    : {mesures['sans_candidat']}")
    typer.echo(f"dossiers partiels: {mesures['partiels']}")
    typer.echo(f"seau adjacent    : {mesures['seau_adjacent']}")
    for origine, m in sorted(mesures["par_origine"].items()):
        typer.echo(
            f"  {origine} : {m['dossiers']} dossiers, {m['candidats']} candidats"
        )
    typer.echo(f"Livrable : {dossier}/dossiers_file.jsonl")
    typer.echo(f"Terminé en {time.monotonic() - debut:.1f} s (aucune écriture).")


@app.command()
def etalonnage(
    rapport_dir: str = typer.Option(None),  # noqa: B008
    taille: int = typer.Option(TAILLE_ETALONNAGE),  # noqa: B008
    graine: int = typer.Option(GRAINE),  # noqa: B008
) -> None:
    """Fabrique les cas d'étalonnage (moitié vrais, moitié faux difficiles)."""
    dossier = _dossier_sortie(rapport_dir)
    cas = _connexion_lecture(lambda cur: fabriquer_etalonnage(cur, taille, graine))
    ecrire_jsonl(dossier / "etalonnage_cas.jsonl", cas)

    vrais = sum(1 for c in cas if c["attendu"] == "same_work")
    meme_dec = sum(1 for c in cas if c["fabrication"] == "leurre_meme_decennie")
    typer.echo(f"cas fabriqués : {len(cas)} (graine {graine}, reproductible)")
    typer.echo(f"  attendus same_work      : {vrais}")
    typer.echo(f"  attendus different_work : {len(cas) - vrais}")
    typer.echo(f"    dont leurres de la même décennie : {meme_dec}")
    typer.echo(f"Livrable : {dossier}/etalonnage_cas.jsonl")


@app.command()
def echantillon(
    rapport_dir: str = typer.Option(None),  # noqa: B008
    graine: int = typer.Option(GRAINE),  # noqa: B008
) -> None:
    """Tire l'échantillon C3 stratifié (100 décisions AUTO)."""
    dossier = _dossier_sortie(rapport_dir)
    tirage = _connexion_lecture(lambda cur: echantillonner_c3(cur, STRATES, graine))
    n = ecrire_csv(
        dossier / "echantillon_c3_arbitrage.csv",
        [
            "series_id",
            "strate",
            "method",
            "score",
            "cas",
            "AVIS_LLM",
            "VERDICT_HUMAIN",
        ],
        (
            [e["series_id"], e["strate"], e["method"], e["score"], e["cas"], "", ""]
            for e in tirage
        ),
    )
    par_strate: dict[str, int] = {}
    for e in tirage:
        par_strate[e["strate"]] = par_strate.get(e["strate"], 0) + 1
    typer.echo(f"échantillon C3 : {n} décisions (graine {graine})")
    for strate, nombre in sorted(par_strate.items()):
        typer.echo(f"  {strate} : {nombre}")
    typer.echo(f"Livrable : {dossier}/echantillon_c3_arbitrage.csv")
    typer.echo("Colonnes AVIS_LLM et VERDICT_HUMAIN vides : R-b puis l'humain.")


@app.command()
def volumetrie_cmd(
    rapport_dir: str = typer.Option(None),  # noqa: B008
    file_attendue: int = typer.Option(FILE_ATTENDUE),  # noqa: B008
) -> None:
    """Mesure la taille réelle des dossiers et chiffre le coût prévisionnel."""
    from identity.etage_r_contrat import PROMPT_SYSTEME

    dossier = _dossier_sortie(rapport_dir)
    dossiers, _ = _connexion_lecture(
        lambda cur: construire_dossiers(cur, file_attendue)
    )
    v = volumetrie(dossiers)
    lignes = texte_volumetrie(v, len(PROMPT_SYSTEME))
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "couts.md").write_text("\n".join(lignes), encoding="utf-8")
    for ligne in lignes:
        typer.echo(ligne)
    typer.echo(f"Livrable : {dossier}/couts.md")


def main() -> int:
    try:
        app()
    except ErreurEtageR as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
