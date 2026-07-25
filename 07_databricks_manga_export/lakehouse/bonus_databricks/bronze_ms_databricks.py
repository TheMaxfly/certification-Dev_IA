# Databricks notebook source
# MAGIC %md
# MAGIC # Bonus — bronze MS sur Databricks + Unity Catalog
# MAGIC
# MAGIC **Même code, autre cible.** Ce notebook porte le job d'ingestion bronze
# MAGIC Manga Sanctuary (cf. `lakehouse/src/lakehouse/bronze.py`) sur Databricks
# MAGIC Free/Community avec **Unity Catalog** : format et paradigme identiques
# MAGIC (Delta, médaillon, partition par `snapshot_date`, idempotence
# MAGIC `replaceWhere`, `mergeSchema`), plateforme optionnelle.
# MAGIC
# MAGIC **Non bloquant** : si ce livrable échoue (quotas, compte expiré), le
# MAGIC module 07 reste complet — le chemin local + conteneur est la référence.
# MAGIC
# MAGIC ## Prérequis
# MAGIC - Un cluster avec un runtime Delta (DBR standard) — Spark/Delta fournis.
# MAGIC - Un **Volume** Unity Catalog contenant le raw MS déposé, p. ex.
# MAGIC   `/Volumes/<catalog>/<schema>/raw/2026-07/manga_sanctuary_volumes.jsonl`.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog UC")
dbutils.widgets.text("schema", "manga_bronze", "Schema UC")
dbutils.widgets.text("snapshot", "2026-07", "Snapshot")
dbutils.widgets.text(
    "volume_raw",
    "/Volumes/workspace/manga_bronze/raw",
    "Base du Volume contenant le raw MS",
)

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
snapshot = dbutils.widgets.get("snapshot")
volume_raw = dbutils.widgets.get("volume_raw")

chemin_source = f"{volume_raw}/{snapshot}/manga_sanctuary_volumes.jsonl"
table_cible = f"{catalog}.{schema}.ms_volumes"

# COMMAND ----------

import re

from pyspark.sql import functions as F

_CAR_INTERDITS = re.compile(r"[ ,;{}()\n\t=.:]+")


def assainir_noms(df):
    """Mêmes noms de colonnes acceptés par Delta que dans le job local."""
    vus, renoms = {}, []
    for nom in df.columns:
        propre = _CAR_INTERDITS.sub("_", nom).strip("_") or "col"
        if propre in vus:
            vus[propre] += 1
            propre = f"{propre}_{vus[propre]}"
        else:
            vus[propre] = 0
        renoms.append((nom, propre))
    for avant, apres in renoms:
        if avant != apres:
            df = df.withColumnRenamed(avant, apres)
    return df


# COMMAND ----------

spark.sql(f"CREATE CATALOG IF NOT EXISTS {catalog}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")

brut = spark.read.json(chemin_source)
df = (
    assainir_noms(brut)
    .withColumn("source_file", F.lit(chemin_source))
    .withColumn("ingested_at", F.current_timestamp())
    .withColumn("snapshot_date", F.lit(snapshot))
)

source_records = df.count()
print(f"lignes source lues : {source_records}")

# COMMAND ----------

# Idempotence : replaceWhere sur la partition du snapshot ; mergeSchema pour la
# colonne volume_ean apparue en 2026-07 — exactement comme le job local.
existe = spark.catalog.tableExists(table_cible)
writer = (
    df.write.format("delta")
    .partitionBy("snapshot_date")
    .option("mergeSchema", "true")
)
if existe:
    writer = writer.mode("overwrite").option(
        "replaceWhere", f"snapshot_date = '{snapshot}'"
    )
else:
    writer = writer.mode("overwrite")
writer.saveAsTable(table_cible)

# COMMAND ----------

n = spark.table(table_cible).where(F.col("snapshot_date") == snapshot).count()
print(f"bronze {table_cible} — partition {snapshot} : {n} lignes")
# Confrontation au chiffre de contrôle (Étape 0) : 103 811 pour 2026-07.
attendu = {"2025-12": 89_188, "2026-07": 103_811}.get(snapshot)
if attendu is not None and n != attendu:
    print(f"⚠️ écart bronze↔attendu : {n} vs {attendu} (à investiguer, pas d'auto-correction)")

dbutils.notebook.exit(f"OK {table_cible} {snapshot} rows={n}")
