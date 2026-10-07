"""Les configurations de recherche : chacune rend, pour une question, le score de
CHAQUE entité ; `top` en tire les k premières, avec leur score.

Classement exact, sur les 66 290 fragments : aucun vivier tronqué avant
l'agrégation par entité, aucun index approximatif. Les fragments, les vecteurs
et le jeu sont lus sur une session en lecture seule côté serveur ; rien n'est
écrit en base ici.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from mesures_recherche.entites import Entites
from mesures_recherche.jeu import Question


@dataclass
class Classement:
    scores: np.ndarray  # par entité
    ordre: np.ndarray  # indices d'entités, rang 1 en tête

    def top(self, entites: Entites, k: int) -> list[tuple[str, float]]:
        return [(entites.cle(e), float(self.scores[e])) for e in self.ordre[:k]]

    def rang(self, entite: int) -> int:
        """Rang (1-based) d'une entité dans le classement complet."""
        return int(np.flatnonzero(self.ordre == entite)[0]) + 1


def classer(entites: Entites, scores_entites: np.ndarray) -> Classement:
    return Classement(scores_entites, entites.classer(scores_entites))


# --------------------------------------------------------------------------- #
#  1, 2 — par le sens
# --------------------------------------------------------------------------- #


@dataclass
class Semantique:
    """Cosinus entre la question, encodée par le service, et chaque fragment."""

    entites: Entites
    matrice: np.ndarray  # float64, une ligne par fragment, ordre des chunk_id
    encoder: object  # question → vecteur (le client du service, rôle « requete »)
    parametres: dict = field(default_factory=dict)

    @classmethod
    def charger(cls, cx, entites: Entites, table: str, encoder, parametres=None):
        from pgvector.psycopg import register_vector

        register_vector(cx)
        lignes = cx.execute(
            f"SELECT chunk_id, embedding FROM {table} ORDER BY chunk_id"  # nosec B608
        ).fetchall()
        chunk_ids = np.array([c for c, _ in lignes], dtype=np.int64)
        if not np.array_equal(chunk_ids, entites.chunk_ids):
            raise ValueError(f"{table} : les fragments vectorisés ≠ ceux du corpus")
        matrice = np.vstack([v.to_numpy() for _, v in lignes]).astype(np.float64)
        return cls(entites, matrice, encoder, dict(parametres or {}))

    def scores(self, question: Question) -> np.ndarray:
        q = np.asarray(self.encoder(question.texte), dtype=np.float64)
        return self.entites.agreger(self.matrice @ q)


# --------------------------------------------------------------------------- #
#  3 — plein texte PostgreSQL, calculé à la volée
# --------------------------------------------------------------------------- #

SQL_PLEIN_TEXTE = """
WITH q AS (
  SELECT n,
         replace(plainto_tsquery(%(config)s::regconfig, texte)::text, '&', '|')
           ::tsquery AS requete
  FROM unnest(%(textes)s::text[]) WITH ORDINALITY AS t(texte, n)
),
c AS MATERIALIZED (
  SELECT chunk_id, to_tsvector(%(config)s::regconfig, chunk_text) AS tsv
  FROM bench.corpus_chunks
)
SELECT q.n, c.chunk_id, ts_rank_cd(c.tsv, q.requete, %(norme)s)
FROM q JOIN c ON c.tsv @@ q.requete
"""


@dataclass
class PleinTexte:
    """`ts_rank_cd` des lexèmes de la question reliés par OU (« | »).

    Une seule requête pour toutes les questions : le tsvector de chaque fragment
    est calculé une fois par exécution (CTE matérialisée), sans table ni index.
    Un fragment qui ne contient aucun lexème a le score 0.
    """

    entites: Entites
    par_question: dict[str, np.ndarray] = field(default_factory=dict)
    duree_s: float = 0.0

    @classmethod
    def calculer(cls, cx, entites: Entites, questions, configuration: str, norme: int):
        debut = time.monotonic()
        lignes = cx.execute(
            SQL_PLEIN_TEXTE,
            {
                "config": configuration,
                "norme": norme,
                "textes": [q.texte for q in questions],
            },
        ).fetchall()
        fragments = np.zeros((len(questions), len(entites.chunk_ids)))
        if lignes:
            brut = np.array(lignes, dtype=np.float64)  # n, chunk_id, score
            n = brut[:, 0].astype(np.int64) - 1  # WITH ORDINALITY commence à 1
            pos = entites.position(brut[:, 1].astype(np.int64))
            fragments[n, pos] = brut[:, 2]
        moi = cls(
            entites,
            {
                q.question_id: entites.agreger(fragments[i])
                for i, q in enumerate(questions)
            },
        )
        moi.duree_s = time.monotonic() - debut
        return moi

    def scores(self, question: Question) -> np.ndarray:
        return self.par_question[question.question_id]


# --------------------------------------------------------------------------- #
#  6 — TF-IDF (scikit-learn)
# --------------------------------------------------------------------------- #


@dataclass
class Tfidf:
    entites: Entites
    vectoriseur: object
    matrice: object  # creuse, une ligne par fragment, normes l2

    @classmethod
    def ajuster(cls, cx, entites: Entites, reglages: dict):
        from sklearn.feature_extraction.text import TfidfVectorizer

        textes = cx.execute(
            "SELECT chunk_id, chunk_text FROM bench.corpus_chunks ORDER BY chunk_id"
        ).fetchall()
        if not np.array_equal(
            np.array([c for c, _ in textes], dtype=np.int64), entites.chunk_ids
        ):
            raise ValueError("fragments lus ≠ fragments du rattachement")
        vectoriseur = TfidfVectorizer(
            lowercase=reglages["lowercase"],
            strip_accents=reglages["strip_accents"],
            analyzer=reglages["analyzer"],
            ngram_range=(reglages["ngram_min"], reglages["ngram_max"]),
            sublinear_tf=reglages["sublinear_tf"],
            min_df=reglages["min_df"],
            norm=reglages["norm"],
        )
        matrice = vectoriseur.fit_transform([t for _, t in textes])
        return cls(entites, vectoriseur, matrice)

    def scores(self, question: Question) -> np.ndarray:
        q = self.vectoriseur.transform([question.texte])
        cosinus = np.asarray((self.matrice @ q.T).todense()).ravel()
        return self.entites.agreger(cosinus.astype(np.float64))


# --------------------------------------------------------------------------- #
#  4, 5 — fusion par rang réciproque
# --------------------------------------------------------------------------- #


def fusion(classements: list[Classement], k_rrf: int, profondeur: int) -> np.ndarray:
    """Σ 1 / (k_rrf + rang) sur les `profondeur` premières entités de chaque
    liste ; une entité absente de toutes les têtes de liste a le score 0."""
    scores = np.zeros(len(classements[0].scores))
    for c in classements:
        tete = c.ordre[:profondeur]
        scores[tete] += 1.0 / (k_rrf + np.arange(1, len(tete) + 1))
    return scores
