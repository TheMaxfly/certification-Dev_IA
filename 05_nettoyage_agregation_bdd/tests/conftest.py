"""Harnais partagé : une base PostgreSQL JETABLE, migrée depuis le dépôt.

Garde-fou central, repris du harnais de `database/` : le DSN est fabriqué ici à
partir du conteneur lancé par le harnais ; une DATABASE_URL présente dans
l'environnement est ignorée, et l'absence de Docker provoque un skip explicite.
Les tests ne se rabattent JAMAIS sur une base réelle, et `apimanga` n'est jamais
atteignable par ce harnais. Une seule exception, explicite et hors harnais : le
contrôle permanent des têtes du catalogue (`test_controle_tetes.py`), qui ne
tourne que si `APIMANGA_DSN` est défini, et en lecture seule.

Le conteneur, la base gabarit (migrée une fois par session par le vrai runner,
`database/migrate.py`) et la copie par test viennent du harnais partagé
`database/harnais_tests/` : la base de test est celle du dépôt, pas une
approximation écrite pour les tests.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
MIGRATIONS = RACINE.parents[0] / "database"
sys.path.insert(0, str(RACINE / "src"))


def _charger_harnais():
    """Le harnais partagé des bases jetables (`database/harnais_tests/`), par chemin."""
    spec = importlib.util.spec_from_file_location(
        "harnais_tests", MIGRATIONS / "harnais_tests" / "__init__.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["harnais_tests"] = module
    spec.loader.exec_module(module)
    return module


harnais = _charger_harnais()


@pytest.fixture(scope="session")
def conteneur() -> str:
    """Un PostgreSQL jetable pour toute la session."""
    raison = harnais.raison_de_sauter()
    if raison:
        pytest.skip(raison)
    with harnais.conteneur("referentiels-test") as dsn:
        yield dsn


@pytest.fixture(scope="session")
def gabarit(conteneur) -> str:
    """La base gabarit, migrée une fois par session par le runner du dépôt."""
    return harnais.creer_gabarit(conteneur)


@pytest.fixture
def base(conteneur, gabarit, monkeypatch) -> str:
    """Une base neuve par test, copiée du gabarit migré."""
    with harnais.base_depuis_gabarit(conteneur, gabarit) as dsn:
        monkeypatch.setitem(os.environ, "DATABASE_URL", dsn)
        yield dsn


def lire(dsn: str, sql: str, params=None):
    """Raccourci de lecture pour les assertions."""
    import psycopg

    with psycopg.connect(dsn) as connexion:
        return connexion.execute(sql, params).fetchall()


def ecrire_run_kitsu(dossier, oeuvres, staff=None):
    """Un run Kitsu miniature : `manga.ndjson`, `relations/staff.ndjson`, et le
    `manifest.json` qui déclare leurs sha256 — la forme exacte du run réel.

    oeuvres : (kitsu_id, sous_type, canonique, titres, synopsis, genres, catégories)
    staff   : {kitsu_id: [(nom, rôle Kitsu), …]}
    """
    import hashlib

    (dossier / "relations").mkdir(parents=True, exist_ok=True)
    lignes = []
    for kid, sous_type, canonique, titres, synopsis, genres, categories in oeuvres:
        inclus = [{"type": "genres", "attributes": {"name": g}} for g in genres] + [
            {"type": "categories", "attributes": {"title": c}} for c in categories
        ]
        lignes.append(
            json.dumps(
                {
                    "data": {
                        "id": str(kid),
                        "type": "manga",
                        "attributes": {
                            "subtype": sous_type,
                            "canonicalTitle": canonique,
                            "titles": titres,
                            "synopsis": synopsis,
                            "popularityRank": kid * 10,
                            "ratingRank": kid * 20,
                        },
                    },
                    "included": inclus,
                }
            )
        )
    (dossier / "manga.ndjson").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    staff_lignes = []
    for kid, personnes in (staff or {}).items():
        staff_lignes.append(
            json.dumps(
                {
                    "manga_id": str(kid),
                    "data": [
                        {
                            "type": "mediaStaff",
                            "attributes": {"role": role},
                            "relationships": {
                                "person": {"data": {"type": "people", "id": f"p{i}"}}
                            },
                        }
                        for i, (_, role) in enumerate(personnes)
                    ],
                    "included": [
                        {"type": "people", "id": f"p{i}", "attributes": {"name": nom}}
                        for i, (nom, _) in enumerate(personnes)
                    ],
                }
            )
        )
    (dossier / "relations/staff.ndjson").write_text(
        "".join(ligne + "\n" for ligne in staff_lignes), encoding="utf-8"
    )
    fichiers = [
        {
            "path": nom,
            "sha256": hashlib.sha256((dossier / nom).read_bytes()).hexdigest(),
        }
        for nom in ("manga.ndjson", "relations/staff.ndjson")
    ]
    (dossier / "manifest.json").write_text(
        json.dumps({"files": fichiers}), encoding="utf-8"
    )
    return dossier
