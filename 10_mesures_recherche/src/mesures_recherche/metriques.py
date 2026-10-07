"""Métriques au grain entité — définies une fois, testées sur des cas à la main.

Pour une question de rang (au moins une série attendue, au catalogue) et les
k premières entités d'un classement :
  - grades : pour chaque rang, le grade attendu de l'entité (2, 1), 0 sinon —
    une entité Kitsu, n'étant pas une série du catalogue, vaut toujours 0 ;
  - hit_rate@k : 1 si une série attendue figure dans les k premiers, sinon 0 ;
  - mrr@k : 1 / rang de la première série attendue dans les k premiers, sinon 0 ;
  - ndcg@k : Σ grade / log2(rang + 1), rapporté au même calcul sur les grades
    attendus triés par ordre décroissant et tronqués à k.
Une question sans série attendue n'a pas de métrique de rang : None.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from mesures_recherche.jeu import Question

METRIQUES = (("hit_rate", 5), ("hit_rate", 10), ("mrr", 10), ("ndcg", 10))


def grades_du_classement(top: Sequence[str], attendus: dict[int, int]) -> list[int]:
    grades = []
    for cle in top:
        genre, ident = cle.split(":")
        grades.append(attendus.get(int(ident), 0) if genre == "serie" else 0)
    return grades


def hit_rate(grades: Sequence[int], k: int) -> float:
    return 1.0 if any(g > 0 for g in grades[:k]) else 0.0


def mrr(grades: Sequence[int], k: int) -> float:
    for rang, g in enumerate(grades[:k], start=1):
        if g > 0:
            return 1.0 / rang
    return 0.0


def dcg(grades: Sequence[int], k: int) -> float:
    return math.fsum(g / math.log2(r + 1) for r, g in enumerate(grades[:k], start=1))


def ndcg(grades: Sequence[int], attendus: dict[int, int], k: int) -> float:
    ideal = dcg(sorted(attendus.values(), reverse=True), k)
    return dcg(grades, k) / ideal


def mesurer(question: Question, top: Sequence[str]) -> dict[str, float] | None:
    """Les métriques de la question, ou None si elle n'est pas de rang."""
    if not question.de_rang:
        return None
    grades = grades_du_classement(top, question.attendus)
    valeurs = {"hit_rate": hit_rate, "mrr": mrr}
    resultat = {}
    for nom, k in METRIQUES:
        if nom == "ndcg":
            resultat[f"{nom}@{k}"] = ndcg(grades, question.attendus, k)
        else:
            resultat[f"{nom}@{k}"] = valeurs[nom](grades, k)
    return resultat


@dataclass(frozen=True)
class Ligne:
    """Une ligne de bench.eval_mesures (hors run_id et jeu_version)."""

    portee: str
    perimetre: str
    metrique: str
    k: int
    valeur: float
    n_questions: int


def portees(question: Question) -> list[str]:
    return ["global", f"mode:{question.mode}", f"famille:{question.famille}"]


def atteignable(question: Question, atteignables: frozenset[int]) -> bool:
    return any(s in atteignables for s in question.attendus)


def agreger(
    questions: Sequence[Question],
    par_question: dict[str, dict[str, float] | None],
    atteignables: frozenset[int],
) -> list[Ligne]:
    """Moyennes par portée et par périmètre, sur les seules questions de rang.

    Une portée sans question de rang dans un périmètre n'a pas de ligne : une
    moyenne sur zéro question n'existe pas.
    """
    groupes: dict[tuple[str, str], list[dict[str, float]]] = {}
    for q in questions:
        valeurs = par_question.get(q.question_id)
        if valeurs is None:
            continue
        perimetres = ["toutes"] + (
            ["atteignables"] if atteignable(q, atteignables) else []
        )
        for portee in portees(q):
            for perimetre in perimetres:
                groupes.setdefault((portee, perimetre), []).append(valeurs)
    lignes = []
    for (portee, perimetre), liste in sorted(groupes.items()):
        for nom, k in METRIQUES:
            cle = f"{nom}@{k}"
            lignes.append(
                Ligne(
                    portee,
                    perimetre,
                    nom,
                    k,
                    math.fsum(v[cle] for v in liste) / len(liste),
                    len(liste),
                )
            )
    return lignes
