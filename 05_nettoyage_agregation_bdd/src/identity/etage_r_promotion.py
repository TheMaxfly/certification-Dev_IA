"""Étage R, run 2 — promotion des verdicts LLM + correction humaine (1428).

    uv run python -m identity.etage_r_promotion                # dry-run (défaut)
    uv run python -m identity.etage_r_promotion --appliquer    # écrit

LÈVE le régime avis-seulement, de façon STRICTEMENT bornée par les mesures :
- PROMOTION en 'auto' des verdicts LLM `same_work` **haute**, non partiels,
  série encore needs_review, à candidat UNIQUE, sans collision d'identité
  (method='llm_review'). Trois strates traçables.
- CORRECTION humaine du faux positif du socle série 1428 (method='human_review',
  status='rejected' + identité remise à NULL + exclusion du pont consignée).

Journal APPEND-ONLY : aucune décision existante n'est modifiée ni supprimée ;
une promotion est une NOUVELLE ligne, une correction est une décision rejected.
work_identity : on REMPLIT des colonnes NULL (COALESCE) ou on les remet à NULL
(1428) — jamais d'écrasement d'une identité déjà posée.

--dry-run est le DÉFAUT : tout est calculé, les rapports sont écrits, la base
n'est pas touchée. --appliquer écrit dans UNE transaction, après pg_dump.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from identity.etage_r_dossiers import RAPPORTS, ErreurEtageR, dsn

SQL_POPULATION = Path(__file__).resolve().parent / "sql" / "etage_r_promotion.sql"

SCORE_PROMO = 0.94
SERIE_1428 = 1428
TOTAL_SERIES = 14670  # work_identity : sert au calcul de couverture

app = typer.Typer(add_completion=False, help=__doc__)

INSERT_PROMOTIONS = """
INSERT INTO manga.match_decision
    (series_id, wikidata_qid, method, score, status, decided_by, details)
SELECT series_id, d_qid, 'llm_review', %s, 'auto', 'etage_r',
    jsonb_build_object(
        'case', strate, 'source', 'etage_r_run2', 'avis_id', avis_id,
        'verdict', 'same_work', 'confiance', 'haute',
        'candidat_type', candidat_type, 'candidat_id', candidat_id,
        'modele', modele, 'prompt_version', prompt_version,
        'identite', jsonb_build_object('qid', d_qid, 'kitsu_id', d_kitsu,
            'mal_id', d_mal, 'anilist_id', d_anilist))
FROM promo_final
"""

UPDATE_IDENTITES = """
UPDATE manga.work_identity w
SET wikidata_qid = COALESCE(w.wikidata_qid, f.d_qid),
    kitsu_id     = COALESCE(w.kitsu_id, f.d_kitsu),
    mal_id       = COALESCE(w.mal_id, f.d_mal),
    anilist_id   = COALESCE(w.anilist_id, f.d_anilist),
    updated_at   = now()
