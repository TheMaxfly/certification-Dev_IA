"""BRONZE — ingestion générique paramétrée par source (pas quatre jobs copiés).

Lit le raw daté (lecture seule), écrit une table Delta partitionnée par
`snapshot_date`, avec colonnes techniques (`source_file`, `ingested_at`,
`snapshot_date`). Typage minimal : le silver typera. Deux invariants :

  - IDEMPOTENCE : réingérer un snapshot = `replaceWhere` sur sa partition,
    jamais de doublon (le DataFrame ne contient que la partition visée) ;
  - SCHEMA EVOLUTION : `mergeSchema=true` — quand `volume_ean` apparaît dans
    le snapshot 2026-07 alors qu'il était absent en 2025-12, la table grandit
    d'une colonne sans casse (cas réel documenté).

Le raw n'est JAMAIS écrit. Une source absente STOPpe (fait nouveau, pas trou
à combler en silence).
"""

from __future__ import annotations

import json
import re

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from .config import COMPTES_ATTENDUS, SOURCES, Source, chemin_table


class SourceIntrouvable(FileNotFoundError):
    """Le raw attendu n'existe pas : on s'arrête plutôt que d'inventer."""


# Delta refuse ` ,;{}()\n\t=` dans les noms de colonnes ; on ajoute `.` et `:`
# qui gênent les références Spark. Les accents, eux, sont autorisés : on les
# garde (colonnes MI « Éditeur VF »). No-op sur les schémas déjà snake_case.
_CAR_INTERDITS = re.compile(r"[ ,;{}()\n\t=.:]+")


def _assainir_noms(df: DataFrame) -> DataFrame:
    """Renomme déterministiquement les colonnes en noms acceptés par Delta."""
    vus: dict[str, int] = {}
    renoms: list[tuple[str, str]] = []
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


def _colonnes_techniques(df: DataFrame, source_path: str, snapshot: str) -> DataFrame:
    return (
        df.withColumn("source_file", F.lit(source_path))
        .withColumn("ingested_at", F.current_timestamp())
        .withColumn("snapshot_date", F.lit(snapshot))
    )


def _lire_source(
    spark: SparkSession, source: Source, snapshot: str
) -> tuple[DataFrame, int]:
    """(DataFrame déballé, nombre d'enregistrements SOURCE lus).

    Le second terme est ce qu'on confronte aux comptes attendus : lignes pour
    les formats texte (une ligne = un enregistrement), pages pour les mappings
    Kitsu (une ligne = une page d'API), lignes pour le parquet, entités pour
    les lots Wikidata.
    """
    chemin = source.chemin(snapshot)
    if source.format == "json_lots":
        existe = chemin.is_dir()
    else:
        existe = chemin.is_file()
    if not existe:
        raise SourceIntrouvable(
            f"{source.nom}/{snapshot} : raw introuvable ({chemin}). "
            "Ingestion interrompue — aucune donnée inventée."
        )
    ruta = str(chemin)

    if source.nom in ("ms_volumes", "ms_reviews"):
        # JSONL délimité par ligne : le lecteur json de Spark le gère nativement.
        df = spark.read.json(ruta)
        return df, df.count()

    if source.nom == "kitsu_manga":
        # Enveloppe JSON:API déballée (comme B3) sans inférer l'énorme schéma
        # `attributes` : on lit brut et on extrait les champs clés par chemin.
        brut = spark.read.text(ruta)
        df = brut.select(
            F.get_json_object("value", "$.data.id").alias("kitsu_id"),
            F.get_json_object("value", "$.data.attributes.subtype").alias("subtype"),
            F.get_json_object("value", "$.data.attributes.canonicalTitle").alias(
                "canonical_title"
            ),
            F.col("value").alias("raw_json"),
        )
        return df, brut.count()

    if source.nom == "kitsu_mappings":
        # Une ligne = une PAGE ; les mappings sont dans data[]. Déballage en
        # une ligne par mapping (manga_id, site externe, id externe).
        pages = spark.read.json(ruta)
        pages_lues = pages.count()
        # `explode` (interne) : une ligne par mapping réel. Les pages sans
        # mapping ne produisent rien (bronze = les mappings, pas les pages) ;
        # le nombre de pages est déjà capté séparément via `pages_lues`.
        df = pages.select(
            F.col("manga_id").cast("string").alias("manga_id"),
            F.explode("data").alias("m"),
        ).select(
            "manga_id",
            F.col("m.id").alias("mapping_id"),
            F.col("m.attributes.externalSite").alias("external_site"),
            F.col("m.attributes.externalId").alias("external_id"),
        )
        return df, pages_lues

    if source.nom == "mi_sorties":
        df = spark.read.parquet(ruta)
        return df, df.count()

    if source.nom == "wd_entities":
        return _lire_wikidata(spark, ruta)

    raise ValueError(f"format non géré pour {source.nom}")


def _lire_wikidata(spark: SparkSession, dossier: str) -> tuple[DataFrame, int]:
    """Lots `{"entities": {QID: {...}}}` : QID variables d'un fichier à
    l'autre, donc parsés par fichier (petits, 165 lots) plutôt que par
    inférence de schéma. On résume : id, labels (json), n sitelinks."""

    def parser(paire: tuple[str, str]) -> list[tuple]:
        _nom, contenu = paire
        objet = json.loads(contenu)
        lignes: list[tuple] = []
        for qid, entite in (objet.get("entities") or {}).items():
            labels = entite.get("labels") or {}
            sitelinks = entite.get("sitelinks") or {}
            lignes.append(
                (
                    entite.get("id") or qid,
                    json.dumps(labels, ensure_ascii=False),
                    len(sitelinks),
                    json.dumps(sitelinks, ensure_ascii=False),
                )
            )
        return lignes

    rdd = spark.sparkContext.wholeTextFiles(f"{dossier}/*.json").flatMap(parser)
    df = spark.createDataFrame(
        rdd, schema=["wd_id", "labels_json", "sitelinks_count", "sitelinks_json"]
    )
    n = df.count()
    return df, n


def ingest(
    spark: SparkSession,
    source_name: str,
    snapshot: str,
) -> dict:
    """Ingère (source, snapshot) en bronze, idempotent. Retourne les mesures."""
    if source_name not in SOURCES:
        raise ValueError(f"source inconnue : {source_name}")
    source = SOURCES[source_name]
    if snapshot not in source.snapshots:
        raise ValueError(
            f"{source_name} : snapshot '{snapshot}' hors {source.snapshots}"
        )

    df, source_records = _lire_source(spark, source, snapshot)
    df = _assainir_noms(df)
    df = _colonnes_techniques(df, str(source.chemin(snapshot)), snapshot)
    rows_bronze = df.count()

    ruta = str(chemin_table(source.table_bronze))
    writer = (
        df.write.format("delta")
        .partitionBy("snapshot_date")
        .option("mergeSchema", "true")
    )
    if DeltaTable.isDeltaTable(spark, ruta):
        # Rejeu : on ne remplace QUE la partition du snapshot -> pas de doublon,
        # les autres snapshots restent intacts.
        writer.mode("overwrite").option(
            "replaceWhere", f"snapshot_date = '{snapshot}'"
        ).save(ruta)
    else:
        writer.mode("overwrite").save(ruta)

    attendu = COMPTES_ATTENDUS.get((source_name, snapshot))
    ecart = None if attendu is None else source_records - attendu
    return {
        "source": source_name,
        "snapshot": snapshot,
        "table": source.table_bronze,
        "source_records": source_records,
        "rows_bronze": rows_bronze,
        "attendu": attendu,
        "ecart": ecart,
        "chemin": ruta,
    }
