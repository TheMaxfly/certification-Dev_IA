"""Time travel Delta : DESCRIBE HISTORY + lecture de la version 0.

Prouve que l'historisation Delta est exploitable (audit, rejeu d'un état passé).
"""

from __future__ import annotations

from delta.tables import DeltaTable

from lakehouse.config import chemin_table


def test_time_travel(spark, lakehouse_tmp):
    chemin = str(chemin_table("bronze._tt_demo"))
    spark.createDataFrame([(1,)], "x int").write.format("delta").save(chemin)
    spark.createDataFrame([(2,)], "x int").write.format("delta").mode("append").save(
        chemin
    )

    historique = DeltaTable.forPath(spark, chemin).history()
    assert historique.count() >= 2  # au moins deux versions journalisées

    version0 = spark.read.format("delta").option("versionAsOf", 0).load(chemin)
    assert version0.count() == 1  # état initial reconstituable

    courant = spark.read.format("delta").load(chemin)
    assert courant.count() == 2
