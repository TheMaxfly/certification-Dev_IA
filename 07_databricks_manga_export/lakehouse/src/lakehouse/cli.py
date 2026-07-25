"""CLI Typer — les jobs du lakehouse (pattern du projet : `def main(): app()`).

Commandes : `ingest` (paramétré --source/--snapshot), `silver`, `gold`,
`rapport` (lisent tout le médaillon car les métriques sont inter-snapshots),
`synthese` (lecteur hors Spark) et `pipeline` (bout-en-bout, pour la démo
conteneur). Le même code tourne en local mode et dans le conteneur.
"""

from __future__ import annotations

import typer

# Alias : les commandes s'appellent `silver`/`gold` (noms CLI) et ne doivent
# pas masquer les modules du même nom.
from . import bronze
from . import gold as gold_mod
from . import silver as silver_mod
from .config import SEUILS, SOURCES, SeuilsAlerte, chemin_table
from .spark import build_spark

app = typer.Typer(
    add_completion=False,
    help="Lakehouse Spark+Delta — contrôles qualité historisés du raw multi-snapshots.",
)


def _sources_pour(snapshot: str) -> list[str]:
    return [n for n, s in SOURCES.items() if snapshot in s.snapshots]


@app.command()
def ingest(
    source: str = typer.Option(
        ..., "--source", help="Nom de source, ou 'all' pour toutes celles du snapshot."
    ),
    snapshot: str = typer.Option(
        ..., "--snapshot", help="Snapshot daté (ex. 2026-07)."
    ),
    shuffle_partitions: int = typer.Option(8, "--shuffle-partitions"),
) -> None:
    """BRONZE : ingère (source, snapshot) — idempotent (replaceWhere)."""
    spark = build_spark("lakehouse-ingest", shuffle_partitions=shuffle_partitions)
    try:
        noms = _sources_pour(snapshot) if source == "all" else [source]
        for nom in noms:
            m = bronze.ingest(spark, nom, snapshot)
            drapeau = "" if m["ecart"] in (0, None) else f"  ⚠️ écart={m['ecart']}"
            typer.echo(
                f"bronze {m['table']:22} snapshot={snapshot} "
                f"source_records={m['source_records']} rows={m['rows_bronze']} "
                f"attendu={m['attendu']}{drapeau}"
            )
    finally:
        spark.stop()


@app.command()
def silver(shuffle_partitions: int = typer.Option(8, "--shuffle-partitions")) -> None:
    """SILVER : typage MS (volumes, critiques) + grain série par snapshot."""
    spark = build_spark("lakehouse-silver", shuffle_partitions=shuffle_partitions)
    try:
        for m in silver_mod.build_all(spark):
            typer.echo(f"silver {m['table']:20} rows={m['rows']}")
    finally:
        spark.stop()


@app.command()
def gold(shuffle_partitions: int = typer.Option(8, "--shuffle-partitions")) -> None:
    """GOLD : les 5 tables de métriques qualité historisées."""
    spark = build_spark("lakehouse-gold", shuffle_partitions=shuffle_partitions)
    try:
        for m in gold_mod.build_all(spark):
            typer.echo(f"gold {m['table']:28} rows={m['rows']}")
    finally:
        spark.stop()


@app.command()
def rapport(
    delta_pct_anormal: float = typer.Option(
        SEUILS.delta_pct_anormal, "--delta-pct-anormal", help="Seuil |Δ%| volumétrie."
    ),
    prefixe_deficit_min: int = typer.Option(
        SEUILS.prefixe_deficit_min,
        "--prefixe-deficit-min",
        help="Seuil disparues/préfixe.",
    ),
    run_id: str = typer.Option(
        "", "--run-id", help="Horodatage forcé (sinon UTC now)."
    ),
) -> None:
    """RAPPORT : démo rétrospective markdown, lue depuis les tables gold."""
    from .report import generer_rapport

    spark = build_spark("lakehouse-rapport")
    try:
        seuils = SeuilsAlerte(
            delta_pct_anormal=delta_pct_anormal,
            prefixe_deficit_min=prefixe_deficit_min,
        )
        chemin = generer_rapport(spark, seuils=seuils, horodatage=run_id or None)
        typer.echo(f"rapport écrit : {chemin}")
    finally:
        spark.stop()


@app.command()
def synthese() -> None:
    """SYNTHÈSE : lecteur hors Spark (DuckDB) — la boucle C1 côté consommateur."""
    from .reader import afficher_synthese

    afficher_synthese()


