"""Les deux CSV du jeu : lecture, validation, écriture.

Les CSV font foi (E3) ; tout ce que la migration `016` refusera, ce module le
refuse AVANT, avec le numéro de ligne — une erreur lisible par celui qui écrit,
plutôt qu'un nom de contrainte au moment du gel.

Les erreurs sont RASSEMBLÉES, pas levées une à une : devant cinquante questions,
corriger une faute par exécution serait une punition.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, replace
from pathlib import Path

COLONNES_QUESTIONS = [
    "question_id",
    "texte",
    "mode",
    "famille",
    "issue_attendue",
    "origine",
    "origine_query_id",
    "note",
]
COLONNES_ATTENDUS = [
    "question_id",
    "reponse_ecrite",
    "grade",
    "series_id",
    "titre_catalogue",
    "confirmation",
]

MODES = ("proposition", "reconnaissance", "refus")
ISSUES = ("au_catalogue", "reconnue_hors_catalogue", "inconnue")
ORIGINES = ("nouvelle", "decembre")
FAMILLES = tuple(f"F{i}" for i in range(1, 11))
VOIES = ("titre", "auteur", "id")

REPONSE = re.compile(r"^\s*(titre|auteur|id)\s*:\s*(\S.*?)\s*$", re.IGNORECASE)


class JeuInvalide(Exception):
    """Le jeu viole au moins une règle ; `erreurs` les porte toutes."""

    def __init__(self, erreurs: list[str]):
        super().__init__(f"{len(erreurs)} erreur(s) :\n  " + "\n  ".join(erreurs))
        self.erreurs = erreurs


@dataclass(frozen=True)
class Question:
    question_id: str
    texte: str
    mode: str
    famille: str
    issue_attendue: str
    origine: str
    origine_query_id: str
    note: str


@dataclass(frozen=True)
class Attendu:
    question_id: str
    reponse_ecrite: str
    grade: str
    series_id: str = ""
    titre_catalogue: str = ""
    confirmation: str = ""

    def completer(self, series_id="", titre_catalogue="", confirmation="") -> Attendu:
        return replace(
            self,
            series_id=str(series_id),
            titre_catalogue=titre_catalogue,
            confirmation=confirmation,
        )


def lire_reponse(texte: str) -> tuple[str, str] | None:
    """`titre: Berserk` → (`titre`, `Berserk`) ; None si la forme est inconnue."""
    m = REPONSE.match(texte or "")
    return (m[1].lower(), m[2]) if m else None


def _lire(chemin: Path, colonnes: list[str]) -> list[dict[str, str]]:
    if not chemin.is_file():
        raise JeuInvalide([f"{chemin} introuvable"])
    with chemin.open(encoding="utf-8", newline="") as f:
        lecteur = csv.DictReader(f)
        if lecteur.fieldnames != colonnes:
            raise JeuInvalide(
                [f"{chemin.name} : en-tête {lecteur.fieldnames}, attendu {colonnes}"]
            )
        return [{k: (v or "").strip() for k, v in ligne.items()} for ligne in lecteur]


def controler_question(q: Question) -> list[str]:
    """Les règles d'une question isolée — celles des CHECK de `016`."""
    e = []
    if not re.fullmatch(r"Q[0-9]{3}", q.question_id):
        e.append("question_id attendu au format Q001")
    if not q.texte:
        e.append("texte vide")
    if not q.note:
        e.append("note vide")
    for champ, valeur, admis in (
        ("mode", q.mode, MODES),
        ("famille", q.famille, FAMILLES),
        ("issue_attendue", q.issue_attendue, ISSUES),
        ("origine", q.origine, ORIGINES),
    ):
        if valeur not in admis:
            e.append(f"{champ} {valeur!r} hors de {admis}")
    if not (
        (q.famille == "F7") == (q.mode == "refus") == (q.issue_attendue == "inconnue")
    ):
        e.append("F7, refus et inconnue vont ensemble, et seulement ensemble")
    if q.issue_attendue == "reconnue_hors_catalogue" and not (
        q.famille in ("F1", "F2") and q.mode == "reconnaissance"
    ):
        e.append("reconnue_hors_catalogue exige F1 ou F2, en reconnaissance")
    if (q.origine == "decembre") != bool(q.origine_query_id):
        e.append("origine_query_id renseigné si et seulement si origine = decembre")
    if q.origine_query_id and not q.origine_query_id.isdigit():
        e.append("origine_query_id doit être un entier")
    return e


def lire_questions(chemin: Path) -> list[Question]:
    erreurs, questions, vus = [], [], set()
    for n, ligne in enumerate(_lire(chemin, COLONNES_QUESTIONS), start=2):
        q = Question(**ligne)
        erreurs += [
            f"{chemin.name}:{n} {q.question_id} — {m}" for m in controler_question(q)
        ]
        if q.question_id in vus:
            erreurs.append(f"{chemin.name}:{n} {q.question_id} — identifiant en double")
        vus.add(q.question_id)
        questions.append(q)
    if erreurs:
        raise JeuInvalide(erreurs)
    return questions


def lire_attendus(chemin: Path, questions: list[Question]) -> list[Attendu]:
    par_id = {q.question_id: q for q in questions}
    erreurs, attendus = [], []
    for n, ligne in enumerate(_lire(chemin, COLONNES_ATTENDUS), start=2):
        a = Attendu(**ligne)
        ou = f"{chemin.name}:{n} {a.question_id}"
        q = par_id.get(a.question_id)
        if q is None:
            erreurs.append(f"{ou} — question inconnue")
            continue
        voie = lire_reponse(a.reponse_ecrite)
        if voie is None:
            erreurs.append(
                f"{ou} — reponse_ecrite attendue : « titre: … », « auteur: … »"
                " ou « id: … »"
            )
        if q.issue_attendue == "au_catalogue" and a.grade not in ("1", "2"):
            erreurs.append(f"{ou} — grade 1 ou 2 exigé pour une issue au_catalogue")
        if q.issue_attendue != "au_catalogue" and a.grade:
            erreurs.append(f"{ou} — pas de grade pour une issue {q.issue_attendue}")
        if q.issue_attendue != "au_catalogue" and voie and voie[0] == "id":
            erreurs.append(
                f"{ou} — une issue {q.issue_attendue} ne se désigne pas par id"
            )
        attendus.append(a)
    if erreurs:
        raise JeuInvalide(erreurs)
    return attendus


def ecrire_attendus(chemin: Path, attendus: list[Attendu]) -> None:
    with chemin.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(COLONNES_ATTENDUS)
        for a in attendus:
            w.writerow([getattr(a, c) for c in COLONNES_ATTENDUS])
