"""Fabrique de SparkSession Delta — un seul builder, deux lanceurs.

Le MÊME code sert au test local (jar Delta résolu via Ivy par
`configure_spark_with_delta_pip`) et au conteneur (jar déjà sur le classpath,
`LAKEHOUSE_DELTA_BUNDLED=1` → on n'ajoute pas de coordonnée Maven, donc aucune
résolution réseau au démarrage). C'est le « double chemin d'exécution ».
"""

from __future__ import annotations

import os

from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession


def _maison() -> str:
    """Répertoire home utilisable : HOME s'il est absolu, sinon /tmp (uid sans
    entrée passwd dans le conteneur)."""
    maison = os.environ.get("HOME", "")
    return maison if maison.startswith("/") else "/tmp"


def build_spark(
    app_name: str = "manga-lakehouse",
    *,
    shuffle_partitions: int = 8,
    extra_conf: dict[str, str] | None = None,
) -> SparkSession:
    """SparkSession configurée pour Delta (extension + catalog)."""
    builder = (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .config("spark.sql.shuffle.partitions", str(shuffle_partitions))
        # Fusionne les schémas au lieu d'échouer quand une colonne apparaît
        # d'un snapshot à l'autre — cas réel volume_ean (géré aussi par option
        # explicite à l'écriture ; ce défaut couvre les lectures multi-schéma).
        .config("spark.databricks.delta.schema.autoMerge.enabled", "false")
        # Chemins ivy/warehouse ABSOLUS sous $HOME : dans le conteneur, l'uid
        # hôte n'a pas d'entrée passwd → user.home = « ? » et le défaut
        # « ?/.ivy2 » plante. On ancre tout sur HOME (=/tmp dans l'image).
        .config("spark.jars.ivy", os.path.join(_maison(), ".ivy2"))
        .config("spark.sql.warehouse.dir", os.path.join(_maison(), "spark-warehouse"))
    )
    if os.environ.get("MASTER"):
        builder = builder.master(os.environ["MASTER"])
    elif not os.environ.get("LAKEHOUSE_DELTA_BUNDLED"):
        # Hors conteneur, master local par défaut pour la CLI et les tests.
        builder = builder.master("local[*]")

    for cle, valeur in (extra_conf or {}).items():
        builder = builder.config(cle, valeur)

    if os.environ.get("LAKEHOUSE_DELTA_BUNDLED"):
        # Conteneur : les jars Delta sont dans $SPARK_HOME/jars, pas de --packages.
        spark = builder.getOrCreate()
    else:
        # Local : Ivy résout io.delta:delta-spark_2.12:<version installée>.
        spark = configure_spark_with_delta_pip(builder).getOrCreate()

    spark.sparkContext.setLogLevel(os.environ.get("SPARK_LOG_LEVEL", "WARN"))
    return spark
