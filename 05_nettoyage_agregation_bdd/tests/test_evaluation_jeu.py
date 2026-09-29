"""Les CSV du jeu : ce que l'outil refuse AVANT la base, avec la ligne fautive.

Le modèle versionné du dépôt est lu tel quel : il doit être valide et vide.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evaluation.jeu import (  # noqa: E402
    COLONNES_ATTENDUS,
    COLONNES_QUESTIONS,
    Attendu,
    JeuInvalide,
    Question,
    controler_question,
    ecrire_attendus,
    lire_attendus,
    lire_questions,
    lire_reponse,
)

MODELE = Path(__file__).resolve().parents[2] / "database/donnees/jeu_evaluation/v1"


def q(**champs) -> Question:
    base = {
        "question_id": "Q001",
        "texte": "Berserk",
        "mode": "reconnaissance",
        "famille": "F1",
        "issue_attendue": "au_catalogue",
        "origine": "nouvelle",
        "origine_query_id": "",
        "note": "titre exact",
    }
    return Question(**{**base, **champs})


def test_le_modele_du_depot_est_valide_et_vide():
    assert lire_questions(MODELE / "questions.csv") == []
    assert lire_attendus(MODELE / "attendus.csv", []) == []


def test_le_modele_porte_les_colonnes_du_paragraphe_6():
    with (MODELE / "questions.csv").open(encoding="utf-8") as f:
        assert next(csv.reader(f)) == COLONNES_QUESTIONS
    with (MODELE / "attendus.csv").open(encoding="utf-8") as f:
        assert next(csv.reader(f)) == COLONNES_ATTENDUS
    assert "issue_attendue" in COLONNES_QUESTIONS


def test_une_question_coherente_passe():
    assert controler_question(q()) == []
    assert (
        controler_question(q(mode="refus", famille="F7", issue_attendue="inconnue"))
        == []
    )
    assert (
        controler_question(q(famille="F2", issue_attendue="reconnue_hors_catalogue"))
        == []
    )


@pytest.mark.parametrize(
    ("champs", "motif"),
    [
        (
            {"mode": "reconnaissance", "famille": "F7", "issue_attendue": "inconnue"},
            "F7",
        ),
        ({"mode": "refus", "famille": "F3", "issue_attendue": "inconnue"}, "F7"),
        (
            {
                "famille": "F3",
                "mode": "proposition",
                "issue_attendue": "reconnue_hors_catalogue",
            },
            "reconnaissance",
        ),
        ({"origine": "decembre"}, "origine_query_id"),
        ({"origine_query_id": "12"}, "origine_query_id"),
        ({"question_id": "F3-01"}, "Q001"),
        ({"famille": "F11"}, "famille"),
        ({"mode": "tout"}, "mode"),
        ({"note": ""}, "note"),
    ],
)
def test_question_incoherente(champs, motif):
    assert any(motif in e for e in controler_question(q(**champs)))


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("titre: Berserk", ("titre", "Berserk")),
        ("Auteur :  Naoki Urasawa ", ("auteur", "Naoki Urasawa")),
        ("id:1234", ("id", "1234")),
        ("Berserk", None),
        ("titre:", None),
        ("genre: seinen", None),
    ],
)
def test_lire_reponse(texte, attendu):
    assert lire_reponse(texte) == attendu


def ecrire(chemin: Path, colonnes: list[str], lignes: list[list[str]]) -> Path:
    with chemin.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(colonnes)
        w.writerows(lignes)
    return chemin


def test_toutes_les_erreurs_sont_rassemblees(tmp_path):
    chemin = ecrire(
        tmp_path / "questions.csv",
        COLONNES_QUESTIONS,
        [
            ["Q001", "a", "refus", "F3", "inconnue", "nouvelle", "", "n"],
            ["Q001", "b", "reconnaissance", "F1", "au_catalogue", "decembre", "", "n"],
        ],
    )
    with pytest.raises(JeuInvalide) as e:
        lire_questions(chemin)
    assert len(e.value.erreurs) == 3  # F7, origine, doublon
    assert all("questions.csv:" in m for m in e.value.erreurs)


@pytest.mark.parametrize(
    ("question", "ligne", "motif"),
    [
        (q(), ["Q999", "titre: X", "2", "", "", ""], "question inconnue"),
        (q(), ["Q001", "Berserk", "2", "", "", ""], "reponse_ecrite"),
        (q(), ["Q001", "titre: Berserk", "", "", "", ""], "grade 1 ou 2"),
        (
            q(mode="refus", famille="F7", issue_attendue="inconnue"),
            ["Q001", "titre: Zzz", "2", "", "", ""],
            "pas de grade",
        ),
        (
            q(mode="refus", famille="F7", issue_attendue="inconnue"),
            ["Q001", "id: 12", "", "", "", ""],
            "ne se désigne pas par id",
        ),
    ],
)
def test_attendu_refuse(tmp_path, question, ligne, motif):
    chemin = ecrire(tmp_path / "attendus.csv", COLONNES_ATTENDUS, [ligne])
    with pytest.raises(JeuInvalide, match=motif):
        lire_attendus(chemin, [question])


def test_aller_retour(tmp_path):
    lignes = [
        Attendu(
            "Q001", "auteur: Naoki Urasawa", "2", "12", "Monster", "auteur:dessinateur"
        ),
        Attendu("Q002", "titre: Zzz", ""),
    ]
    ecrire_attendus(tmp_path / "attendus.csv", lignes)
    questions = [
        q(),
        q(question_id="Q002", mode="refus", famille="F7", issue_attendue="inconnue"),
    ]
    assert lire_attendus(tmp_path / "attendus.csv", questions) == lignes


@pytest.mark.parametrize(
    ("question", "ligne", "motif"),
    [
        (
            q(famille="F3", mode="proposition"),
            ["Q001", "regle: Q001", "1", "", "", ""],
            "F9 et F10",
        ),
        (
            q(famille="F10", mode="reconnaissance"),
            ["Q001", "regle: Q001", "1", "", "", ""],
            "F9 et F10",
        ),
        (
            q(famille="F9", mode="proposition"),
            ["Q001", "regle: Q002", "1", "", "", ""],
            "SA règle",
        ),
    ],
)
def test_regle_refusee_a_la_lecture(tmp_path, question, ligne, motif):
    chemin = ecrire(tmp_path / "attendus.csv", COLONNES_ATTENDUS, [ligne])
    with pytest.raises(JeuInvalide, match=motif):
        lire_attendus(chemin, [question])


def test_regle_admise_a_la_lecture(tmp_path):
    chemin = ecrire(
        tmp_path / "attendus.csv",
        COLONNES_ATTENDUS,
        [["Q001", "regle: Q001", "1", "", "", ""]],
    )
    assert len(lire_attendus(chemin, [q(famille="F9", mode="proposition")])) == 1


# --------------------------------------------------------------------------- #
#  Le format source : un seul CSV écrit à la main (v1)
# --------------------------------------------------------------------------- #

from evaluation.jeu import COLONNES_SOURCE, analyser_source  # noqa: E402


def source(tmp_path, lignes):
    return ecrire(tmp_path / "jeu_evaluation_recherche.csv", COLONNES_SOURCE, lignes)


def ligne_source(qid="Q001", **champs):
    base = {
        "id": qid,
        "texte": "t",
        "mode": "reconnaissance",
        "famille": "F1",
        "issue_attendue": "au_catalogue",
        "series_attendues": "Berserk",
        "grade": "",
        "origine": "nouvelle",
        "note": "n",
        "cle_confirmation": "",
    }
    base.update(champs)
    return [base[c] for c in COLONNES_SOURCE]


def test_source_titres_grades_cles_et_regles(tmp_path):
    chemin = source(
        tmp_path,
        [
            ligne_source(
                "Q001",
                series_attendues="Monster",
                cle_confirmation="Monster => auteur: Naoki Urasawa",
            ),
            ligne_source(
                "Q002",
                mode="proposition",
                series_attendues="In/Spectre | Akira",
                grade="In/Spectre: tres_pertinent | Akira: pertinent",
            ),
            ligne_source(
                "Q003",
                mode="proposition",
                famille="F9",
                series_attendues="règle : shonen",
                grade="par règle",
            ),
            ligne_source(
                "Q004",
                mode="refus",
                famille="F7",
                issue_attendue="inconnue",
                series_attendues="",
            ),
            ligne_source(
                "Q005",
                famille="F2",
                issue_attendue="reconnue_hors_catalogue",
                series_attendues="Oishinbo",
            ),
        ],
    )
    questions, attendus, erreurs = analyser_source(chemin)
    assert erreurs == [] and len(questions) == 5
    assert [(a.question_id, a.reponse_ecrite, a.grade) for a in attendus] == [
        ("Q001", "titre: Monster ; auteur: Naoki Urasawa", "2"),
        ("Q002", "titre: In/Spectre", "2"),
        ("Q002", "titre: Akira", "1"),
        ("Q003", "regle: Q003", "1"),
        ("Q005", "titre: Oishinbo", ""),
    ]


@pytest.mark.parametrize(
    ("champs", "motif"),
    [
        (
            {"grade": "Berserk: pertinent (parodique)", "mode": "proposition"},
            "grade inconnu",
        ),
        ({"cle_confirmation": "Akira => auteur: Otomo"}, "sans titre attendu"),
        ({"cle_confirmation": "Berserk -> Miura"}, "illisible"),
        (
            {"mode": "refus", "famille": "F7", "issue_attendue": "inconnue"},
            "n'attend aucune",
        ),
        (
            {"grade": "par règle", "famille": "F9", "mode": "proposition"},
            "sans règle écrite",
        ),
        (
            {
                "famille": "F5",
                "mode": "proposition",
                "issue_attendue": "reconnue_hors_catalogue",
            },
            "reconnaissance",
        ),
    ],
)
def test_source_erreurs_rattachees_a_leur_question(tmp_path, champs, motif):
    _, _, erreurs = analyser_source(source(tmp_path, [ligne_source(**champs)]))
    assert erreurs and all(qid == "Q001" for qid, _ in erreurs)
    assert any(motif in m for _, m in erreurs)
