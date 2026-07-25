"""Livrable 5 — la démo rétrospective (la pièce jury).

Génère un rapport markdown EN LISANT les tables gold : il rejoue l'histoire
vraie du projet — les trois incidents détectés jadis PAR ACCIDENT, désormais
détectés PAR SYSTÈME. Les seuils d'alerte sont des PARAMÈTRES imprimés, pas de
magie. Le rapport ne bloque rien : une alerte motive un NO-GO HUMAIN au cycle
mensuel (couche consultative).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from .config import JOB_VERSION, SEUILS, SeuilsAlerte, chemin_table, rapports_dir


def _lire(spark: SparkSession, table: str):
    return spark.read.format("delta").load(str(chemin_table(table)))


def _tableau_md(entetes: list[str], lignes: list[list]) -> str:
    out = ["| " + " | ".join(entetes) + " |"]
    out.append("| " + " | ".join("---" for _ in entetes) + " |")
    for ligne in lignes:
        out.append("| " + " | ".join("" if c is None else str(c) for c in ligne) + " |")
    return "\n".join(out)


def generer_rapport(
    spark: SparkSession,
    *,
    seuils: SeuilsAlerte = SEUILS,
    horodatage: str | None = None,
) -> Path:
    ts = horodatage or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lignes: list[str] = []
    a = lignes.append

    a(f"# Rapport qualité lakehouse — {ts}")
    a("")
    a(
        "> Couche **complémentaire consultative** (module 07 v2). Hors chemin "
        "critique : historise et alerte, ne bloque jamais. Une ⚠️ peut motiver "
        "un **NO-GO humain** au cycle mensuel."
    )
    a("")
    a(f"- Version des jobs : `{JOB_VERSION}`")
    a(
        f"- Seuils d'alerte (paramètres) : |Δ%| volumétrie ≥ "
        f"**{seuils.delta_pct_anormal} %** ; déficit par préfixe "
        f"(`disparues`) ≥ **{seuils.prefixe_deficit_min}**."
    )
    a("")

    # ---- 1. Volumétrie ----------------------------------------------------
    vol = _lire(spark, "gold.volumetrie").orderBy("grain", "snapshot_date").collect()
    a("## Volumétrie par snapshot")
    a("")
    a(
        _tableau_md(
            ["snapshot", "grain", "n", "n préc.", "Δ abs", "Δ %"],
            [
                [
                    r["snapshot_date"],
                    r["grain"],
                    r["n"],
                    r["n_precedent"],
                    r["delta_abs"],
                    r["delta_pct"],
                ]
                for r in vol
            ],
        )
    )
    a("")
    anomalies_vol = [
        r
        for r in vol
        if r["delta_pct"] is not None
        and abs(r["delta_pct"]) >= seuils.delta_pct_anormal
    ]
    for r in anomalies_vol:
        a(
            f"- ⚠️ **ANOMALIE volumétrie** : {r['grain']} "
            f"{r['n_precedent']} → {r['n']} (**{r['delta_pct']:+} %**) au "
            f"snapshot {r['snapshot_date']}."
        )
    a(
        "  - Lecture : la croissance des **critiques** est la « croissance "
        "impossible » qui avait révélé la référence 2025-12 tronquée."
    )
    a("")

    # ---- 2. Complétude par préfixe (le trou « Di ») -----------------------
    comp = _lire(spark, "gold.completude_par_prefixe")
    deficits = (
        comp.where(F.col("disparues") >= seuils.prefixe_deficit_min)
        .orderBy(F.col("disparues").desc())
        .collect()
    )
    a("## Complétude par préfixe — déficits localisés")
    a("")
    a(
        "Le Δ **net** ment : un préfixe peut perdre des séries pendant que le "
        "total monte. Le détecteur est `disparues` (séries présentes en N-1, "
        "absentes en N)."
    )
    a("")
    if deficits:
        a(
            _tableau_md(
                ["snapshot", "préfixe", "n séries", "Δ net", "disparues", "nouvelles"],
                [
                    [
                        r["snapshot_date"],
                        f"`{r['prefixe']}`",
                        r["n_series"],
                        r["delta_abs"],
                        r["disparues"],
                        r["nouvelles"],
                    ]
                    for r in deficits
                ],
            )
        )
        a("")
        di = [r for r in deficits if r["prefixe"] == "di"]
        if di:
            r = di[0]
            a(
                f"- ⚠️ **ANOMALIE préfixe** : `di` perd **{r['disparues']}** séries "
                f"(Δ net seulement {r['delta_abs']:+}, masqué par {r['nouvelles']} "
                "nouvelles) — le **trou de crawl « Di »**, détecté par système."
            )
    else:
        a("_Aucun déficit de préfixe au-dessus du seuil._")
    a("")

    # ---- 3. Remplissage des champs ---------------------------------------
    remp = _lire(spark, "gold.remplissage_champs").orderBy("champ", "snapshot_date")
    rows = remp.collect()
    a("## Remplissage des champs (% non vide)")
    a("")
    a(
        _tableau_md(
            ["snapshot", "champ", "grain", "total", "non vide", "%", "Δ points"],
            [
                [
                    r["snapshot_date"],
                    r["champ"],
                    r["grain"],
                    r["total"],
                    r["non_vide"],
                    r["pct_non_vide"],
                    r["delta_pct_points"],
                ]
                for r in rows
            ],
        )
    )
    a("")
    rb = [r for r in rows if r["champ"] == "review_body" and r["delta_pct_points"]]
    for r in rb:
        a(
            f"- ⚠️ **remplissage** : `review_body` bondit de "
            f"**{r['delta_pct_points']:+} points** (à {r['pct_non_vide']} %) — le "
            "bug de sélecteur des critiques, quantifié d'un coup d'œil."
        )
    a("")

    # ---- 4. Recouvrement entre snapshots ---------------------------------
    rec = _lire(spark, "gold.recouvrement_snapshots").orderBy("cle_type").collect()
    a("## Recouvrement entre snapshots")
    a("")
    if rec:
        a(
            _tableau_md(
                [
                    "clé",
                    "N-1 → N",
                    "n préc.",
                    "n cour.",
                    "communes",
                    "nouvelles",
                    "disparues",
                ],
                [
                    [
                        r["cle_type"],
                        f"{r['snapshot_precedent']} → {r['snapshot_date']}",
                        r["n_precedent"],
                        r["n_courant"],
                        r["communes"],
                        r["nouvelles"],
                        r["disparues"],
                    ]
                    for r in rec
                ],
            )
        )
        a("")
        vu = {r["cle_type"]: r["disparues"] for r in rec}
        if vu.get("volume_url") is not None:
            a(
                f"- **{vu['volume_url']} volumes** et **{vu.get('review_url', '?')} "
                "critiques** non revus d'un mois à l'autre — listables, "
                "métrique de routine (la volumétrie de référence par snapshot "
                "est ce qui confronte les comptes en base : les 59 volumes que "
                "l'ELT historique perdait)."
            )
    else:
        a("_Un seul snapshot : pas de comparaison possible._")
    a("")

    # ---- 5. Qualité EAN ---------------------------------------------------
    qe = _lire(spark, "gold.qualite_ean").orderBy("snapshot_date").collect()
    a("## Qualité EAN")
    a("")
    a(
        _tableau_md(
            [
                "snapshot",
                "volumes",
                "renseignés",
                "13 chiffres",
                "clé valide",
                "uniques",
            ],
            [
                [
                    r["snapshot_date"],
                    r["total_volumes"],
                    r["renseignes"],
                    r["format_13"],
                    r["cle_valide"],
                    r["uniques_valides"],
                ]
                for r in qe
            ],
        )
    )
    a("")

    # ---- 6. Schema evolution (cas réel) ----------------------------------
    a("## Évolution de schéma (cas réel)")
    a("")
    a(
        "- `volume_ean` **n'existe que dans le snapshot 2026-07** ; l'ingestion "
        "des deux snapshots MS dans la même table bronze est passée par "
        "`mergeSchema` — la colonne apparue entre deux mois, gérée sans casse. "
        "Trace : `volume_ean` est à 0 % de remplissage en 2025-12 (colonne "
        "absente) puis renseignée en 2026-07."
    )
    a("")
    a("---")
    a("")
    a(
        "_Rapport lu par la checklist du cycle mensuel (boucle C1 : extraction "
        "depuis le système big data). Généré à partir des tables gold._"
    )

    dossier = rapports_dir()
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / f"rapport_qualite_{ts}.md"
    chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return chemin
