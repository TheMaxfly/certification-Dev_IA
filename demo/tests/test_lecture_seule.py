"""Le mode lecture seule ne peut pas écrire — vérifié, pas promis.

Deux barrières indépendantes, testées séparément :
  1. le menu ne PROPOSE aucune action d'écriture base (on ne déclenche pas par
     erreur ce qui est absent) ;
  2. même appelé directement, l'exécuteur REFUSE une action d'écriture base en
     lecture seule (la barrière ne dépend pas de l'affichage).
"""

from __future__ import annotations

import pytest

from manga_pipeline.actions import ACTIONS, ACTIONS_PAR_CLE, Impact, actions_visibles
from manga_pipeline.executeur import (
    MOT_DE_CONFIRMATION,
    RefusEcriture,
    confirmation_valide,
    executer,
)


def test_le_menu_lecture_seule_masque_les_ecritures_base():
    visibles = actions_visibles(lecture_seule=True)
    assert visibles, "le menu lecture seule ne peut pas être vide"
    assert all(not a.ecrit_en_base for a in visibles)


def test_le_mode_ecriture_propose_tout():
    assert len(actions_visibles(lecture_seule=False)) == len(ACTIONS)


def test_il_existe_bien_des_actions_base_a_masquer():
    """Sans ça, le test précédent passerait pour une mauvaise raison."""
    assert any(a.ecrit_en_base for a in ACTIONS)


@pytest.mark.parametrize("cle", [a.cle for a in ACTIONS if a.impact is Impact.BASE])
def test_executeur_refuse_ecriture_en_lecture_seule(cle):
    """La barrière tient même si l'appel contourne le menu."""
    with pytest.raises(RefusEcriture):
        executer(ACTIONS_PAR_CLE[cle], lecture_seule=True)


def test_executeur_refuse_les_actions_documentees():
    documentees = [a for a in ACTIONS if a.impact is Impact.DOCUMENTE]
    assert documentees
    with pytest.raises(RefusEcriture):
        executer(documentees[0], lecture_seule=False)


def test_confirmation_ecriture_base_exige_le_mot_exact():
    action = next(a for a in ACTIONS if a.ecrit_en_base)
    for saisie in ["", "o", "oui", "yes", "ecrire", "ECRIRE ", "ÉCRIRE"]:
        if saisie.strip() == MOT_DE_CONFIRMATION:
            continue
        assert not confirmation_valide(action, saisie), f"{saisie!r} ne doit pas passer"
    assert confirmation_valide(action, MOT_DE_CONFIRMATION)


def test_confirmation_simple_pour_les_actions_sans_ecriture_base():
    action = next(a for a in ACTIONS if a.impact is Impact.LECTURE)
    assert confirmation_valide(action, "o")
    assert confirmation_valide(action, "")  # Entrée = accepter
    assert not confirmation_valide(action, "non")
