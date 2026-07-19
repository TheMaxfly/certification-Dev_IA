"""Chargeur du staff et de la méta Kitsu, de bout en bout, sur base JETABLE.

Réutilise le harnais partagé (conteneur + base migrée 000->009). `apimanga`
n'est jamais atteignable.

Les fixtures sont minuscules mais portent les cas qui décident : un nom qui ne
vit que dans `included`, une personne citée sans être incluse, un light novel,
une startDate absente, une autre hors plage, et une personne créditée deux fois
sur la même œuvre.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import lire

from identity import charger_kitsu_staff


def enveloppe_manga(kitsu_id: str, subtype: str, start_date) -> dict:
    return {
        "data": {
            "id": kitsu_id,
            "type": "manga",
            "attributes": {"subtype": subtype, "startDate": start_date},
        }
    }


def enveloppe_staff(manga_id: str, credits: list[tuple], personnes: dict) -> dict:
    """credits = [(staff_id, role, person_id)] ; personnes = {id: nom}.

    `personnes` ne contient QUE ce qui est inclus dans l'enveloppe : c'est le
    levier du test « personne citée mais non incluse ».
    """
    return {
        "manga_id": manga_id,
        "relationship": "staff",
        "data": [
            {
                "id": staff_id,
                "type": "mediaStaff",
                "attributes": {"role": role},
                "relationships": {
                    "person": {"data": {"type": "people", "id": person_id}}
                },
            }
            for staff_id, role, person_id in credits
        ],
        "included": [
            {"id": pid, "type": "people", "attributes": {"name": nom}}
            for pid, nom in personnes.items()
        ],
    }


def ecrire_ndjson(chemin: Path, objets: list[dict]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        "\n".join(json.dumps(o, ensure_ascii=False) for o in objets) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def run_kitsu(tmp_path, monkeypatch) -> Path:
    """Un run Kitsu miniature, structuré comme le vrai."""
    catalogue = tmp_path / "full_catalog"
    dossier = catalogue / "20990101T000000Z"
    ecrire_ndjson(
        dossier / "manga.ndjson",
        [
            enveloppe_manga("1", "manga", "2005-01-01"),
            enveloppe_manga("2", "manhwa", "1998-06-30"),
            # Hors cible : ne doit apparaître ni en meta ni en staff.
            enveloppe_manga("3", "novel", "2010-01-01"),
            # Année absente, puis année aberrante : les deux -> NULL.
            enveloppe_manga("4", "manga", None),
            enveloppe_manga("5", "manga", "0001-01-01"),
        ],
    )
    ecrire_ndjson(
        dossier / "relations/staff.ndjson",
        [
            enveloppe_staff(
                "1",
                [("s1", "Story", "p1"), ("s2", "Art", "p2")],
                {"p1": "Akira Toriyama", "p2": "Shouko Fukaki"},
            ),
            # Même personne, deux rôles sur la même œuvre : deux lignes légitimes.
            enveloppe_staff(
                "2",
                [("s3", "Story", "p1"), ("s4", "Art", "p1")],
                {"p1": "Akira Toriyama"},
            ),
            # Le light novel a du staff : le filtre subtype doit l'écarter.
            enveloppe_staff("3", [("s5", "Story", "p3")], {"p3": "Personne Novel"}),
            # Personne CITÉE mais absente de `included` : nom non résolu.
            enveloppe_staff("4", [("s6", "Story", "p404")], {}),
        ],
    )
    monkeypatch.setattr(charger_kitsu_staff, "CATALOGUE", catalogue)
    return dossier


@pytest.fixture
def charge(base, run_kitsu):
    """Exécute le chargement une fois, rend le DSN."""
    import psycopg

    with psycopg.connect(base) as connexion:
        with connexion.cursor() as curseur:
            charger_kitsu_staff.verifier_prerequis(curseur)
        meta = charger_kitsu_staff.charger_meta(connexion, run_kitsu)
        staff = charger_kitsu_staff.charger_staff(connexion, run_kitsu)
        connexion.commit()
    return base, meta, staff


# --------------------------------------------------------------------------- #
#  L'extraction d'année
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        ("2005-01-01", 2005),
        ("1998-06-30", 1998),
        (None, None),
        ("", None),
        ("0001-01-01", None),  # hors plage : jamais une année inventée
        ("pas-une-date", None),
        ("3000-01-01", None),
    ],
)
def test_annee_de_extrait_ou_renonce(valeur, attendu):
    assert charger_kitsu_staff.annee_de(valeur) == attendu


# --------------------------------------------------------------------------- #
#  kitsu_meta
# --------------------------------------------------------------------------- #


def test_meta_ne_charge_que_la_cible(charge):
    base, _, _ = charge
    lignes = lire(base, "SELECT kitsu_id, subtype FROM manga.kitsu_meta ORDER BY 1")
    assert [k for k, _ in lignes] == [1, 2, 4, 5], "le novel doit être écarté"


def test_meta_laisse_l_annee_nulle_plutot_que_fausse(charge):
    base, _, _ = charge
    lignes = dict(lire(base, "SELECT kitsu_id, annee FROM manga.kitsu_meta"))
    assert lignes[1] == 2005
    assert lignes[2] == 1998
    assert lignes[4] is None, "startDate absente"
    assert lignes[5] is None, "startDate hors plage"


def test_meta_est_idempotente(charge, run_kitsu):
    """MUTATION : sans l'upsert, un rechargement violerait la PK."""
    import psycopg

    base, _, _ = charge
    avant = lire(base, "SELECT count(*) FROM manga.kitsu_meta")[0][0]
    with psycopg.connect(base) as connexion:
        charger_kitsu_staff.charger_meta(connexion, run_kitsu)
        connexion.commit()
    assert lire(base, "SELECT count(*) FROM manga.kitsu_meta")[0][0] == avant


