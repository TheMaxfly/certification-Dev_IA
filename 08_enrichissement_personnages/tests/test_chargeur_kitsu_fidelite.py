"""Les six contrôles de fidélité du §9 — des ÉGALITÉS, pas des concordances.

Ignorés si `APIMANGA_DSN` n'est pas défini : la suite reste verte hors poste de
développement, mais le contrôle porte sur la vraie base quand elle est là.
"""

import hashlib
import json
import os

import psycopg
import pytest

import chargeur_kitsu as ck
from commun.chargement import PROMOTIONS, promouvoir

DSN = os.environ.get("APIMANGA_DSN")
RAW = ck.RAW_DEFAUT
MANIFESTE = RAW.parent.parent / "manifest.json"

besoin_base = pytest.mark.skipif(not DSN, reason="APIMANGA_DSN non defini")
besoin_raw = pytest.mark.skipif(not RAW.exists(), reason="raw Kitsu absent")


def _ensembles_du_raw() -> tuple[set[str], set[tuple[str, str]]]:
    """Relit le raw en flux — jamais d'un bloc, même en test."""
    identifiants: set[str] = set()
    paires: set[tuple[str, str]] = set()
    with RAW.open(encoding="utf-8") as flux:
        for ligne in flux:
            page = json.loads(ligne)
            oeuvre = str(page["manga_id"])
            for inclus in page.get("included") or []:
                if inclus.get("type") == "characters":
                    identifiants.add(str(inclus["id"]))
            for element in page.get("data") or []:
                relation = (
                    (element.get("relationships") or {}).get("character") or {}
                ).get("data") or {}
                if relation.get("id"):
                    paires.add((oeuvre, str(relation["id"])))
    return identifiants, paires


@pytest.fixture(scope="module")
def connexion():
    with psycopg.connect(DSN) as conn:
        yield conn


@besoin_base
@besoin_raw
def test_1_egalite_des_identifiants_source(connexion) -> None:
    """Différence symétrique vide, DANS LES DEUX SENS."""
    du_raw, _ = _ensembles_du_raw()
    en_base = {
        i
        for (i,) in connexion.execute(
            "SELECT source_id FROM manga.characters WHERE source = 'kitsu'"
        ).fetchall()
    }
    assert du_raw - en_base == set(), "des personnages du raw manquent en base"
    assert en_base - du_raw == set(), "des personnages en base sont absents du raw"


@besoin_base
@besoin_raw
def test_2_egalite_des_paires_oeuvre_personnage(connexion) -> None:
    _, du_raw = _ensembles_du_raw()
    en_base = {
        (o, i)
        for (o, i) in connexion.execute(
            "SELECT w.oeuvre_id, c.source_id FROM manga.character_work w "
            "JOIN manga.characters c USING (character_uid) "
            "WHERE w.source = 'kitsu'"
        ).fetchall()
    }
    assert du_raw - en_base == set(), "des liens du raw manquent en base"
    assert en_base - du_raw == set(), "des liens en base sont absents du raw"


@besoin_raw
def test_3_empreinte_du_raw_inchangee() -> None:
    """Le raw est immuable : le chargeur le lit, ne l'écrit jamais."""
    if not MANIFESTE.exists():
        pytest.skip("manifeste Kitsu absent")
    attendu = next(
        (
            f["sha256"]
            for f in json.loads(MANIFESTE.read_text(encoding="utf-8")).get("files", [])
            if f.get("path", "").endswith("characters.ndjson")
        ),
        None,
    )
    assert attendu, "le manifeste ne porte pas d'empreinte pour characters.ndjson"
    sha = hashlib.sha256()
    with RAW.open("rb") as flux:
        for bloc in iter(lambda: flux.read(1024 * 1024), b""):
            sha.update(bloc)
    assert sha.hexdigest() == attendu


@besoin_base
def test_4_rejeu_idempotent(connexion) -> None:
    """Rejouer la promotion n'insère rien : ni compte ni contenu ne bougent."""
    avant = _instantane(connexion)
    inseres = promouvoir(connexion)
    connexion.rollback()
    assert sum(inseres.values()) == 0, f"le rejeu a inséré : {inseres}"
    assert _instantane(connexion) == avant


def _instantane(connexion) -> dict[str, int]:
    return {
        table: connexion.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table, _ in PROMOTIONS
    }


@besoin_base
@pytest.mark.parametrize(
    "table",
    ["character_forms", "character_descriptions", "character_work"],
)
def test_5_aucun_orphelin(connexion, table: str) -> None:
    (orphelins,) = connexion.execute(
        f"SELECT count(*) FROM manga.{table} t "
        "WHERE NOT EXISTS (SELECT 1 FROM manga.characters c "
        "                  WHERE c.character_uid = t.character_uid)"
    ).fetchone()
    assert orphelins == 0


@besoin_base
@pytest.mark.parametrize(
    "table",
    ["characters", "character_forms", "character_descriptions", "character_work"],
)
def test_6_sentinelle_multisources(connexion, table: str) -> None:
    """Zéro exception : une ligne sans source casserait le schéma en silence."""
    (sans,) = connexion.execute(
        f"SELECT count(*) FROM manga.{table} WHERE source IS NULL OR btrim(source) = ''"
    ).fetchone()
    assert sans == 0, f"{table} porte {sans} ligne(s) sans source"


@besoin_base
def test_aucune_deduplication_entre_sources_n_est_possible(connexion) -> None:
    """La règle d'arrêt la vise nommément : deux sources, deux lignes.

    L'unicité porte sur (source, source_id) et non sur le nom : rien n'empêche
    donc un personnage Wikipédia de coexister avec son homologue Kitsu. C'est
    la cascade, plus tard, qui les rapprochera.
    """
    (contrainte,) = connexion.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'characters_source_id_unique'"
    ).fetchone()
    assert "source" in contrainte and "source_id" in contrainte
    assert "canonical_name" not in contrainte