@app.command()
def pipeline(
    shuffle_partitions: int = typer.Option(8, "--shuffle-partitions"),
) -> None:
    """PIPELINE bout-en-bout (démo conteneur) : bronze tous snapshots →
    silver → gold → rapport. Idempotent."""
    spark = build_spark("lakehouse-pipeline", shuffle_partitions=shuffle_partitions)
    try:
        for snapshot in ("2025-12", "2026-07"):
            for nom in _sources_pour(snapshot):
                m = bronze.ingest(spark, nom, snapshot)
                typer.echo(
                    f"bronze {m['table']:22} {snapshot} rows={m['rows_bronze']} "
                    f"ecart={m['ecart']}"
                )
        for m in silver_mod.build_all(spark):
            typer.echo(f"silver {m['table']:20} rows={m['rows']}")
        for m in gold_mod.build_all(spark):
            typer.echo(f"gold {m['table']:28} rows={m['rows']}")
        from .report import generer_rapport

        chemin = generer_rapport(spark)
        typer.echo(f"rapport écrit : {chemin}")
    finally:
        spark.stop()


@app.command()
def verifier() -> None:
    """VÉRIFICATIONS imposées : confronte l'état du lakehouse aux attendus
    (comptes bronze, incidents gold) et à la lecture externe. Sort non-zéro si
    un contrôle dur échoue. Lecture seule."""
    spark = build_spark("lakehouse-verif")
    resultats: list[tuple[str, object, object, bool]] = []

    def lire(table: str):
        return spark.read.format("delta").load(str(chemin_table(table)))

    def compte(table: str, cond: str | None = None) -> int:
        df = lire(table)
        return (df.where(cond) if cond else df).count()

    try:
        for table, cond, attendu in [
            ("bronze.ms_volumes", "snapshot_date='2025-12'", 89_188),
            ("bronze.ms_volumes", "snapshot_date='2026-07'", 103_811),
            ("bronze.ms_reviews", "snapshot_date='2025-12'", 6_749),
            ("bronze.ms_reviews", "snapshot_date='2026-07'", 11_052),
            ("bronze.kitsu_manga", None, 62_768),
            ("bronze.mi_sorties", None, 59_062),
        ]:
            libelle = f"{table} {cond or ''}".strip()
            try:
                n = compte(table, cond)
                resultats.append((libelle, n, attendu, n == attendu))
            except Exception:
                resultats.append((f"{libelle} (table absente)", None, attendu, False))

        crit = (
            lire("gold.volumetrie")
            .where("grain='critiques' and snapshot_date='2026-07'")
            .first()
        )
        dpct = crit["delta_pct"] if crit else None
        resultats.append(
            (
                "gold.volumetrie critiques Δ% (flag)",
                dpct,
                "≥20",
                bool(dpct and dpct >= 20),
            )
        )

        di = (
            lire("gold.completude_par_prefixe")
            .where("prefixe='di' and snapshot_date='2026-07'")
            .first()
        )
        disp = di["disparues"] if di else None
        resultats.append(("gold.completude « di » disparues", disp, 9, disp == 9))

        remp = lire("gold.remplissage_champs")
        rb12 = remp.where("champ='review_body' and snapshot_date='2025-12'").first()
        rb07 = remp.where("champ='review_body' and snapshot_date='2026-07'").first()
        v12 = rb12["pct_non_vide"] if rb12 else None
        v07 = rb07["pct_non_vide"] if rb07 else None
        ok_rb = bool(v12 and abs(v12 - 47.22) < 1 and v07 and v07 >= 99.9)
        resultats.append(
            ("gold.remplissage review_body", f"{v12}→{v07}", "≈47.2→99.99", ok_rb)
        )

        rec = lire("gold.recouvrement_snapshots").where("cle_type='volume_url'").first()
        rdisp = rec["disparues"] if rec else None
        resultats.append(
            ("gold.recouvrement volume_url disparues", rdisp, 302, rdisp == 302)
        )

        # Lecteur externe (hors Spark) : parité avec gold sur la clé EAN valide.
        from .reader import synthese

        ean_reader = {r[0]: r for r in synthese()["qualite_ean"]}.get("2026-07")
        vr = ean_reader[3] if ean_reader else None
        eg = lire("gold.qualite_ean").where("snapshot_date='2026-07'").first()
        vg = eg["cle_valide"] if eg else None
        resultats.append(
            ("lecteur externe = gold (cle_valide 2026-07)", vr, vg, vr == vg)
        )

        typer.echo("\n=== Vérifications imposées ===")
        tout_ok = True
        for libelle, obtenu, attendu, ok in resultats:
            tout_ok = tout_ok and ok
            marque = "✓" if ok else "✗"
            typer.echo(f"  [{marque}] {libelle} : {obtenu} (attendu {attendu})")
        typer.echo("  [✓] raw : lecture seule (aucune écriture)")
        typer.echo("\n" + ("TOUT VERT" if tout_ok else "ÉCHECS présents"))
        if not tout_ok:
            raise typer.Exit(1)
    finally:
        spark.stop()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