# --------------------------------------------------------------------------- #
#  kitsu_staff — la jointure interne au fichier
# --------------------------------------------------------------------------- #


def test_le_nom_est_resolu_depuis_included(charge):
    """Le cœur du chargeur : `data[]` ne porte que le rôle et un pointeur — le
    nom vit dans `included[]`. Sans cette jointure, la table serait vide."""
    base, _, _ = charge
    noms = lire(
        base,
        "SELECT personne, personne_norm FROM manga.kitsu_staff "
        "WHERE kitsu_id = 1 ORDER BY personne",
    )
    assert noms == [
        ("Akira Toriyama", "akira toriyama"),
        ("Shouko Fukaki", "shouko fukaki"),
    ]


def test_une_personne_citee_mais_non_incluse_n_invente_pas_de_nom(charge):
    """L'œuvre 4 cite p404 sans l'inclure : aucune ligne ne doit sortir."""
    base, _, staff = charge
    assert lire(base, "SELECT count(*) FROM manga.kitsu_staff WHERE kitsu_id = 4") == [
        (0,)
    ]
    assert staff["sans_nom"] == 1, "le crédit non résolu doit être COMPTÉ, pas ignoré"


def test_le_filtre_subtype_ecarte_le_staff_des_novels(charge):
    """MUTATION : sans la jointure à kitsu_meta, les auteurs de light novels
    entreraient dans les confirmateurs de la cascade."""
    base, _, staff = charge
    assert lire(
        base,
        "SELECT count(*) FROM manga.kitsu_staff WHERE personne_norm LIKE '%novel%'",
    ) == [(0,)]
    assert staff["exclus_subtype"] == 1


def test_une_personne_peut_tenir_deux_roles(charge):
    """Le rôle fait partie de la clé : Story ET Art sur la même œuvre."""
    base, _, _ = charge
    roles = lire(
        base,
        "SELECT role FROM manga.kitsu_staff WHERE kitsu_id = 2 ORDER BY role",
    )
    assert roles == [("Art",), ("Story",)]


def test_le_staging_garde_tout_ce_que_la_promotion_ecarte(charge):
    """Le staging est fidèle à la source : il porte AUSSI le novel et le crédit
    non résolu, sinon la mesure du filtre serait perdue."""
    base, _, _ = charge
    assert lire(base, "SELECT count(*) FROM staging.kitsu_staff") == [(6,)]
    assert lire(base, "SELECT count(*) FROM manga.kitsu_staff") == [(4,)]


def test_staff_est_idempotent(charge, run_kitsu):
    """MUTATION : sans ON CONFLICT DO NOTHING, un rechargement doublerait la
    table — 53 183 crédits par cycle."""
    import psycopg

    base, _, _ = charge
    avant = lire(base, "SELECT count(*) FROM manga.kitsu_staff")[0][0]
    with psycopg.connect(base) as connexion:
        charger_kitsu_staff.charger_staff(connexion, run_kitsu)
        connexion.commit()
    assert lire(base, "SELECT count(*) FROM manga.kitsu_staff")[0][0] == avant


def test_la_normalisation_est_celle_du_module(charge):
    """Une seule normalisation, en Python. Si le SQL en faisait une autre, la
    comparaison aux auteurs MS renverrait « pas de match » en silence."""
    from identity.wikidata_dump import normaliser

    base, _, _ = charge
    lignes = lire(base, "SELECT personne, personne_norm FROM manga.kitsu_staff")
    for personne, norme in lignes:
        assert norme == normaliser(personne)


# --------------------------------------------------------------------------- #
#  Le garde-fou de schéma
# --------------------------------------------------------------------------- #


def test_verifier_prerequis_refuse_un_schema_incomplet(base):
    import psycopg

    with psycopg.connect(base) as connexion:
        connexion.execute("DROP TABLE manga.kitsu_staff")
        connexion.commit()
        with connexion.cursor() as curseur:
            with pytest.raises(
                charger_kitsu_staff.ErreurChargement, match="migration 009"
            ):
                charger_kitsu_staff.verifier_prerequis(curseur)
