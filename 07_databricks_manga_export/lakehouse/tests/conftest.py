"""Fixtures de test — Spark local mode (Java 8 de l'hôte), lakehouse isolé.

Les chemins du lakehouse sont résolus dynamiquement depuis l'env : chaque test
pointe LAKEHOUSE_ROOT sur un tmp jetable. Aucun accès au raw réel ni à
PostgreSQL — tout est fabriqué.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lakehouse.spark import build_spark


@pytest.fixture(scope="session")
def spark():
    os.environ.setdefault("SPARK_LOG_LEVEL", "ERROR")
    if not os.environ.get("JAVA_HOME"):
        java8 = "/usr/lib/jvm/java-8-openjdk-amd64"
        if os.path.isdir(java8):
            os.environ["JAVA_HOME"] = java8
    session = build_spark("tests", shuffle_partitions=2)
    yield session
    session.stop()


@pytest.fixture
def lakehouse_tmp(tmp_path, monkeypatch) -> Path:
    """LAKEHOUSE_ROOT isolé pour un test (tables Delta jetables)."""
    root = tmp_path / "lakehouse"
    monkeypatch.setenv("LAKEHOUSE_ROOT", str(root))
    monkeypatch.setenv("LAKEHOUSE_RAPPORTS", str(tmp_path / "rapports"))
    return root


@pytest.fixture
def fake_repo(tmp_path, monkeypatch) -> Path:
    """Racine de dépôt fabriquée : LAKEHOUSE_REPO_ROOT pointe ici, on y écrit
    des fichiers raw MS minuscules pour tester bronze sans le vrai raw."""
    repo = tmp_path / "repo"
    monkeypatch.setenv("LAKEHOUSE_REPO_ROOT", str(repo))
    return repo


def ecrire_jsonl(chemin: Path, enregistrements: list[dict]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", encoding="utf-8") as fh:
        for rec in enregistrements:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def ecrire_silver(spark, table: str, lignes: list[tuple], schema: str) -> None:
    """Écrit une table silver Delta fabriquée à l'emplacement attendu."""
    from lakehouse.config import chemin_table

    df = spark.createDataFrame(lignes, schema=schema)
    (
        df.write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .partitionBy("snapshot_date")
        .save(str(chemin_table(table)))
    )


_SCHEMA_VOL = (
    "series_id int, volume_url string, volume_ean string, "
    "volume_ean_valide boolean, snapshot_date string"
)
_SCHEMA_REV = (
    "series_id int, review_url string, review_body string, snapshot_date string"
)
_SCHEMA_SER = (
    "series_id int, series_title string, series_genres array<string>, "
    "series_tags array<string>, series_other_titles array<string>, snapshot_date string"
)


@pytest.fixture
def silver_fabrique(spark, lakehouse_tmp):
    """Deux snapshots à RÉSULTAT CONNU : le total de séries monte (4→5) mais le
    préfixe « di » perd 2 séries (0 nouvelle) — le déficit localisé qui doit
    flasher. Partagée par les tests gold ET le test du lecteur externe."""
    volumes = [
        (1, "v1", None, False, "2025-12"),
        (1, "v2", None, False, "2025-12"),
        (2, "v3", None, False, "2025-12"),
        (3, "v4", None, False, "2025-12"),
        (1, "v1", "9782355929489", True, "2026-07"),
        (2, "v3", "9782355929488", False, "2026-07"),
        (4, "v5", "9791041113002", True, "2026-07"),
        (5, "v6", None, False, "2026-07"),
        (6, "v7", "9782811678845", True, "2026-07"),
        (7, "v8", "0000000000000", True, "2026-07"),
    ]
    reviews = [
        (1, "r1", "texte", "2025-12"),
        (2, "r2", None, "2025-12"),
        (1, "r1", "texte", "2026-07"),
        (3, "r3", "a", "2026-07"),
        (4, "r4", "b", "2026-07"),
    ]
    series = [
        (1, "Dino Crisis", ["a"], [], ["x"], "2025-12"),
        (2, "Digimon", [], [], [], "2025-12"),
        (3, "Dinosaur King", ["a"], ["t"], [], "2025-12"),
        (4, "Alpha", [], [], [], "2025-12"),
        (1, "Dino Crisis", ["a"], ["t"], ["x"], "2026-07"),
        (4, "Alpha", ["b"], [], [], "2026-07"),
        (5, "Beta", ["b"], ["t"], ["y"], "2026-07"),
        (6, "Gamma", [], [], [], "2026-07"),
        (7, "Delta", ["c"], ["t"], ["z"], "2026-07"),
    ]
    ecrire_silver(spark, "silver.ms_volumes", volumes, _SCHEMA_VOL)
    ecrire_silver(spark, "silver.ms_reviews", reviews, _SCHEMA_REV)
    ecrire_silver(spark, "silver.ms_series", series, _SCHEMA_SER)