FROM promo_final f
WHERE w.series_id = f.series_id
"""


def verifier_prerequis(cur) -> None:
    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid='manga.match_decision'::regclass "
        "  AND conname='match_decision_method_check'"
    )
    row = cur.fetchone()
    if not row or "human_review" not in row[0]:
        raise ErreurEtageR(
            "Migration 011 non appliquée : 'human_review' absent du CHECK "
            "match_decision.method — STOP (pas d'écriture sans le contrat)."
        )
    cur.execute("SELECT count(*) FROM manga.llm_avis WHERE phase='file'")
    if cur.fetchone()[0] == 0:
        raise ErreurEtageR("Aucun avis phase='file' — le run 1 a-t-il tourné ?")


def snapshot(cur) -> dict:
    cur.execute("SELECT status, count(*) FROM manga.v_match_current GROUP BY status")
    par_statut = dict(cur.fetchall())
    cur.execute(
        "SELECT count(*) FILTER (WHERE wikidata_qid IS NOT NULL), "
        "count(*) FILTER (WHERE kitsu_id IS NOT NULL), "
        "count(*) FILTER (WHERE mal_id IS NOT NULL), "
        "count(*) FILTER (WHERE anilist_id IS NOT NULL) FROM manga.work_identity"
    )
    qid, kitsu, mal, anilist = cur.fetchone()
    cur.execute("SELECT count(*) FROM manga.match_decision")
    return {
        "auto": par_statut.get("auto", 0),
        "needs_review": par_statut.get("needs_review", 0),
        "identites": {"qid": qid, "kitsu": kitsu, "mal": mal, "anilist": anilist},
        "decisions": cur.fetchone()[0],
    }


def construire_population(cur) -> None:
    cur.execute(SQL_POPULATION.read_text(encoding="utf-8"))


def mesures(cur) -> dict:
    cur.execute("SELECT count(DISTINCT series_id) FROM promo_avis")
    avis_series = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM promo_unique")
    uniques = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM promo_collision")
    collisions = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM promo_final")
    promus = cur.fetchone()[0]
    cur.execute("SELECT strate, count(*) FROM promo_final GROUP BY strate ORDER BY 1")
    par_strate = dict(cur.fetchall())
    cur.execute(
        "SELECT count(*) FILTER (WHERE d_qid IS NOT NULL), "
        "count(*) FILTER (WHERE d_kitsu IS NOT NULL), "
        "count(*) FILTER (WHERE d_mal IS NOT NULL), "
        "count(*) FILTER (WHERE d_anilist IS NOT NULL) FROM promo_final"
    )
    q, k, m, a = cur.fetchone()
    return {
        "multi_exclus": avis_series - uniques,
        "uniques": uniques,
        "collisions": collisions,
        "promus": promus,
        "par_strate": par_strate,
        "identites_derivees": {"qid": q, "kitsu": k, "mal": m, "anilist": a},
    }


def corriger_1428(cur) -> dict:
    cur.execute(
        "SELECT count(*) FROM manga.match_decision "
        "WHERE series_id=%s AND method='human_review'",
        (SERIE_1428,),
    )
    if cur.fetchone()[0] > 0:
        return {"applique": False, "raison": "correction déjà présente (idempotence)"}

    cur.execute(
        "SELECT wikidata_qid, kitsu_id, mal_id, anilist_id "
        "FROM manga.work_identity WHERE series_id=%s",
        (SERIE_1428,),
    )
    row = cur.fetchone()
    if row is None:
        raise ErreurEtageR(f"série {SERIE_1428} absente de work_identity — STOP.")
    qid, kitsu, mal, anilist = row

    cur.execute(
        "SELECT decision_id FROM manga.match_decision "
        "WHERE series_id=%s AND method='kitsu_bridge' "
        "ORDER BY decision_id DESC LIMIT 1",
        (SERIE_1428,),
    )
    faux = cur.fetchone()
    cur.execute(
        "SELECT coalesce(array_agg(avis_id), '{}') FROM manga.llm_avis "
        "WHERE series_id=%s AND phase='etalonnage'",
        (SERIE_1428,),
    )
    avis_etal = cur.fetchone()[0]

    details = json.dumps(
        {
            "case": "correction_faux_positif_pont",
            "motif": (
                "auteurs distincts (Knife Senno vs Gō Zappa) — faux positif "
                "kitsu_bridge confirmé humainement, avis LLM concordant"
            ),
            "decision_fautive": faux[0] if faux else None,
            "avis_etalonnage": avis_etal,
            "exclusion_pont": {"kitsu_id": kitsu, "qid": qid},
        },
        ensure_ascii=False,
    )
    cur.execute(
        "INSERT INTO manga.match_decision "
        "(series_id, wikidata_qid, method, score, status, decided_by, details) "
        "VALUES (%s, %s, 'human_review', NULL, 'rejected', 'human', %s::jsonb)",
        (SERIE_1428, qid, details),
    )
    cur.execute(
        "UPDATE manga.work_identity SET wikidata_qid=NULL, kitsu_id=NULL, "
        "mal_id=NULL, anilist_id=NULL, updated_at=now() WHERE series_id=%s",
        (SERIE_1428,),
    )
    return {
        "applique": True,
        "identite_effacee": {
            "qid": qid,
            "kitsu": kitsu,
            "mal": mal,
            "anilist": anilist,
        },
        "exclusion_pont": {"series_id": SERIE_1428, "kitsu_id": kitsu},
    }


# --------------------------------------------------------------------------- #
# Livrables résiduels
# --------------------------------------------------------------------------- #
SQL_UNDECIDABLE = (
    "SELECT series_id, phase, candidat_type, candidat_id, justification "
    "FROM manga.llm_avis WHERE verdict='undecidable' ORDER BY phase, series_id"
)
SQL_MOYENNE = (
    "SELECT series_id, candidat_type, candidat_id, justification "
    "FROM manga.llm_avis WHERE phase='file' AND verdict='same_work' "
    "AND confiance='moyenne' ORDER BY series_id"
)
# Les 60 séries multi-candidats, classées conflit vs fusible (mal/anilist partagés).
SQL_MULTI_CLASSE = """
WITH shb AS (
  SELECT a.series_id, a.candidat_type, a.candidat_id
  FROM manga.llm_avis a JOIN manga.v_match_current v ON v.series_id=a.series_id
  WHERE a.phase='file' AND a.verdict='same_work' AND a.confiance='haute'
    AND a.dossier_partiel=false AND v.status='needs_review'),
