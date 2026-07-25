"""Lecteur externe (DuckDB, hors Spark) : il lit EXACTEMENT ce que gold a écrit.

C'est la preuve de la boucle C1 côté consommateur — le cycle mensuel lit le
lakehouse sans démarrer la JVM.
"""

from __future__ import annotations

from lakehouse import gold, reader


def test_lecteur_lit_ce_que_gold_ecrit(spark, silver_fabrique):
    gold.build_all(spark)

    s = reader.synthese()  # ouvre sa propre connexion DuckDB

    recouvrement = {r[0]: r for r in s["recouvrement"]}
    # (cle_type, n_precedent, n_courant, disparues)
    assert recouvrement["volume_url"][3] == 2
    assert recouvrement["review_url"][3] == 1

    ean = {r[0]: r for r in s["qualite_ean"]}
    # (snapshot_date, total_volumes, renseignes, cle_valide)
    assert ean["2026-07"][1] == 6
    assert ean["2026-07"][3] == 4

    volumetrie = {(r["grain"], r["snapshot_date"]): r for r in s["volumetrie"]}
    assert volumetrie[("volumes", "2026-07")]["n"] == 6
