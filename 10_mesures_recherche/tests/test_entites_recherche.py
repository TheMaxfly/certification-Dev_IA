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