multi AS (SELECT series_id FROM shb GROUP BY series_id HAVING count(*)>1),
cand AS (
  SELECT s.series_id, s.candidat_type, s.candidat_id,
    CASE WHEN s.candidat_type='qid'
      THEN (SELECT p.mal_id FROM manga.wd_pivot p WHERE p.qid=s.candidat_id)
      ELSE (SELECT km.external_id FROM manga.kitsu_mappings km
            WHERE km.kitsu_id=s.candidat_id::bigint
              AND km.external_site='myanimelist/manga' LIMIT 1) END AS mal_id,
    CASE WHEN s.candidat_type='qid'
      THEN (SELECT p.anilist_id FROM manga.wd_pivot p WHERE p.qid=s.candidat_id)
      ELSE (SELECT km.external_id FROM manga.kitsu_mappings km
            WHERE km.kitsu_id=s.candidat_id::bigint
              AND km.external_site='anilist/manga' LIMIT 1) END AS anilist_id
  FROM shb s JOIN multi m ON m.series_id=s.series_id),
parserie AS (
  SELECT series_id,
    string_agg(candidat_type||':'||candidat_id, ' | '
               ORDER BY candidat_id) AS candidats,
    count(DISTINCT mal_id) FILTER (WHERE mal_id IS NOT NULL) mal_dist,
    count(DISTINCT anilist_id) FILTER (WHERE anilist_id IS NOT NULL) ani_dist,
    count(*) FILTER (WHERE mal_id IS NULL AND anilist_id IS NULL) sans_lien
  FROM cand GROUP BY series_id)
SELECT series_id,
  CASE WHEN mal_dist<=1 AND ani_dist<=1 AND sans_lien=0
       THEN 'fusible' ELSE 'conflit' END AS nature,
  candidats
