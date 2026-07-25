"""GOLD : chaque métrique sur une fixture silver à résultat CONNU.

La fixture fabrique deux snapshots où : le total de séries MONTE (4→5) mais le
préfixe « di » PERD 2 séries (masqué par 0 nouvelle) — le déficit localisé qui
DOIT flasher. `test_completude_disparues_flash` est la vérification-mutation :
retirer le calcul de `disparues` du gold rend `disparues` null et casse le test.
"""

from __future__ import annotations

from pyspark.sql import functions as F

from lakehouse import gold
from lakehouse.config import chemin_table


def _lire(spark, table):
    return spark.read.format("delta").load(str(chemin_table(table)))


def test_volumetrie_delta(spark, silver_fabrique):
    gold.build_volumetrie(spark)
    lignes = {
        (r["grain"], r["snapshot_date"]): r
        for r in _lire(spark, "gold.volumetrie").collect()
    }
    assert lignes[("volumes", "2026-07")]["n"] == 6
    assert lignes[("volumes", "2026-07")]["delta_pct"] == 50.0
    assert lignes[("critiques", "2026-07")]["delta_pct"] == 50.0
    assert lignes[("series", "2026-07")]["n"] == 5
    assert lignes[("series", "2026-07")]["delta_pct"] == 25.0


def test_completude_disparues_flash(spark, silver_fabrique):
    """VÉRIFICATION-MUTATION : le déficit fabriqué « di » DOIT flasher."""
    gold.build_completude_par_prefixe(spark)
    di = (
        _lire(spark, "gold.completude_par_prefixe")
        .where((F.col("prefixe") == "di") & (F.col("snapshot_date") == "2026-07"))
        .first()
    )
    assert di["delta_abs"] == -2  # le net, faible
    assert di["disparues"] == 2  # le vrai détecteur : 2 séries perdues
    assert di["nouvelles"] == 0


def test_remplissage_champs(spark, silver_fabrique):
    gold.build_remplissage_champs(spark)
    lignes = {
        (r["champ"], r["snapshot_date"]): r
        for r in _lire(spark, "gold.remplissage_champs").collect()
    }
    assert lignes[("review_body", "2025-12")]["pct_non_vide"] == 50.0
    assert lignes[("review_body", "2026-07")]["pct_non_vide"] == 100.0
    assert lignes[("review_body", "2026-07")]["delta_pct_points"] == 50.0
    assert lignes[("volume_ean", "2025-12")]["pct_non_vide"] == 0.0
    assert lignes[("volume_ean", "2026-07")]["pct_non_vide"] == 83.33
    assert lignes[("series_genres", "2026-07")]["pct_non_vide"] == 80.0


def test_recouvrement(spark, silver_fabrique):
    gold.build_recouvrement_snapshots(spark)
    lignes = {
        r["cle_type"]: r for r in _lire(spark, "gold.recouvrement_snapshots").collect()
    }
    assert lignes["volume_url"]["disparues"] == 2
    assert lignes["review_url"]["disparues"] == 1
    assert lignes["series_id"]["disparues"] == 2
    assert lignes["volume_url"]["communes"] == 2


def test_qualite_ean(spark, silver_fabrique):
    gold.build_qualite_ean(spark)
    q = {r["snapshot_date"]: r for r in _lire(spark, "gold.qualite_ean").collect()}
    assert q["2026-07"]["total_volumes"] == 6
    assert q["2026-07"]["renseignes"] == 5
    assert q["2026-07"]["format_13"] == 5
    assert q["2026-07"]["cle_valide"] == 4  # v3 (9782355929488) rejeté
    assert q["2026-07"]["uniques_valides"] == 4
    assert q["2025-12"]["cle_valide"] == 0  # volume_ean absent en 2025-12
