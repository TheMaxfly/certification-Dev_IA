"""GOLD — métriques qualité historisées, conçues pour être COMPARÉES.

Cinq tables partitionnées par `snapshot_date`, une ligne par (snapshot,
dimension), chacune estampillée `computed_at` + `job_version`. Elles répondent
aux trois incidents réels du projet :

  - volumetrie            : une croissance/chute anormale FLASHE (Δ%) ;
  - completude_par_prefixe: un déficit localisé (« Di ») saute aux yeux quand
                            le total, lui, monte ;
  - remplissage_champs    : un corps de critique vide en masse devient une
                            ligne de tableau (le bug de sélecteur quantifié) ;
  - recouvrement_snapshots: les clés disparues d'un mois à l'autre, listables ;
  - qualite_ean           : la validité EAN par snapshot.

Le gold lit le silver (MS) et n'écrit que sous LAKEHOUSE_ROOT.
"""

from __future__ import annotations

from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

from .config import JOB_VERSION, chemin_table
from .normalize import expr_prefixe


def _finaliser(df: DataFrame) -> DataFrame:
    return df.withColumn("computed_at", F.current_timestamp()).withColumn(
        "job_version", F.lit(JOB_VERSION)
    )


def _ecrire(df: DataFrame, table: str) -> dict:
    ruta = str(chemin_table(table))
    _finaliser(df).write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).partitionBy("snapshot_date").save(ruta)
    return {"table": table, "rows": df.count(), "chemin": ruta}


def _non_vide_texte(colonne: Column) -> Column:
    return colonne.isNotNull() & (F.trim(colonne) != F.lit(""))


def _non_vide_liste(colonne: Column) -> Column:
    return colonne.isNotNull() & (F.size(colonne) > F.lit(0))


def _lire(spark: SparkSession, table: str) -> DataFrame:
    return spark.read.format("delta").load(str(chemin_table(table)))


def build_volumetrie(spark: SparkSession) -> dict:
    """Comptes par (snapshot, grain) + Δ absolu et Δ% vs snapshot précédent."""
    vols = _lire(spark, "silver.ms_volumes").groupBy("snapshot_date").count()
    revs = _lire(spark, "silver.ms_reviews").groupBy("snapshot_date").count()
    sers = _lire(spark, "silver.ms_series").groupBy("snapshot_date").count()
    union = (
        vols.withColumn("grain", F.lit("volumes"))
        .unionByName(revs.withColumn("grain", F.lit("critiques")))
        .unionByName(sers.withColumn("grain", F.lit("series")))
        .withColumnRenamed("count", "n")
    )
    w = Window.partitionBy("grain").orderBy("snapshot_date")
    prec = F.lag("n").over(w)
    df = (
        union.withColumn("n_precedent", prec)
        .withColumn("delta_abs", F.col("n") - prec)
        .withColumn(
            "delta_pct",
            F.when(
                prec.isNotNull() & (prec != 0),
                F.round((F.col("n") - prec) / prec * 100, 2),
            ),
        )
        .select("snapshot_date", "grain", "n", "n_precedent", "delta_abs", "delta_pct")
    )
    return _ecrire(df, "gold.volumetrie")


