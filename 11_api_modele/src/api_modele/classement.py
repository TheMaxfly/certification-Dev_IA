"""Le classement : celui du banc (module 10), réutilisé, pas réécrit.

Seul point de contact avec les modules 10 et 05 (chargés par leur chemin,
`api_modele.banc`). Pour une question, dans l'ordre, comme la mesure 8 :

1. au démarrage, le rattachement des fragments du corpus en service
   (`entites.charger`, `SQL_RATTACHEMENT` du module 05) et les vecteurs de
   l'encodage en service SEULEMENT (`recherche.Semantique.charger`, filtre
   `encodage_id`, contrôle « fragments vectorisés = fragments du corpus », float64) ;
2. à chaque question, les scores des entités par `Semantique.scores` — produit
   scalaire avec la question encodée, score d'une série = son meilleur fragment —
   sur une copie superficielle qui rend le vecteur déjà encodé (la matrice n'est pas
   copiée, la question n'est pas encodée deux fois) ;
3. l'ordre par `recherche.classer` (départage par l'identifiant, puis la série
   d'abord ; `catalogue` : les seules séries, dans l'ordre complet).

Ajout de l'API : les passages, les fragments d'une entité retenue triés par score
décroissant puis `chunk_id` croissant, sur la même matrice et le même vecteur — le
premier passage porte donc le score de sa série.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from psycopg import Connection
from psycopg.rows import TupleRow

from api_modele import banc


@dataclass(frozen=True, slots=True)
class Fragment:
    chunk_id: int
    score: float


@dataclass(frozen=True, slots=True)
class EntiteClassee:
    rang: int
    entite: str
    serie_id: int | None
    kitsu_id: int | None
    score: float
    fragments: tuple[Fragment, ...]


def _sans_encodeur(_texte: str) -> NDArray[np.float64]:
    raise RuntimeError("le classeur n'encode pas : le vecteur lui est donné")


class Classeur:
    """Le classement chargé une fois, pour le corpus et l'encodage en service."""

    def __init__(self, entites: Any, semantique: Any, recherche: Any, jeu: Any) -> None:
        self._entites = entites
        self._semantique = semantique
        self._classer = recherche.classer
        self._question = jeu.Question
        # Les fragments de chaque entité (indices dans l'ordre des chunk_id).
        ordre = np.argsort(entites.entite, kind="stable")
        debuts = np.flatnonzero(np.r_[True, np.diff(entites.entite[ordre]) != 0])
        self._positions: list[NDArray[np.intp]] = np.split(ordre, debuts[1:])

    @classmethod
    def charger(
        cls, cx: Connection[TupleRow], corpus_id: str, table: str, encodage_id: int
    ) -> Classeur:
        entites_ = banc.module("entites")
        recherche = banc.module("recherche")
        jeu = banc.module("jeu")
        entites = entites_.charger(cx, corpus_id)
        semantique = recherche.Semantique.charger(
            cx, entites, table, _sans_encodeur, encodage_id=encodage_id
        )
        return cls(entites, semantique, recherche, jeu)

    @property
    def corpus_id(self) -> str:
        return str(self._entites.corpus_id)

    @property
    def nb_fragments(self) -> int:
        return len(self._entites.chunk_ids)

    @property
    def nb_entites(self) -> int:
        return len(self._entites)

    @property
    def octets_matrice(self) -> int:
        return int(self._semantique.matrice.nbytes)

    def classer(
        self,
        vecteur: Sequence[float],
        *,
        catalogue_seul: bool,
        nombre: int,
        passages: int,
    ) -> list[EntiteClassee]:
        q = np.asarray(vecteur, dtype=np.float64)
        question = self._question(
            question_id="api", texte="", mode="", famille="", issue=""
        )

        def encoder(_texte: str) -> NDArray[np.float64]:
            return q

        semantique = dataclasses.replace(self._semantique, encoder=encoder)
        scores = semantique.scores(question)
        classement = self._classer(
            self._entites, scores, "catalogue" if catalogue_seul else "toutes"
        )
        fragments: NDArray[np.float64] = self._semantique.matrice @ q
        chunk_ids: NDArray[np.int64] = self._entites.chunk_ids
        resultat: list[EntiteClassee] = []
        for rang, e in enumerate(classement.ordre[:nombre], start=1):
            positions = self._positions[int(e)]
            ordre = np.lexsort((chunk_ids[positions], -fragments[positions]))
            retenus = positions[ordre[:passages]]
            est_serie = bool(self._entites.est_serie[e])
            identifiant = int(self._entites.identifiant[e])
            resultat.append(
                EntiteClassee(
                    rang=rang,
                    entite=str(self._entites.cle(e)),
                    serie_id=identifiant if est_serie else None,
                    kitsu_id=None if est_serie else identifiant,
                    score=float(scores[e]),
                    fragments=tuple(
                        Fragment(int(chunk_ids[p]), float(fragments[p]))
                        for p in retenus
                    ),
                )
            )
        return resultat
