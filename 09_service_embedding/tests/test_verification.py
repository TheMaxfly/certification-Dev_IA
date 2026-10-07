"""Les valeurs posées d'avance, et l'empreinte du jeu recalculée."""

from __future__ import annotations

from service_embedding.verification import (
    DOSSIER_JEU,
    EMPREINTE_JEU,
    controle,
    empreinte_fichiers,
)


def test_empreinte_du_jeu_v2_recalculee_depuis_les_fichiers():
    # Même règle que `evaluation.geler.empreinte` (module 05) : si elle
    # divergeait, le contrôle « empreinte inchangée » ne prouverait rien.
    assert empreinte_fichiers(DOSSIER_JEU) == EMPREINTE_JEU


def test_controle_compare_strictement():
    assert controle("x", 0, 0)["conforme"]
    assert not controle("x", 66_290, 66_289)["conforme"]
    assert not controle("x", [1, 2], [2, 1])["conforme"]