def build_completude_par_prefixe(spark: SparkSession) -> dict:
    """Séries par préfixe (2 car. normalisés) et snapshot + Δ net ET mouvement.

    C'est le contrôle né du trou « Di ». LEÇON DES DONNÉES : le Δ NET ment.
    Pour « di », 10 séries de 2025-12 ne sont pas re-collectées en 2026-07 mais
    9 nouvelles arrivent → Δ net = −1, le trou est masqué exactement comme la
    volumétrie globale le masque. Le vrai détecteur est `disparues` (séries
    présentes en N-1, absentes en N) : « di » = 10 disparues, soit les ~9 fiches
    vérifiées existantes du trou de crawl. On expose donc les deux : le net
    (`delta_abs`) et le mouvement brut (`disparues`, `nouvelles`).
    """
    ser = (
        _lire(spark, "silver.ms_series")
        .withColumn("prefixe", expr_prefixe(F.col("series_title"), 2))
        .select("snapshot_date", "series_id", "prefixe")
        .distinct()
    )
    comptes = ser.groupBy("snapshot_date", "prefixe").agg(
        F.countDistinct("series_id").alias("n_series")
    )
    w = Window.partitionBy("prefixe").orderBy("snapshot_date")
    prec = F.lag("n_series").over(w)
    comptes = comptes.withColumn("n_series_precedent", prec).withColumn(
        "delta_abs", F.col("n_series") - prec
    )

    snaps = [
        r["snapshot_date"]
        for r in ser.select("snapshot_date")
        .distinct()
        .orderBy("snapshot_date")
        .collect()
    ]
    mouvements = []
    for i in range(1, len(snaps)):
        prev_s, cur_s = snaps[i - 1], snaps[i]
        prev_sp = ser.where(F.col("snapshot_date") == prev_s).select(
            "series_id", "prefixe"
        )
        cur_sp = ser.where(F.col("snapshot_date") == cur_s).select(
            "series_id", "prefixe"
        )
        prev_ids = prev_sp.select("series_id").distinct()
        cur_ids = cur_sp.select("series_id").distinct()
        disp = (
            prev_sp.join(cur_ids, "series_id", "left_anti")
            .groupBy("prefixe")
            .agg(F.count(F.lit(1)).alias("disparues"))
            .withColumn("snapshot_date", F.lit(cur_s))
        )
        nouv = (
            cur_sp.join(prev_ids, "series_id", "left_anti")
            .groupBy("prefixe")
            .agg(F.count(F.lit(1)).alias("nouvelles"))
            .withColumn("snapshot_date", F.lit(cur_s))
        )
        mouvements.append(disp.join(nouv, ["snapshot_date", "prefixe"], "full_outer"))

    df = comptes
    cur_snaps = snaps[1:]
    if mouvements:
        mvt = mouvements[0]
        for m in mouvements[1:]:
            mvt = mvt.unionByName(m)
        df = df.join(mvt, ["snapshot_date", "prefixe"], "left")
        # Snapshot avec prédécesseur : absence de mouvement = 0. Premier
        # snapshot (sans prédécesseur) : mouvement indéfini = null.
        a_predecesseur = F.col("snapshot_date").isin(cur_snaps)
        df = df.withColumn(
            "disparues",
            F.when(a_predecesseur, F.coalesce(F.col("disparues"), F.lit(0))),
        ).withColumn(
            "nouvelles",
            F.when(a_predecesseur, F.coalesce(F.col("nouvelles"), F.lit(0))),
        )
    else:
        df = df.withColumn("disparues", F.lit(None).cast("long")).withColumn(
            "nouvelles", F.lit(None).cast("long")
        )

    df = df.select(
        "snapshot_date",
        "prefixe",
        "n_series",
        "n_series_precedent",
        "delta_abs",
        "disparues",
        "nouvelles",
    )
    return _ecrire(df, "gold.completude_par_prefixe")


def build_remplissage_champs(spark: SparkSession) -> dict:
    """% non vide par (champ, snapshot) + Δ% vs snapshot précédent."""
    vol = _lire(spark, "silver.ms_volumes")
    rev = _lire(spark, "silver.ms_reviews")
    ser = _lire(spark, "silver.ms_series")

    def stats(df: DataFrame, champ: str, grain: str, non_vide: Column) -> DataFrame:
        return (
            df.groupBy("snapshot_date")
            .agg(
                F.count(F.lit(1)).alias("total"),
                F.sum(non_vide.cast("int")).alias("non_vide"),
            )
            .withColumn("champ", F.lit(champ))
            .withColumn("grain", F.lit(grain))
        )

    parts = [
        stats(rev, "review_body", "critiques", _non_vide_texte(F.col("review_body"))),
        stats(vol, "volume_ean", "volumes", _non_vide_texte(F.col("volume_ean"))),
        stats(ser, "series_genres", "series", _non_vide_liste(F.col("series_genres"))),
        stats(ser, "series_tags", "series", _non_vide_liste(F.col("series_tags"))),
        stats(
            ser,
            "series_other_titles",
            "series",
            _non_vide_liste(F.col("series_other_titles")),
        ),
    ]
    union = parts[0]
    for p in parts[1:]:
        union = union.unionByName(p)
    union = union.withColumn(
        "pct_non_vide", F.round(F.col("non_vide") / F.col("total") * 100, 2)
    )
    w = Window.partitionBy("champ").orderBy("snapshot_date")
    prec = F.lag("pct_non_vide").over(w)
    df = union.withColumn(
        "delta_pct_points", F.round(F.col("pct_non_vide") - prec, 2)
    ).select(
        "snapshot_date",
        "champ",
        "grain",
        "total",
        "non_vide",
        "pct_non_vide",
        "delta_pct_points",
    )
    return _ecrire(df, "gold.remplissage_champs")


