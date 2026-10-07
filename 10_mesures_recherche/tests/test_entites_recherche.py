"""Grain entité (agrégation, départage) et fusion par rang réciproque."""

from __future__ import annotations

import numpy as np
import pytest

from mesures_recherche.entites import construire
from mesures_recherche.recherche import classer, fusion

# (chunk_id, source, series_id, kitsu_id) — dans le désordre, exprès.
LIGNES = [
    (30, "kitsu_synopsis", None, 7),
    (10, "ms_review", 5, None),
    (11, "ms_review", 5, None),
    (20, "kitsu_synopsis", 9, 70),  # rattaché : l'entité est la série 9
    (40, "kitsu_synopsis", None, 5),  # kitsu:5, distinct de la série 5
]


@pytest.fixture
def ent():
    return construire(LIGNES, {5, 9})


def test_une_entite_par_fragment_serie_d_abord(ent):
    assert list(ent.chunk_ids) == [10, 11, 20, 30, 40]
    assert [ent.cle(e) for e in ent.entite] == [
        "serie:5",
        "serie:5",
        "serie:9",
        "kitsu:7",
        "kitsu:5",
    ]
    assert len(ent) == 4


def test_score_d_entite_le_meilleur_de_ses_fragments(ent):
    scores = ent.agreger(np.array([0.1, 0.8, 0.3, 0.2, 0.0]))
    assert {ent.cle(e): s for e, s in enumerate(scores)} == {
        "serie:5": 0.8,
        "serie:9": 0.3,
        "kitsu:7": 0.2,
        "kitsu:5": 0.0,
    }


def test_toutes_les_entites_classees_egalites_par_identifiant(ent):
    # Tout à égalité : identifiant croissant, la série d'abord à nombre égal.
    c = classer(ent, ent.agreger(np.zeros(5)))
    assert [cle for cle, _ in c.top(ent, 10)] == [
        "serie:5",
        "kitsu:5",
        "kitsu:7",
        "serie:9",
    ]
    assert c.rang(ent.index_serie[9]) == 4


def test_score_avant_identifiant(ent):
    c = classer(ent, ent.agreger(np.array([0.0, 0.0, 0.9, 0.0, 0.5])))
    assert [cle for cle, _ in c.top(ent, 2)] == ["serie:9", "kitsu:5"]


def test_fragment_sans_entite_refuse():
    with pytest.raises(ValueError, match="sans entité"):
        construire([(1, "autre", None, None)], set())


def test_fragment_inconnu_refuse(ent):
    with pytest.raises(ValueError, match="inconnu"):
        ent.position([10, 99])


def test_fusion_rang_reciproque(ent):
    a = classer(ent, np.array([4.0, 3.0, 2.0, 1.0]))  # entités 0, 1, 2, 3
    b = classer(ent, np.array([1.0, 2.0, 3.0, 4.0]))  # 3, 2, 1, 0
    s = fusion([a, b], k_rrf=60, profondeur=2)
    attendu = np.array([1 / 61, 1 / 62, 1 / 62, 1 / 61])
    np.testing.assert_allclose(s, attendu)


def test_fusion_hors_profondeur_vaut_zero(ent):
    a = classer(ent, np.array([4.0, 3.0, 2.0, 1.0]))
    s = fusion([a], k_rrf=60, profondeur=1)
    assert s[0] == pytest.approx(1 / 61) and not s[1:].any()


# --------------------------------------------------------------------------- #
#  Tour 2 — le périmètre des entités classées
# --------------------------------------------------------------------------- #


def test_perimetre_catalogue_retire_les_entites_kitsu_ordre_conserve(ent):
    # Entités : 0 serie:5, 1 serie:9, 2 kitsu:7, 3 kitsu:5.
    scores = np.array([0.1, 0.8, 0.9, 0.7])
    toutes = classer(ent, scores)
    catalogue = classer(ent, scores, "catalogue")
    assert [c for c, _ in toutes.top(ent, 4)] == [
        "kitsu:7",
        "serie:9",
        "kitsu:5",
        "serie:5",
    ]
    assert [c for c, _ in catalogue.top(ent, 10)] == ["serie:9", "serie:5"]
    assert catalogue.rang(ent.index_serie[5]) == 2 < toutes.rang(ent.index_serie[5])
    np.testing.assert_array_equal(catalogue.scores, toutes.scores)


