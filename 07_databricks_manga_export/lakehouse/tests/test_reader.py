"""Lecteur externe (DuckDB, hors Spark) : il lit EXACTEMENT ce que gold a écrit.

C'est la preuve de la boucle C1 côté consommateur — le cycle mensuel lit le
lakehouse sans démarrer la JVM. Les VALEURS des tables gold sont vérifiées par
`test_gold.py` ; ici, la seule question : DuckDB lit-il, table par table, les mêmes
lignes que Spark ?
"""

from __future__ import annotations

from lakehouse import reader
from lakehouse.config import chemin_table


def _par_spark(spark, table, colonnes, filtre=None):
    df = spark.read.format("delta").load(str(chemin_table(table))).select(*colonnes)
    if filtre is not None:
        df = df.where(filtre)
    return sorted(tuple(r[c] for c in colonnes) for r in df.collect())


def test_lecteur_lit_ce_que_gold_ecrit(spark, gold_fabrique):
    s = reader.synthese()  # ouvre sa propre connexion DuckDB

    # (snapshot_date, grain) est unique : le tri ne compare jamais les colonnes
    # suivantes, qui peuvent être nulles.
    colonnes_vol = [
        "snapshot_date",
        "grain",
        "n",
        "n_precedent",
        "delta_abs",
        "delta_pct",
    ]
    volumetrie = sorted(tuple(r[c] for c in colonnes_vol) for r in s["volumetrie"])
    assert volumetrie, "gold.volumetrie vide : le test ne prouverait rien"
    assert volumetrie == _par_spark(spark, "gold.volumetrie", colonnes_vol)

    recouvrement = sorted(tuple(r) for r in s["recouvrement"])
    assert recouvrement
    assert recouvrement == _par_spark(
        spark,
        "gold.recouvrement_snapshots",
        ["cle_type", "n_precedent", "n_courant", "disparues"],
    )

    ean = sorted(tuple(r) for r in s["qualite_ean"])
    assert ean
    assert ean == _par_spark(
        spark,
        "gold.qualite_ean",
        ["snapshot_date", "total_volumes", "renseignes", "cle_valide"],
    )

    assert sorted(tuple(r) for r in s["deficits_prefixe"]) == _par_spark(
        spark,
        "gold.completude_par_prefixe",
        ["prefixe", "snapshot_date", "delta_abs", "disparues", "nouvelles"],
        "disparues >= 5",
    )
