"""SILVER — typage aligné des sources MS (volumes, critiques) + grain série.

Ce cycle : MS d'abord (décision figée). Les autres sources restent bronze-only
— les métriques inter-snapshots naîtront à leur deuxième snapshot. Le silver
est une projection PURE du bronze : reconstructible, réécrit en entier
(overwrite) à chaque build, partitionné par `snapshot_date`.

Conventions du projet appliquées : `series_id` entier, année entière, dates
françaises → ISO (`volume_publication_date_iso`), EAN nettoyé + flag de validité
par la MÊME règle que B2 (`ean.expr_ean13_valide`). Les champs suivis par le
gold (genres/tags/autres-titres, corps de critique, EAN) sont conservés.
"""

from __future__ import annotations

from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import ArrayType, StringType

from .config import chemin_table
from .ean import expr_ean13_valide

# Abréviations de mois FR (accents inclus) → numéro. Le préfixe suffit à
# distinguer (« juin »/« juil. », « mars »/« mai »).
_MOIS_FR = [
    ("janv", 1),
    ("févr", 2),
    ("mars", 3),
    ("avr", 4),
    ("mai", 5),
    ("juin", 6),
    ("juil", 7),
    ("août", 8),
    ("sept", 9),
    ("oct", 10),
    ("nov", 11),
    ("déc", 12),
]


def expr_date_fr_iso(colonne: Column) -> Column:
    """Date FR « [jour.] JJ mois. AAAA » → date ISO ; null si non reconnue.

    Le préfixe jour-de-semaine (« mar. ») est ignoré : le jour est le nombre
    qui précède le mois, lui-même suivi de l'année à 4 chiffres.
    """
    s = F.trim(colonne.cast("string"))
    jour = F.regexp_extract(s, r"(\d{1,2})\s+\S+\.?\s+\d{4}", 1)
    mois_tok = F.lower(F.regexp_extract(s, r"\d{1,2}\s+(\S+?)\.?\s+\d{4}", 1))
    annee = F.regexp_extract(s, r"(\d{4})", 1)

    mois_num: Column = F.lit(None).cast("int")
    for prefixe, numero in _MOIS_FR:
        mois_num = F.when(mois_tok.startswith(prefixe), F.lit(numero)).otherwise(
            mois_num
        )

    iso = F.concat_ws(
        "-",
        annee,
        F.lpad(mois_num.cast("string"), 2, "0"),
        F.lpad(jour, 2, "0"),
    )
    valide = (annee != "") & (jour != "") & mois_num.isNotNull()
    return F.when(valide, F.to_date(iso, "yyyy-MM-dd")).otherwise(F.lit(None))


def _col_ou_null(df: DataFrame, nom: str, type_spark) -> Column:
    """Colonne `nom` si présente (le bronze peut ne pas l'avoir avant que le
    snapshot qui l'introduit soit ingéré), sinon un null typé — robustesse
    schema-evolution jusqu'au premier snapshot porteur de la colonne."""
    if nom in df.columns:
        return F.col(nom)
    return F.lit(None).cast(type_spark)


def build_ms_volumes(spark: SparkSession) -> dict:
    b = spark.read.format("delta").load(str(chemin_table("bronze.ms_volumes")))
    df = b.select(
        F.col("series_id").cast("int").alias("series_id"),
        F.col("series_title").cast("string").alias("series_title"),
        F.col("series_year").cast("int").alias("series_year"),
        F.col("volume_url").cast("string").alias("volume_url"),
        _col_ou_null(b, "volume_number", StringType())
        .cast("string")
        .alias("volume_number"),
        F.trim(_col_ou_null(b, "volume_ean", StringType()).cast("string")).alias(
            "volume_ean"
        ),
        _col_ou_null(b, "volume_publication_date", StringType())
        .cast("string")
        .alias("volume_publication_date"),
        _col_ou_null(b, "series_genres", ArrayType(StringType())).alias(
            "series_genres"
        ),
        _col_ou_null(b, "series_tags", ArrayType(StringType())).alias("series_tags"),
        _col_ou_null(b, "series_other_titles", ArrayType(StringType())).alias(
            "series_other_titles"
        ),
        F.col("snapshot_date"),
    )
    df = df.withColumn("volume_ean_valide", expr_ean13_valide(F.col("volume_ean")))
    df = df.withColumn(
        "volume_publication_date_iso",
        expr_date_fr_iso(F.col("volume_publication_date")),
    )
    ruta = str(chemin_table("silver.ms_volumes"))
    df.write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).partitionBy("snapshot_date").save(ruta)
    return {"table": "silver.ms_volumes", "rows": df.count(), "chemin": ruta}


def build_ms_reviews(spark: SparkSession) -> dict:
    b = spark.read.format("delta").load(str(chemin_table("bronze.ms_reviews")))
    df = b.select(
        F.col("series_id").cast("int").alias("series_id"),
        F.col("volume_url").cast("string").alias("volume_url"),
        _col_ou_null(b, "review_url", StringType()).cast("string").alias("review_url"),
        _col_ou_null(b, "review_body", StringType())
        .cast("string")
        .alias("review_body"),
        _col_ou_null(b, "review_score", StringType())
        .cast("double")
        .alias("review_score"),
        _col_ou_null(b, "review_date", StringType())
        .cast("string")
        .alias("review_date"),
        F.col("snapshot_date"),
    )
    ruta = str(chemin_table("silver.ms_reviews"))
    df.write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).partitionBy("snapshot_date").save(ruta)
    return {"table": "silver.ms_reviews", "rows": df.count(), "chemin": ruta}


def build_ms_series(spark: SparkSession) -> dict:
    """Grain série par snapshot (DISTINCT series_id), attributs série agrégés."""
    v = spark.read.format("delta").load(str(chemin_table("silver.ms_volumes")))
    df = v.groupBy("snapshot_date", "series_id").agg(
        F.first("series_title", ignorenulls=True).alias("series_title"),
        F.first("series_year", ignorenulls=True).alias("series_year"),
        F.first("series_genres", ignorenulls=True).alias("series_genres"),
        F.first("series_tags", ignorenulls=True).alias("series_tags"),
        F.first("series_other_titles", ignorenulls=True).alias("series_other_titles"),
    )
    ruta = str(chemin_table("silver.ms_series"))
    df.write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).partitionBy("snapshot_date").save(ruta)
    return {"table": "silver.ms_series", "rows": df.count(), "chemin": ruta}


def build_all(spark: SparkSession) -> list[dict]:
    """Silver dépend de bronze.ms_* ; ms_series dépend de silver.ms_volumes."""
    mesures = [build_ms_volumes(spark), build_ms_reviews(spark)]
    mesures.append(build_ms_series(spark))
    return mesures
