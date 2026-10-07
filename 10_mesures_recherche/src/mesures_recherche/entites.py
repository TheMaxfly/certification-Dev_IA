"""Le grain de mesure : l'entité.

Une entité est la série du catalogue quand le fragment y est rattaché, sinon
l'identifiant Kitsu de son document. Le rattachement n'est PAS écrit ici : il
est celui de `evaluation/atteignabilite.py` (module 05, `SQL_RATTACHEMENT`),
chargé tel quel depuis son fichier — sans installer le module 05 et ses
dépendances. Les séries atteignables viennent de la même source.

Score d'une entité = le meilleur score de ses fragments. Toutes les entités
occupent un rang ; égalités départagées par l'identifiant croissant (series_id
ou kitsu_id, comme nombres), la série d'abord à nombre égal.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from functools import cached_property

import numpy as np

from mesures_recherche.configuration import RACINE_DEPOT

FICHIER_ATTEIGNABILITE = (
    RACINE_DEPOT
    / "05_nettoyage_agregation_bdd"
    / "src"
    / "evaluation"
    / "atteignabilite.py"
)


def charger_atteignabilite():
    """Le module du rattachement, tel quel, depuis son fichier."""
    nom = "atteignabilite_module05"
    if nom in sys.modules:
        return sys.modules[nom]
    spec = importlib.util.spec_from_file_location(nom, FICHIER_ATTEIGNABILITE)
    if spec is None or spec.loader is None:
        raise ImportError(f"rattachement introuvable : {FICHIER_ATTEIGNABILITE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[nom] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class Entites:
    chunk_ids: np.ndarray  # int64, croissants : l'ordre de tout vecteur de scores
    entite: np.ndarray  # pour chaque fragment, l'indice de son entité
    identifiant: np.ndarray  # par entité : series_id ou kitsu_id
    est_serie: np.ndarray  # par entité
    atteignables: frozenset[int]

    @cached_property
    def _groupes(self) -> tuple[np.ndarray, np.ndarray]:
        ordre = np.argsort(self.entite, kind="stable")
        debuts = np.flatnonzero(np.r_[True, np.diff(self.entite[ordre]) != 0])
        return ordre, debuts

    @cached_property
    def index_serie(self) -> dict[int, int]:
        return {
            int(i): e
            for e, (i, s) in enumerate(
                zip(self.identifiant, self.est_serie, strict=True)
            )
            if s
        }

    def __len__(self) -> int:
        return len(self.identifiant)

    def cle(self, e: int) -> str:
        return f"{'serie' if self.est_serie[e] else 'kitsu'}:{int(self.identifiant[e])}"

    def position(self, chunk_ids) -> np.ndarray:
        """Position de fragments (par chunk_id) dans l'ordre des scores."""
        ids = np.asarray(chunk_ids, dtype=np.int64)
        pos = np.searchsorted(self.chunk_ids, ids)
        dedans = pos < len(self.chunk_ids)
        if not dedans.all() or not np.array_equal(self.chunk_ids[pos], ids):
            raise ValueError("chunk_id inconnu du rattachement")
        return pos

    def agreger(self, scores_fragments: np.ndarray) -> np.ndarray:
        """Score de chaque entité = le meilleur de ses fragments."""
        if scores_fragments.shape != self.chunk_ids.shape:
            raise ValueError("un score par fragment, dans l'ordre des chunk_id")
        ordre, debuts = self._groupes
        return np.maximum.reduceat(scores_fragments[ordre], debuts)

    def classer(self, scores_entites: np.ndarray) -> np.ndarray:
        """Indices d'entités, du rang 1 au dernier : score décroissant, puis
        identifiant croissant, puis la série d'abord."""
        return np.lexsort((~self.est_serie, self.identifiant, -scores_entites))


def construire(lignes, atteignables) -> Entites:
    """À partir des lignes du rattachement (chunk_id, source, series_id, kitsu_id)."""
    lignes = sorted(lignes)
    cles: dict[tuple[bool, int], int] = {}
    entite = np.empty(len(lignes), dtype=np.int64)
    for i, (_, _, series_id, kitsu_id) in enumerate(lignes):
        if series_id is not None:
            cle = (True, int(series_id))
        elif kitsu_id is not None:
            cle = (False, int(kitsu_id))
        else:
            raise ValueError(f"fragment {lignes[i][0]} sans entité")
        entite[i] = cles.setdefault(cle, len(cles))
    est_serie = np.array([s for s, _ in cles], dtype=bool)
    identifiant = np.array([i for _, i in cles], dtype=np.int64)
    return Entites(
        chunk_ids=np.array([c for c, *_ in lignes], dtype=np.int64),
        entite=entite,
        identifiant=identifiant,
        est_serie=est_serie,
        atteignables=frozenset(atteignables),
    )


def charger(cx) -> Entites:
    module = charger_atteignabilite()
    return construire(module.rattachement(cx), module.atteignables(cx))
