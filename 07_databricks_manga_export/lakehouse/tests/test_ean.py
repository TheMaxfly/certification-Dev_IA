"""EAN-13 : 5 valeurs de référence croisées avec la base, + parité Python↔Spark.

Références : deux valeurs canoniques de B2 (une valide, une à clé fausse), une
trop courte, et deux EAN RÉELS de la source MI — dont la validité a été relevée
sur les données. La validité globale du snapshot 2026-07 (63 627 clés valides)
correspond au chiffre B2 (voir gold.qualite_ean), preuve croisée à l'échelle.
"""

from __future__ import annotations

import pytest
from pyspark.sql import functions as F

from lakehouse.ean import ean13_valide, expr_ean13_valide

CAS_REFERENCE = [
    ("9782355929489", True),  # B2 canonique, valide
    ("9782355929488", False),  # B2 canonique, dernier chiffre faux
    ("978235592948", False),  # 12 chiffres
    ("9791041113002", True),  # réel MI
    ("9782811678845", True),  # réel MI
]


@pytest.mark.parametrize("valeur,attendu", CAS_REFERENCE)
def test_ean13_python(valeur, attendu):
    assert ean13_valide(valeur) is attendu


def test_ean13_python_none():
    assert ean13_valide(None) is False


def test_ean13_spark_egale_python(spark):
    """L'expression Spark native rend EXACTEMENT ce que rend le Python."""
    df = spark.createDataFrame([(v,) for v, _ in CAS_REFERENCE], ["ean"])
    resultat = {
        r["ean"]: r["ok"]
        for r in df.withColumn("ok", expr_ean13_valide(F.col("ean"))).collect()
    }
    for valeur, attendu in CAS_REFERENCE:
        assert resultat[valeur] == attendu


def test_ean13_spark_null_est_false(spark):
    df = spark.createDataFrame([(None,)], "ean string")
    r = df.withColumn("ok", expr_ean13_valide(F.col("ean"))).first()
    assert r["ok"] is False
