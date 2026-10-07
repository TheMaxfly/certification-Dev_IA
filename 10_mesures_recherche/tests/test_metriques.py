"""Les métriques, sur des cas écrits à la main (spec E2 jour 2, §2.4)."""

from __future__ import annotations

import math

import pytest

from mesures_recherche.jeu import Question
from mesures_recherche.metriques import (
    agreger,
    grades_du_classement,
    hit_rate,
    mesurer,
    mrr,
    ndcg,
)


def question(
    qid="Q001", attendus=None, mode="proposition", famille="F1", issue="au_catalogue"
):
    return Question(
        qid, "texte", mode, famille, issue, {1: 2} if attendus is None else attendus
    )


def top(*cles):
    """Dix entités : les clés données d'abord, puis des entités Kitsu neutres."""
    cles = list(cles)
    return cles + [f"kitsu:{900 + i}" for i in range(10 - len(cles))]


def test_rang_1():
    m = mesurer(question(attendus={1: 2}), top("serie:1"))
    assert m == {"hit_rate@5": 1.0, "hit_rate@10": 1.0, "mrr@10": 1.0, "ndcg@10": 1.0}


def test_rang_11_hors_des_dix_premiers():
    # Les dix premiers ne contiennent pas la série : elle est au rang 11 au mieux.
    m = mesurer(question(attendus={1: 2}), top())
    assert m == {"hit_rate@5": 0.0, "hit_rate@10": 0.0, "mrr@10": 0.0, "ndcg@10": 0.0}


def test_rang_6_entre_5_et_10():
    m = mesurer(
        question(attendus={1: 1}), top(*[f"kitsu:{i}" for i in range(5)], "serie:1")
    )
    assert m["hit_rate@5"] == 0.0 and m["hit_rate@10"] == 1.0
    assert m["mrr@10"] == pytest.approx(1 / 6)
    assert m["ndcg@10"] == pytest.approx(1 / math.log2(7))


def test_aucune_serie_trouvee():
    m = mesurer(question(attendus={1: 2, 2: 1}), top("serie:3", "serie:4"))
    assert m == {"hit_rate@5": 0.0, "hit_rate@10": 0.0, "mrr@10": 0.0, "ndcg@10": 0.0}


def test_deux_grades_ordre_ideal_et_inverse():
    attendus = {1: 2, 2: 1}
    ideal = 2 / math.log2(2) + 1 / math.log2(3)
    assert mesurer(question(attendus=attendus), top("serie:1", "serie:2"))[
        "ndcg@10"
    ] == pytest.approx(1.0)
    inverse = mesurer(question(attendus=attendus), top("serie:2", "serie:1"))
    assert inverse["ndcg@10"] == pytest.approx((1 / 1 + 2 / math.log2(3)) / ideal)
    assert inverse["mrr@10"] == 1.0, "le MRR ne regarde pas le grade"


def test_question_sans_attendu_n_a_pas_de_metrique():
    assert (
        mesurer(
            question(attendus={}, mode="refus", famille="F7", issue="inconnue"),
            top("serie:1"),
        )
        is None
    )
    hors = question(attendus={}, mode="reconnaissance", issue="reconnue_hors_catalogue")
    assert mesurer(hors, top()) is None


def test_une_entite_kitsu_ne_vaut_jamais_un_grade():
    # kitsu:1 n'est pas la série 1, même à identifiant égal.
    assert grades_du_classement(["kitsu:1", "serie:1"], {1: 2}) == [0, 2]


def test_fonctions_elementaires():
    assert hit_rate([0, 0, 1], 2) == 0.0 and hit_rate([0, 0, 1], 3) == 1.0
    assert mrr([0, 2, 1], 10) == 0.5
    assert ndcg([0, 0], {1: 1}, 10) == 0.0


def test_agreger_portees_perimetres_et_exclusions():
    questions = [
        question("Q001", {1: 2}, "proposition", "F1"),
        question("Q002", {2: 2}, "reconnaissance", "F1"),
        question("Q003", {}, "refus", "F7", "inconnue"),
    ]
    par_question = {
        "Q001": mesurer(questions[0], top("serie:1")),
        "Q002": mesurer(questions[1], top()),
        "Q003": None,
    }
    lignes = {
        (lg.portee, lg.perimetre, lg.metrique, lg.k): (lg.valeur, lg.n_questions)
        for lg in agreger(questions, par_question, frozenset({1}))
    }
    assert lignes[("global", "toutes", "hit_rate", 10)] == (0.5, 2)
    assert lignes[("global", "atteignables", "hit_rate", 10)] == (1.0, 1)
    assert lignes[("famille:F1", "toutes", "mrr", 10)] == (0.5, 2)
    assert lignes[("mode:reconnaissance", "toutes", "ndcg", 10)] == (0.0, 1)
    # Aucune ligne sur zéro question : ni F7, ni le mode refus, ni la
    # reconnaissance au périmètre atteignable (la série 2 ne l'est pas).
    assert not any(p in ("famille:F7", "mode:refus") for p, *_ in lignes)
    assert ("mode:reconnaissance", "atteignables", "mrr", 10) not in lignes
