"""Registre des sources raw, chemins du lakehouse, seuils d'alerte.

Un seul endroit décrit OÙ lire le raw (lecture seule), OÙ écrire les tables
Delta (reconstructibles) et OÙ déposer les rapports (versionnés). Tout est
surchargeable par variable d'environnement pour que le MÊME code tourne à
l'identique en local mode (chemins du dépôt) et dans le conteneur (montages
`/data/...`) — c'est le « double chemin d'exécution » du livrable 1.

Aucune écriture n'est jamais faite hors de LAKEHOUSE_ROOT et RAPPORTS_DIR :
le raw est l'archive, le lakehouse en est une projection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve()
# .../07_databricks_manga_export/lakehouse/src/lakehouse/config.py
#     parents[0]=lakehouse  [1]=src  [2]=lakehouse(projet)  [3]=07_...  [4]=dépôt
MODULE07_ROOT = _HERE.parents[3]


def _env_path(nom: str, defaut: Path) -> Path:
    valeur = os.environ.get(nom)
    return Path(valeur) if valeur else defaut


# Chemins résolus DYNAMIQUEMENT (lecture de l'env à chaque appel) : le même
# code s'adapte au conteneur (montages /repo, /lakehouse, /rapports) et laisse
# les tests pointer chaque cas sur un tmp isolé (monkeypatch d'env).
def repo_root() -> Path:
    """Base des chemins raw (répartis dans plusieurs modules)."""
    return _env_path("LAKEHOUSE_REPO_ROOT", _HERE.parents[4])


def lakehouse_root() -> Path:
    """Racine des tables Delta (rw, reconstructibles)."""
    return _env_path("LAKEHOUSE_ROOT", MODULE07_ROOT / "data" / "lakehouse")


def rapports_dir() -> Path:
    """Dossier des rapports générés (versionnés)."""
    return _env_path("LAKEHOUSE_RAPPORTS", MODULE07_ROOT / "rapports")


# Répertoires datés figés (immuables) des sources non-MS.
KITSU_RUN = "20260714T152202Z"
MI_RUN = "2026-07"
WIKIDATA_RUN = "2026-07-14"

# Snapshots MS disponibles (les deux mois : la matière des comparaisons).
SNAPSHOTS_MS = ("2025-12", "2026-07")


@dataclass(frozen=True)
class Source:
    """Une source ingérable en bronze.

    `formats` :
      - "jsonl"   : un objet JSON par ligne (MS) ;
      - "ndjson"  : idem, enveloppe JSON:API à déballer au bronze (Kitsu) ;
      - "parquet" : fichier colonne (MI) ;
      - "json_lots" : dossier de fichiers JSON, chacun une liste (Wikidata).
    """

    nom: str
    table_bronze: str
    format: str
    snapshots: tuple[str, ...]

    def chemin(self, snapshot: str) -> Path:
        """Chemin du raw pour un snapshot donné. Ne vérifie pas l'existence
        (le job d'ingestion le fait et STOPpe si absent)."""
        return _RESOLVEURS[self.nom](snapshot)


def _ms_volumes(snapshot: str) -> Path:
    return (
        repo_root()
        / "04_scraping_manga_sanctuary/data/raw"
        / snapshot
        / "manga_sanctuary_volumes.jsonl"
    )


def _ms_reviews(snapshot: str) -> Path:
    return (
        repo_root()
        / "04_scraping_manga_sanctuary/data/raw"
        / snapshot
        / "manga_sanctuary_reviews.jsonl"
    )


def _kitsu_manga(_snapshot: str) -> Path:
    return (
        repo_root()
        / "03_kitsu_api_exports/exports/full_catalog"
        / KITSU_RUN
        / "manga.ndjson"
    )


def _kitsu_mappings(_snapshot: str) -> Path:
    return (
        repo_root()
        / "03_kitsu_api_exports/exports/full_catalog"
        / KITSU_RUN
        / "relations/mappings.ndjson"
    )


def _mi_sorties(_snapshot: str) -> Path:
    return (
        repo_root()
        / "05_nettoyage_agregation_bdd/data/raw/mi"
        / MI_RUN
        / "data.parquet"
    )


def _wd_entities(_snapshot: str) -> Path:
    return (
        repo_root()
        / "05_nettoyage_agregation_bdd/data/raw/wikidata"
        / WIKIDATA_RUN
        / "entities"
    )


_RESOLVEURS = {
    "ms_volumes": _ms_volumes,
    "ms_reviews": _ms_reviews,
    "kitsu_manga": _kitsu_manga,
    "kitsu_mappings": _kitsu_mappings,
    "mi_sorties": _mi_sorties,
    "wd_entities": _wd_entities,
}


SOURCES: dict[str, Source] = {
    "ms_volumes": Source("ms_volumes", "bronze.ms_volumes", "jsonl", SNAPSHOTS_MS),
    "ms_reviews": Source("ms_reviews", "bronze.ms_reviews", "jsonl", SNAPSHOTS_MS),
    "kitsu_manga": Source("kitsu_manga", "bronze.kitsu_manga", "ndjson", ("2026-07",)),
    "kitsu_mappings": Source(
        "kitsu_mappings", "bronze.kitsu_mappings", "ndjson", ("2026-07",)
    ),
    "mi_sorties": Source("mi_sorties", "bronze.mi_sorties", "parquet", ("2026-07",)),
    "wd_entities": Source(
        "wd_entities", "bronze.wd_entities", "json_lots", ("2026-07",)
    ),
}


# Chiffres de contrôle attendus par (source, snapshot), issus du profilage
# (Étape 0). Un écart bronze↔attendu part en section ⚠️ du rapport, JAMAIS
# d'auto-correction : le raw fait foi, l'écart est un fait à investiguer.
COMPTES_ATTENDUS: dict[tuple[str, str], int] = {
    ("ms_volumes", "2025-12"): 89_188,
    ("ms_volumes", "2026-07"): 103_811,
    ("ms_reviews", "2025-12"): 6_749,
    ("ms_reviews", "2026-07"): 11_052,
    ("kitsu_manga", "2026-07"): 62_768,
    ("kitsu_mappings", "2026-07"): 62_768,  # pages ; items attendus 104 726
    ("mi_sorties", "2026-07"): 59_062,
}


@dataclass(frozen=True)
class SeuilsAlerte:
    """Paramètres d'alerte du rapport — imprimés, valeurs par défaut
    raisonnables, aucune magie. Surchargeables en CLI."""

    delta_pct_anormal: float = 20.0  # |Δ%| volumétrie au-delà → ⚠️
    prefixe_deficit_min: int = 5  # une baisse ≥ N sur un préfixe → ⚠️


SEUILS = SeuilsAlerte()


def chemin_table(nom_qualifie: str) -> Path:
    """`bronze.ms_volumes` → LAKEHOUSE_ROOT/bronze/ms_volumes (chemin Delta)."""
    couche, _, table = nom_qualifie.partition(".")
    return lakehouse_root() / couche / table


# Version des jobs, estampillée dans chaque table gold (computed_at + version).
JOB_VERSION = "0.2.0"