FROM parserie ORDER BY nature, series_id
"""


def ecrire_file_humaine(cur, chemin: Path) -> dict:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    comptes: dict[str, int] = {}
    with chemin.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series_id", "nature", "detail", "VERDICT_HUMAIN", "NOTES"])
        cur.execute(SQL_UNDECIDABLE)
        for sid, phase, ct, cid, just in cur.fetchall():
            w.writerow([sid, f"undecidable_{phase}", f"{ct}:{cid} — {just}", "", ""])
            comptes["undecidable"] = comptes.get("undecidable", 0) + 1
        cur.execute(SQL_MULTI_CLASSE)
        for sid, nature, candidats in cur.fetchall():
            w.writerow([sid, nature, candidats, "", ""])
            comptes[nature] = comptes.get(nature, 0) + 1
        cur.execute("SELECT series_id, nature FROM promo_collision ORDER BY series_id")
        for sid, nature in cur.fetchall():
            w.writerow([sid, f"collision_{nature}", "", "", ""])
            comptes["collision"] = comptes.get("collision", 0) + 1
    return comptes


def ecrire_reserve_moyenne(cur, chemin: Path) -> int:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    cur.execute(SQL_MOYENNE)
    lignes = cur.fetchall()
    with chemin.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series_id", "candidat_type", "candidat_id", "justification"])
        w.writerows(lignes)
    return len(lignes)


def _couverture(auto: int) -> str:
    return f"{auto} / {TOTAL_SERIES} = {auto / TOTAL_SERIES:.1%}"


def ecrire_entonnoir(chemin: Path, avant: dict, m: dict, corr: dict) -> list[str]:
    id_ = m["identites_derivees"]
    auto_apres = avant["auto"] + m["promus"] - (1 if corr["applique"] else 0)
    lignes = [
        "# Entonnoir de promotion — étage R, run 2",
        "",
        "Régime avis-seulement LEVÉ, borné par les mesures (étalonnage 100 % /",
        "arbitrage C3). Journal append-only, jamais d'écrasement d'identité.",
        "",
        "## Entonnoir",
        "",
        "| Étape | Séries |",
        "|---|---:|",
        f"| éligibles (same_work haute, non partiel, needs_review) "
        f"| {m['uniques'] + m['multi_exclus']} |",
        f"| − candidats multiples (→ humain) | −{m['multi_exclus']} |",
        f"| = candidat unique | {m['uniques']} |",
        f"| − collisions d'identité (groupe exclu) | −{m['collisions']} |",
        f"| = **promues en 'auto' (llm_review)** | **{m['promus']}** |",
        "",
        "## Par strate",
        "",
        "| Strate (details.case) | Promues |",
        "|---|---:|",
    ]
    for strate, n in sorted(m["par_strate"].items()):
        lignes.append(f"| `{strate}` | {n} |")
    lignes += [
        "",
        "## Identités dérivées des promues",
        "",
        f"- qid : {id_['qid']} · kitsu_id : {id_['kitsu']} · mal_id : {id_['mal']} "
        f"· anilist_id : {id_['anilist']}",
        "- (une identité mal/anilist sans qid est une identité partielle légitime)",
        "",
        "## Couverture avant / après",
        "",
        f"- auto AVANT : {_couverture(avant['auto'])}",
        f"- auto APRÈS (projeté) : {_couverture(auto_apres)}",
        f"- needs_review : {avant['needs_review']} → "
        f"{avant['needs_review'] - m['promus']} "
        f"(1428 : auto → rejected, hors needs_review)",
        "",
        "## Correction humaine",
        "",
        f"- série 1428 : {'appliquée' if corr['applique'] else corr.get('raison')}",
        "",
        "Hors périmètre (comptés, non touchés) : same_work MOYENNE réservés ; "
        "different_work restent needs_review ; undecidable → file humaine.",
    ]
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("\n".join(lignes), encoding="utf-8")
    return lignes


def ecrire_corrections(chemin: Path, corr: dict) -> None:
    lignes = ["# Corrections humaines — étage R, run 2", ""]
    if not corr["applique"]:
        lignes.append(f"Série 1428 : **non appliquée** — {corr.get('raison')}.")
    else:
        eff = corr["identite_effacee"]
        lignes += [
            "## Série 1428 « Sister » (Knife Senno) — faux positif du pont",
            "",
            "Le `kitsu_bridge` vers **Q1045285 « Chocotto Sister »** (Gō Zappa) est",
            "FAUX : auteurs distincts, vérifié humainement, avis LLM concordant.",
            "",
            "- décision écrite : `human_review` / `rejected` (append-only, la ligne",
            "  `kitsu_bridge` fautive n'est ni modifiée ni supprimée) ;",
            f"- identité remise à NULL : qid `{eff['qid']}`, "
            f"kitsu_id `{eff['kitsu']}`, mal_id `{eff['mal']}`, "
            f"anilist_id `{eff['anilist']}` ;",
            f"- exclusion du pont consignée : (série {SERIE_1428}, kitsu_id "
            f"`{eff['kitsu']}`) — une re-passe ne doit pas reproduire l'erreur.",
            "",
            "La série redevient vierge : éligible aux autres étages / à la v2.",
        ]
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("\n".join(lignes), encoding="utf-8")


@app.command()
def promouvoir(
    rapport_dir: str = typer.Option(None),  # noqa: B008
    appliquer: bool = typer.Option(False, help="Écrire (défaut : dry-run)"),  # noqa: B008
) -> None:
    """Promeut les verdicts LLM éligibles + corrige 1428. Dry-run par défaut."""
    dest = (
        Path(rapport_dir)
        if rapport_dir
        else RAPPORTS / ("run2_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    )
    with psycopg.connect(dsn()) as cx:
        with cx.cursor() as cur:
            verifier_prerequis(cur)
            avant = snapshot(cur)
            construire_population(cur)
            m = mesures(cur)
            corr = corriger_1428(cur)  # calcule (et écrit dans la transaction)

            comptes_hum = ecrire_file_humaine(cur, dest / "file_humaine_residuelle.csv")
            n_moyenne = ecrire_reserve_moyenne(
                cur, dest / "reserve_confiance_moyenne.csv"
            )

            if appliquer:
                cur.execute(INSERT_PROMOTIONS, (SCORE_PROMO,))
                cur.execute(UPDATE_IDENTITES)
                cx.commit()
                mode = "APPLIQUÉ"
            else:
                cx.rollback()
                mode = "DRY-RUN (rien écrit)"

        lignes = ecrire_entonnoir(dest / "entonnoir_promotion.md", avant, m, corr)
        ecrire_corrections(dest / "corrections.md", corr)

    for ligne in lignes:
        typer.echo(ligne)
    typer.echo("")
    typer.echo(f"file humaine résiduelle : {comptes_hum} (→ {dest})")
    typer.echo(f"réserve confiance moyenne : {n_moyenne}")
    typer.echo(f"MODE : {mode}")


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
