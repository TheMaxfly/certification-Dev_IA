"""BRONZE : idempotence (replaceWhere) et évolution de schéma (mergeSchema).

Raw fabriqué sous un dépôt tmp (fixture `fake_repo`) — jamais le vrai raw.
"""

from __future__ import annotations

from conftest import ecrire_jsonl

from lakehouse import bronze
from lakehouse.config import chemin_table


def _chemin_volumes(repo, snapshot):
    return (
        repo
        / "04_scraping_manga_sanctuary/data/raw"
        / snapshot
        / "manga_sanctuary_volumes.jsonl"
    )


def test_ingestion_idempotente(spark, lakehouse_tmp, fake_repo):
    ecrire_jsonl(
        _chemin_volumes(fake_repo, "2025-12"),
        [
            {"series_id": "1", "volume_url": "u1"},
            {"series_id": "1", "volume_url": "u2"},
        ],
    )
    bronze.ingest(spark, "ms_volumes", "2025-12")
    bronze.ingest(spark, "ms_volumes", "2025-12")  # rejeu du MÊME snapshot

    df = spark.read.format("delta").load(str(chemin_table("bronze.ms_volumes")))
    # replaceWhere sur la partition : le rejeu remplace, ne duplique pas.
    assert df.count() == 2
    assert df.where("snapshot_date = '2025-12'").count() == 2


def test_source_introuvable_stoppe(spark, lakehouse_tmp, fake_repo):
    import pytest

    with pytest.raises(bronze.SourceIntrouvable):
        bronze.ingest(spark, "ms_volumes", "2026-07")  # rien écrit sous fake_repo


def test_merge_schema_colonne_apparue(spark, lakehouse_tmp, fake_repo):
    # 2025-12 SANS volume_ean.
    ecrire_jsonl(
        _chemin_volumes(fake_repo, "2025-12"),
        [{"series_id": "1", "volume_url": "u1"}],
    )
    # 2026-07 AVEC volume_ean (colonne inédite).
    ecrire_jsonl(
        _chemin_volumes(fake_repo, "2026-07"),
        [{"series_id": "2", "volume_url": "u2", "volume_ean": "9782355929489"}],
    )

    bronze.ingest(spark, "ms_volumes", "2025-12")
    avant = set(
        spark.read.format("delta").load(str(chemin_table("bronze.ms_volumes"))).columns
    )
    assert "volume_ean" not in avant

    bronze.ingest(spark, "ms_volumes", "2026-07")  # mergeSchema
    apres = spark.read.format("delta").load(str(chemin_table("bronze.ms_volumes")))
    assert "volume_ean" in apres.columns
    # La ligne de 2025-12 a volume_ean = null (colonne ajoutée sans casse) ;
    # celle de 2026-07 porte l'EAN.
    assert apres.count() == 2
    ean_2026 = apres.where("snapshot_date = '2026-07'").first()["volume_ean"]
    assert ean_2026 == "9782355929489"