def test_perimetre_inconnu_refuse(ent):
    with pytest.raises(ValueError, match="périmètre inconnu"):
        classer(ent, np.zeros(4), "series")


def test_fusion_au_catalogue_sur_les_tetes_du_perimetre(ent):
    # Au catalogue, la tête de liste de profondeur 1 est la première SÉRIE.
    a = classer(ent, np.array([0.1, 0.8, 0.9, 0.7]), "catalogue")
    s = fusion([a], k_rrf=60, profondeur=1)
    assert s[ent.index_serie[9]] == pytest.approx(1 / 61) and s.sum() == s[1]


# --------------------------------------------------------------------------- #
#  Tour 3 — représenter l'œuvre entière
# --------------------------------------------------------------------------- #


def test_fragments_par_entite(ent):
    # Entités : 0 serie:5 (fragments 10, 11), 1 serie:9, 2 kitsu:7, 3 kitsu:5.
    assert list(ent.fragments_par_entite) == [2, 1, 1, 1]


def test_vecteur_moyen_ramene_a_la_longueur_1(ent):
    matrice = np.array(
        [[1.0, 0.0], [0.0, 1.0], [0.6, 0.8], [1.0, 0.0], [0.0, 1.0]]
    )  # fragments 10, 11, 20, 30, 40
    v = ent.vecteurs_moyens(matrice)
    np.testing.assert_allclose(v[0], [2**-0.5, 2**-0.5])  # serie:5 : (1,0)+(0,1)
    np.testing.assert_allclose(v[1], [0.6, 0.8])  # un seul fragment : lui-même
    np.testing.assert_allclose(np.linalg.norm(v, axis=1), 1.0)


def test_moyenne_des_trois_meilleurs_ou_de_ceux_qu_on_a():
    lignes = [(c, "ms_review", 5, None) for c in (1, 2, 3, 4)] + [
        (5, "ms_review", 9, None),
        (6, "ms_review", 9, None),
    ]
    e = construire(lignes, {5, 9})
    scores = np.array([0.1, 0.9, 0.5, 0.7, 0.4, 0.2])
    m = e.moyenne_meilleurs(scores, 3)
    assert m[e.index_serie[5]] == pytest.approx((0.9 + 0.7 + 0.5) / 3)
    assert m[e.index_serie[9]] == pytest.approx((0.4 + 0.2) / 2), "deux fragments"


def test_un_seul_fragment_meme_score_dans_les_trois_representations(ent):
    from mesures_recherche.diagnostic import ecart_un_fragment
    from mesures_recherche.recherche import Semantique

    rng = np.random.default_rng(0)
    matrice = rng.normal(size=(5, 4))
    matrice /= np.linalg.norm(matrice, axis=1, keepdims=True)
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    scores = {
        r: Semantique(ent, matrice, lambda _t: q, representation=r).scores(
            type("Q", (), {"texte": "x"})
        )
        for r in ("meilleur_fragment", "vecteur_serie", "trois_meilleurs")
    }
    ref = scores["meilleur_fragment"]
    assert ecart_un_fragment(ent, ref, scores["trois_meilleurs"]) == 0.0
    assert ecart_un_fragment(ent, ref, scores["vecteur_serie"]) < 1e-12
    # serie:5 (deux fragments) : les représentations diffèrent.
    assert scores["vecteur_serie"][0] != pytest.approx(ref[0])


def test_representation_inconnue_refusee(ent):
    from mesures_recherche.recherche import Semantique

    with pytest.raises(ValueError, match="représentation inconnue"):
        Semantique(ent, np.eye(5), None, representation="moyenne")
