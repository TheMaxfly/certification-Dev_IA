"""Mêmes versions que le module 10 pour ce dont le calcul du classement dépend.

Le classement est chargé depuis le module 10 sans l'installer (`api_modele.banc`) :
le résolveur ne garantit donc plus l'alignement des versions. L'égalité avec le banc
(les dix premières séries de chaque question) en dépend : numpy fait le produit
scalaire, pgvector et psycopg lisent les vecteurs, typer est importé par le
rattachement du module 05.
"""

from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

from conftest import RACINE_DEPOT, RACINE_MODULE

PAQUETS_DU_CALCUL = ("numpy", "pgvector", "psycopg", "psycopg-binary", "typer")


def versions(verrou: Path) -> dict[str, str]:
    contenu = tomllib.loads(verrou.read_text(encoding="utf-8"))
    return {p["name"]: p["version"] for p in contenu["package"] if "version" in p}


def test_les_versions_du_calcul_sont_celles_du_verrou_du_10():
    du_11 = versions(RACINE_MODULE / "uv.lock")
    du_10 = versions(RACINE_DEPOT / "10_mesures_recherche" / "uv.lock")

    attendu = {p: du_10.get(p) for p in PAQUETS_DU_CALCUL}
    assert all(attendu.values()), f"absents du verrou du 10 : {attendu}"
    assert {p: du_11.get(p) for p in PAQUETS_DU_CALCUL} == attendu


def test_l_environnement_porte_les_versions_du_verrou():
    du_11 = versions(RACINE_MODULE / "uv.lock")

    installees = {p: importlib.metadata.version(p) for p in PAQUETS_DU_CALCUL}
    assert installees == {p: du_11[p] for p in PAQUETS_DU_CALCUL}
