"""Le jeu gelé v2, lu par famille et par issue, empreinte vérifiée d'abord.

L'empreinte est celle de la règle du gel, calculée par le module 10
(`mesures_recherche.jeu.empreinte_fichiers`, chargé par son chemin) : refus si les
fichiers versionnés ne la tiennent pas. Une question n'est jamais désignée une par
une : un jeu est une union de conditions, `famille:<F>` ou `issue:<issue>`.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from api_modele import banc
from api_modele.configuration import RACINE_MODULE

DOSSIER = RACINE_MODULE.parent / "database" / "donnees" / "jeu_evaluation" / "v2"
EMPREINTE = "31712b0d0b0cbd318ef228da105673af0cba2db431a72462f7635b2f6ec78b30"
#: Le nom court d'une issue → sa valeur dans le jeu.
ISSUES = {
    "hors_catalogue": "reconnue_hors_catalogue",
    "inconnue": "inconnue",
    "au_catalogue": "au_catalogue",
}


class JeuRefuse(Exception):
    """Le jeu n'est pas celui du gel, ou sa désignation est fautive."""


@dataclass(frozen=True, slots=True)
class Question:
    question_id: str
    texte: str
    famille: str
    issue: str
    #: Les séries attendues (`series_id`), vides hors du catalogue.
    attendues: tuple[int, ...]

    @property
    def attend_une_serie(self) -> bool:
        return self.issue == "au_catalogue" and bool(self.attendues)


def conditions(designation: str) -> tuple[tuple[str, str], ...]:
    """`famille:F7,issue:hors_catalogue` → ((« famille », « F7 »), (« issue »,
    « reconnue_hors_catalogue »)) ; refus d'une forme inconnue."""
    sortie: list[tuple[str, str]] = []
    for element in designation.split(","):
        cle, _, valeur = element.strip().partition(":")
        if cle == "famille" and valeur.startswith("F") and valeur[1:].isdigit():
            sortie.append((cle, valeur))
        elif cle == "issue" and valeur in ISSUES:
            sortie.append((cle, ISSUES[valeur]))
        else:
            raise JeuRefuse(
                f"désignation inconnue : « {element.strip()} » (famille:F<n> ou"
                f" issue:{'|'.join(ISSUES)})"
            )
    return tuple(sortie)


def nom_court(designation: str) -> str:
    """Le nom du jeu dans un nom de run : `F7+hors_catalogue`."""
    return "+".join(e.strip().partition(":")[2] for e in designation.split(","))


def empreinte(dossier: Path = DOSSIER) -> str:
    calcul = cast(Callable[[Path], str], banc.module("jeu").empreinte_fichiers)
    return calcul(dossier)


def lire(designation: str, dossier: Path = DOSSIER) -> tuple[list[Question], str]:
    """Les questions désignées, dans l'ordre de leurs identifiants, et l'empreinte
    vérifiée du jeu."""
    choix = conditions(designation)
    trouvee = empreinte(dossier)
    if trouvee != EMPREINTE:
        raise JeuRefuse(
            f"empreinte du jeu {trouvee[:12]}… ≠ {EMPREINTE[:12]}… : rien n'est mesuré"
        )
    attendues: dict[str, list[int]] = {}
    with (dossier / "attendus.csv").open(encoding="utf-8", newline="") as f:
        for ligne in csv.DictReader(f):
            # Une œuvre attendue hors du catalogue n'a pas de `series_id`.
            if ligne["series_id"].strip():
                attendues.setdefault(ligne["question_id"], []).append(
                    int(ligne["series_id"])
                )
    questions: list[Question] = []
    with (dossier / "questions.csv").open(encoding="utf-8", newline="") as f:
        for ligne in csv.DictReader(f):
            q = Question(
                question_id=ligne["question_id"],
                texte=ligne["texte"],
                famille=ligne["famille"],
                issue=ligne["issue_attendue"],
                attendues=tuple(attendues.get(ligne["question_id"], [])),
            )
            if any(
                (cle == "famille" and q.famille == v)
                or (cle == "issue" and q.issue == v)
                for cle, v in choix
            ):
                questions.append(q)
    if not questions:
        raise JeuRefuse(f"aucune question pour « {designation} »")
    return sorted(questions, key=lambda q: q.question_id), trouvee


def texte_de(question_id: str, dossier: Path = DOSSIER) -> str:
    """Le texte d'une question du jeu par son identifiant (premier tour d'un
    scénario de conversation, que Max désigne par son identifiant)."""
    with (dossier / "questions.csv").open(encoding="utf-8", newline="") as f:
        for ligne in csv.DictReader(f):
            if ligne["question_id"] == question_id:
                return ligne["texte"]
    raise JeuRefuse(f"question inconnue du jeu : {question_id}")
