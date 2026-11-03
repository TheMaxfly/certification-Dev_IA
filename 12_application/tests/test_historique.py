"""L'historique porté par la page : revalidé, jamais gardé."""

from __future__ import annotations

import json

import pytest

from application import historique as h
from application.config import Limites

LIMITES = Limites(
    question_caracteres_max=500,
    echanges=5,
    tours_max=10,
    caracteres_max=4000,
    tour_caracteres_max=2000,
)


def paire(n: int = 1, contenu: str = "x") -> list[dict[str, str]]:
    return [
        {"role": role, "contenu": contenu}
        for _ in range(n)
        for role in ("libraire", "assistant")
    ]


@pytest.mark.parametrize("brut", ["", "   ", "[]"])
def test_vide_une_conversation_neuve(brut):
    assert h.lire(brut, LIMITES) == []


def test_un_historique_valide_est_lu_et_reecrit_a_l_identique():
    tours = paire(2, "é")

    lus = h.lire(json.dumps(tours), LIMITES)

    assert [t.role for t in lus] == ["libraire", "assistant"] * 2
    assert h.en_liste(lus) == tours
    assert json.loads(h.ecrire(lus)) == tours
    assert "é" in h.ecrire(lus), "le texte, pas son échappement"
    assert h.taille(lus) == 4


def test_ecrire_un_historique_vide_rend_un_champ_vide():
    assert h.ecrire([]) == ""


@pytest.mark.parametrize(
    "brut",
    [
        "{cassé",
        '{"role": "libraire"}',
        "[1]",
        json.dumps([{"role": "libraire"}]),
        json.dumps([{"role": "libraire", "contenu": ""}]),
        json.dumps([{"role": "libraire", "contenu": 3}]),
        json.dumps(
            [{"role": "juge", "contenu": "x"}, {"role": "assistant", "contenu": "y"}]
        ),
        json.dumps([{"role": "libraire", "contenu": "x", "autre": 1}]),
        json.dumps(paire(6)),
        json.dumps(paire(2, "y" * 1001)),
        json.dumps(
            [
                {"role": "libraire", "contenu": "y" * 2001},
                {"role": "assistant", "contenu": "z"},
            ]
        ),
        json.dumps(paire(1)[::-1]),
        json.dumps(paire(1) + paire(1)[1:]),
        json.dumps(paire(1)[:1]),
    ],
    ids=[
        "json_casse",
        "pas_une_liste",
        "pas_un_tour",
        "champ_manquant",
        "contenu_vide",
        "contenu_non_texte",
        "role_inconnu",
        "champ_de_plus",
        "onze_tours_et_plus",
        "plus_de_4000_caracteres",
        "un_tour_de_plus_de_2000",
        "premier_tour_assistant",
        "roles_non_alternes",
        "dernier_tour_libraire",
    ],
)
def test_un_historique_invalide_est_refuse(brut):
    with pytest.raises(h.HistoriqueInvalide):
        h.lire(brut, LIMITES)


def test_les_bornes_exactes_sont_admises():
    tours = [
        {"role": "libraire", "contenu": "y" * 2000},
        {"role": "assistant", "contenu": "z" * 2000},
    ]

    assert len(h.lire(json.dumps(tours), LIMITES)) == 2
    assert len(h.lire(json.dumps(paire(5)), LIMITES)) == 10


@pytest.mark.parametrize(
    ("tours", "attendu"),
    [
        ([h.Tour("libraire", "x" * 4000)], False),
        ([h.Tour("libraire", "x" * 2000), h.Tour("assistant", "x" * 2001)], True),
        ([h.Tour("libraire", "x" * 2001)], True),
    ],
)
def test_depasse_dit_si_le_nouvel_historique_ne_tient_plus(tours, attendu):
    limites = Limites(500, 5, 10, 4000, 4000) if attendu is False else LIMITES

    assert h.depasse(tours, limites) is attendu
