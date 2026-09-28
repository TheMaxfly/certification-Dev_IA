"""Pseudonymes : repérage, masquage, et les deux listes versionnées (D5).

Les listes RÉELLES du dépôt sont vérifiées telles quelles — une fixture
inventée passerait au vert pendant que le vrai fichier serait faux. Les refus
sont éprouvés sur des listes fabriquées ici.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corpus import pseudonymes as p  # noqa: E402

DONNEES = Path(__file__).resolve().parents[2] / "database/donnees"


def test_empreinte_stable():
    assert p.empreinte("abc") == "ba7816bf8f01cfea"


@pytest.mark.parametrize(
    ("texte", "trouve"),
    [
        ("Merci à Tori pour cette critique", True),
        ("merci à TORI.", True),
        ("(par Tori)", True),
        ("Toriko est un manga", False),
        ("Satori", False),
        ("Tori_2", False),
    ],
)
def test_mot_entier_casse_ignoree(texte, trouve):
    assert bool(p.motif("Tori").search(texte)) is trouve


def test_pseudonyme_avec_ponctuation_echappee():
    assert p.motif("K.O").search("salut K.O !")
    assert not p.motif("K.O").search("salut KXO !")


def test_masquer_remplace_toutes_les_occurrences():
    texte, n = p.masquer("Tori, puis tori et Toriko.", "Tori")
    assert (texte, n) == (f"{p.JETON}, puis {p.JETON} et Toriko.", 2)


# --------------------------------------------------------------------------- #
#  Les listes du dépôt
# --------------------------------------------------------------------------- #


def test_listes_du_depot_valides():
    masquage, homonymes = p.lire_listes(DONNEES)
    assert len(masquage) == 20
    assert len({e.doc_key for e in masquage}) == 19
    # 27 homonymes de critiques, qualifiés à la lecture ; les homonymes Kitsu sont
    # régénérés par provenance (`corpus.homonymes`) depuis le raw de juillet.
    assert len(homonymes) == 155
    assert Counter(e.valeur for e in homonymes)["provenance_kitsu"] == 128
    assert sum(1 for e in homonymes if e.doc_key.startswith("ms_review:")) == 27


def test_listes_du_depot_sans_pseudonyme_en_clair():
    """Aucune colonne ne porte autre chose qu'une clé, une empreinte, une
    catégorie : le dépôt est public."""
    for nom in (p.FICHIER_MASQUAGE, p.FICHIER_HOMONYMES):
        with (DONNEES / nom).open(encoding="utf-8") as f:
            for ligne in csv.DictReader(f):
                assert ligne["doc_key"].split(":")[0] in ("ms_review", "kitsu")
                assert ligne["doc_key"].split(":")[1].isdigit()


# --------------------------------------------------------------------------- #
#  Refus
# --------------------------------------------------------------------------- #


def ecrire(chemin: Path, entete: list[str], lignes: list[tuple]) -> Path:
    with chemin.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(entete)
        w.writerows(lignes)
    return chemin


@pytest.mark.parametrize(
    ("entete", "lignes", "message"),
    [
        (["doc_key", "empreinte", "nature"], [], "en-tête"),
        (
            ["doc_key", "empreinte_pseudo", "nature"],
            [("ms_review:1", "ba7816bf8f01cfea", "rumeur")],
            "valeur inconnue",
        ),
        (
            ["doc_key", "empreinte_pseudo", "nature"],
            [("ms_review:1", "Tori", "signature")],
            "empreinte mal formée",
        ),
        (
            ["doc_key", "empreinte_pseudo", "nature"],
            [("ms_review:1", "ba7816bf8f01cfea", "signature")] * 2,
            "doublon",
        ),
    ],
)
def test_liste_refusee(tmp_path, entete, lignes, message):
    chemin = ecrire(tmp_path / "l.csv", entete, lignes)
    with pytest.raises(p.ListeInvalide, match=message):
        p.lire_liste(chemin, "nature", p.NATURES)


def test_couple_masque_et_admis_refuse(tmp_path):
    couple = ("ms_review:1", "ba7816bf8f01cfea")
    ecrire(
        tmp_path / p.FICHIER_MASQUAGE,
        ["doc_key", "empreinte_pseudo", "nature"],
        [(*couple, "signature")],
    )
    ecrire(
        tmp_path / p.FICHIER_HOMONYMES,
        ["doc_key", "empreinte_pseudo", "motif"],
        [(*couple, "personnage")],
    )
    with pytest.raises(p.ListeInvalide, match="à la fois"):
        p.lire_listes(tmp_path)


def test_masquage_perime_refuse():
    docs = {"ms_review:1": {"doc_text": "rien à masquer", "title": "t"}}
    entree = p.Entree("ms_review:1", p.empreinte("Tori"), "signature")
    with pytest.raises(p.ListeInvalide, match="n'y figure plus"):
        p.appliquer_masquage(docs, [entree], {p.empreinte("Tori"): "Tori"})
    with pytest.raises(p.ListeInvalide, match="absent"):
        p.appliquer_masquage({}, [entree], {p.empreinte("Tori"): "Tori"})
    with pytest.raises(p.ListeInvalide, match="sans pseudonyme"):
        p.appliquer_masquage(docs, [entree], {})


def test_homonyme_perime_refuse():
    docs = {"kitsu:1": {"doc_text": "a bird", "title": "Birds"}}
    entree = p.Entree("kitsu:1", p.empreinte("Tori"), "provenance_kitsu")
    with pytest.raises(p.ListeInvalide, match="n'y figure plus"):
        p.verifier_homonymes(docs, [entree], {p.empreinte("Tori"): "Tori"})
    docs["kitsu:1"]["title"] = "Tori"
    p.verifier_homonymes(docs, [entree], {p.empreinte("Tori"): "Tori"})
