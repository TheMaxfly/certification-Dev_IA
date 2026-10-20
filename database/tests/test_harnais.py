"""Le harnais partagé des bases jetables (`database/harnais_tests/`).

Deux promesses, vérifiées ici :
  - une base copiée du gabarit VAUT une base migrée de zéro : mêmes migrations
    enregistrées, mêmes relations, mêmes droits de niveau base (les `GRANT CONNECT`
    de 012 et 022, que `CREATE DATABASE … TEMPLATE` ne recopie pas), mêmes données
    de référence ;
  - l'empreinte de l'image PostgreSQL n'est écrite qu'une fois : aucun conftest ne
    la redéfinit, et la seule autre copie (le Compose du 02, qui ne peut pas importer
    de Python) est la même.
"""

from __future__ import annotations

import re

import psycopg
from conftest import RACINE, harnais

DEPOT = RACINE.parent
CONFTESTS = [
    DEPOT / "database" / "tests" / "conftest.py",
    DEPOT / "05_nettoyage_agregation_bdd" / "tests" / "conftest.py",
    DEPOT / "08_enrichissement_personnages" / "tests" / "conftest.py",
    DEPOT / "09_service_embedding" / "tests" / "conftest.py",
    DEPOT / "10_mesures_recherche" / "tests" / "conftest.py",
]


def portrait(dsn: str) -> dict:
    """Ce qu'une base migrée doit porter, lu dans son catalogue."""
    with psycopg.connect(dsn) as cx:
        return {
            "migrations": cx.execute(
                "SELECT version, checksum FROM public.schema_migrations ORDER BY 1"
            ).fetchall(),
            "relations": cx.execute(
                "SELECT n.nspname, c.relname, c.relkind::text FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname IN ('manga', 'bench', 'staging', 'public')"
                " ORDER BY 1, 2"
            ).fetchall(),
            "droits_de_la_base": sorted(
                cx.execute(
                    "SELECT coalesce(r.rolname, 'PUBLIC'), a.privilege_type"
                    " FROM pg_database d CROSS JOIN LATERAL aclexplode(d.datacl) a"
                    " LEFT JOIN pg_roles r ON r.oid = a.grantee"
                    " WHERE d.datname = current_database()"
                ).fetchall()
            ),
            "strategies": cx.execute(
                "SELECT name FROM bench.chunking_strategies ORDER BY 1"
            ).fetchall(),
            "extensions": cx.execute(
                "SELECT extname, extversion FROM pg_extension ORDER BY 1"
            ).fetchall(),
        }


def test_la_copie_du_gabarit_vaut_une_base_migree(conteneur_postgres, gabarit):
    with harnais.base_depuis_gabarit(conteneur_postgres, gabarit) as copie:
        attendu_copie = portrait(copie)
    with harnais.base_vide(conteneur_postgres) as neuve:
        harnais.migrer(neuve)
        attendu_neuve = portrait(neuve)

    assert attendu_copie["migrations"], "aucune migration : le test ne prouverait rien"
    assert ("manga_ro", "CONNECT") in attendu_copie["droits_de_la_base"]
    assert ("verif_ro", "CONNECT") in attendu_copie["droits_de_la_base"]
    assert attendu_copie == attendu_neuve


def test_migrer_jusqu_a_une_cible(conteneur_postgres):
    with harnais.base_vide(conteneur_postgres) as dsn:
        harnais.migrer(dsn, cible="012")
        with psycopg.connect(dsn) as cx:
            (derniere,) = cx.execute(
                "SELECT max(version) FROM public.schema_migrations"
            ).fetchone()
    assert derniere == "012"


def test_l_image_n_est_ecrite_qu_une_fois():
    image = harnais.IMAGE_POSTGRES
    assert "@sha256:" in image
    empreinte = image.split("@sha256:")[1]
    for conftest in CONFTESTS:
        contenu = conftest.read_text(encoding="utf-8")
        assert "IMAGE_POSTGRES =" not in contenu, f"{conftest} redéfinit l'image"
        assert empreinte not in contenu, f"{conftest} recopie l'empreinte de l'image"
    compose = (DEPOT / "02_api_manga" / "compose.integration.yml").read_text(
        encoding="utf-8"
    )
    images = re.findall(r"image:\s*(pgvector/\S+)", compose)
    assert images == [image], f"Compose du 02 : {images}, harnais : {image}"
