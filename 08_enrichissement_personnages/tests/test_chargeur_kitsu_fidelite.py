"""Les contrôles de fidélité du §9 — des ÉGALITÉS, pas des concordances.

Deux lieux :
  - sur le poste, contre le raw réel et la base réelle (`APIMANGA_DSN`) : l'égalité
    des identifiants et des paires, et l'empreinte du raw. Ils sautent ailleurs, et
    tournent dans l'étape « état d'apimanga » de `verify-poste` ;
  - partout, sur une base jetable chargée d'un raw factice : le rejeu idempotent.

L'absence d'orphelin et la présence de la source sur chaque ligne ne sont plus
comptées ici : les clés étrangères, `NOT NULL` et les `CHECK` de 015 les imposent.
L'unicité par (source, source_id), jamais par le nom, est éprouvée dans `database`.
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
def connexion_reelle():
    with psycopg.connect(DSN) as conn:
        yield conn


@besoin_base
@besoin_raw
def test_1_egalite_des_identifiants_source(connexion_reelle) -> None:
    """Différence symétrique vide, DANS LES DEUX SENS."""
    du_raw, _ = _ensembles_du_raw()
    en_base = {
        i
        for (i,) in connexion_reelle.execute(
            "SELECT source_id FROM manga.characters WHERE source = 'kitsu'"
        ).fetchall()
    }
    assert du_raw - en_base == set(), "des personnages du raw manquent en base"
    assert en_base - du_raw == set(), "des personnages en base sont absents du raw"


@besoin_base
@besoin_raw
def test_2_egalite_des_paires_oeuvre_personnage(connexion_reelle) -> None:
    _, du_raw = _ensembles_du_raw()
    en_base = {
        (o, i)
        for (o, i) in connexion_reelle.execute(
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


def test_4_rejeu_idempotent(base_chargee, raws) -> None:
    """Recharger le même raw et rejouer la promotion n'insère rien : ni compte ni
    contenu ne bougent (base jetable, raw factice)."""
    with psycopg.connect(base_chargee) as connexion:
        avant = _instantane(connexion)
        assert all(avant.values()), f"une table vide ne prouverait rien : {avant}"
        ck.remplir_staging(connexion, raws["kitsu"])
        inseres = promouvoir(connexion)
        connexion.rollback()
        assert sum(inseres.values()) == 0, f"le rejeu a inséré : {inseres}"
        assert _instantane(connexion) == avant


def test_le_chargement_suit_les_regles_sur_un_raw_factice(base_chargee) -> None:
    """Le raw factice traverse les trois étages : rôles traduits (et l'inconnu
    gardé tel quel, sans traduction), formes versées une fois par personnage,
    licence selon la provenance citée, dates de la source typées."""
    with psycopg.connect(base_chargee) as connexion:
        liens = connexion.execute(
            "SELECT c.source_id, w.oeuvre_id, w.role_source, w.role_normalise"
            " FROM manga.character_work w JOIN manga.characters c USING"
            " (character_uid) WHERE w.source = 'kitsu' ORDER BY 1, 2"
        ).fetchall()
        formes = connexion.execute(
            "SELECT count(*) FROM manga.character_forms f JOIN manga.characters c"
            " USING (character_uid) WHERE c.source = 'kitsu' AND c.source_id = '1'"
        ).fetchone()
        licences = dict(
            connexion.execute(
                "SELECT c.source_id, d.licence FROM manga.character_descriptions d"
                " JOIN manga.characters c USING (character_uid)"
                " WHERE d.source = 'kitsu'"
            ).fetchall()
        )
        (cree,) = connexion.execute(
            "SELECT source_created_at IS NOT NULL FROM manga.characters"
            " WHERE source = 'kitsu' AND source_id = '1'"
        ).fetchone()
    assert liens == [
        ("1", "38", "main", "principal"),
        ("1", "41", "cameo", "apparition"),
        ("2", "38", "supporting", "secondaire"),
        ("3", "38", "figurant", None),
    ]
    # canonique + deux name_lang + un alias : les vides sont écartés, et la
    # seconde page qui le cite ne les reverse pas.
    assert formes == (4,)
    assert licences == {"1": "CC BY-SA", "2": "à instruire"}
    assert cree


def _instantane(connexion) -> dict[str, int]:
    return {
        table: connexion.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table, _ in PROMOTIONS
    }