def _recouvrement_pour(
    spark: SparkSession, df: DataFrame, cle: str, cle_type: str
) -> list[tuple]:
    snaps = [
        r["snapshot_date"]
        for r in df.select("snapshot_date")
        .distinct()
        .orderBy("snapshot_date")
        .collect()
    ]
    lignes: list[tuple] = []
    for i in range(1, len(snaps)):
        prev_s, cur_s = snaps[i - 1], snaps[i]
        prev = (
            df.where(F.col("snapshot_date") == prev_s)
            .select(F.col(cle).alias("cle"))
            .where(F.col("cle").isNotNull())
            .distinct()
        )
        cur = (
            df.where(F.col("snapshot_date") == cur_s)
            .select(F.col(cle).alias("cle"))
            .where(F.col("cle").isNotNull())
            .distinct()
        )
        n_prev, n_cur = prev.count(), cur.count()
        communes = prev.join(cur, "cle", "inner").count()
        nouvelles = cur.join(prev, "cle", "left_anti").count()
        disparues = prev.join(cur, "cle", "left_anti").count()
        lignes.append(
            (cur_s, cle_type, prev_s, n_prev, n_cur, communes, nouvelles, disparues)
        )
    return lignes


def build_recouvrement_snapshots(spark: SparkSession) -> dict:
    """Par clé (volume_url, review_url, series_id) : communes/nouvelles/
    disparues entre snapshot N-1 et N. Les volumes/critiques disparus
    deviennent une métrique de routine (au lieu d'une découverte par accident).
    """
    vol = _lire(spark, "silver.ms_volumes")
    rev = _lire(spark, "silver.ms_reviews")
    ser = _lire(spark, "silver.ms_series")
    lignes = (
        _recouvrement_pour(spark, vol, "volume_url", "volume_url")
        + _recouvrement_pour(spark, rev, "review_url", "review_url")
        + _recouvrement_pour(spark, ser, "series_id", "series_id")
    )
    colonnes = [
        "snapshot_date",
        "cle_type",
        "snapshot_precedent",
        "n_precedent",
        "n_courant",
        "communes",
        "nouvelles",
        "disparues",
    ]
    if not lignes:
        # Un seul snapshot : pas de comparaison possible (table vide, pas d'erreur).
        vide = spark.createDataFrame(
            [], schema=", ".join(f"{c} string" for c in colonnes)
        )
        return _ecrire(vide, "gold.recouvrement_snapshots")
    df = spark.createDataFrame(lignes, schema=colonnes)
    # series_id arrive en string (createDataFrame) ; les comptes sont des longs.
    for c in ["n_precedent", "n_courant", "communes", "nouvelles", "disparues"]:
        df = df.withColumn(c, F.col(c).cast("long"))
    return _ecrire(df, "gold.recouvrement_snapshots")


def build_qualite_ean(spark: SparkSession) -> dict:
    """Validité EAN par snapshot : renseignés / 13 chiffres / clé valide / uniques."""
    vol = _lire(spark, "silver.ms_volumes")
    ean = F.col("volume_ean")
    df = vol.groupBy("snapshot_date").agg(
        F.count(F.lit(1)).alias("total_volumes"),
        F.sum(F.when(_non_vide_texte(ean), 1).otherwise(0)).alias("renseignes"),
        F.sum(F.when(ean.rlike(r"^\d{13}$"), 1).otherwise(0)).alias("format_13"),
        F.sum(F.when(F.col("volume_ean_valide"), 1).otherwise(0)).alias("cle_valide"),
        F.countDistinct(F.when(F.col("volume_ean_valide"), ean)).alias(
            "uniques_valides"
        ),
    )
    return _ecrire(df, "gold.qualite_ean")


def build_all(spark: SparkSession) -> list[dict]:
    return [
        build_volumetrie(spark),
        build_completude_par_prefixe(spark),
        build_remplissage_champs(spark),
        build_recouvrement_snapshots(spark),
        build_qualite_ean(spark),
    ]
