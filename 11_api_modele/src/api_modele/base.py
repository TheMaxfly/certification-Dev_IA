"""La base : un pool de connexions en lecture seule, et ce que l'API y lit.

Le pool reprend celui du module 02 (`app/database.py`) : `ConnectionPool` synchrone,
ouvert au démarrage, fermé à l'arrêt. Ici, chaque session est en lecture seule dès
sa première requête (en plus du rôle `modele_api`, lui-même en lecture seule), et
chaque transaction porte son délai maximal (`statement_timeout`).

L'API ne lit que le périmètre de `modele_ro` (migration 023) ; l'encodage servi est
toujours celui de la vue `bench.v_encodage_en_service`.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

from psycopg import Connection
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool

from api_modele.configuration import Reglages

type Pool = ConnectionPool[Connection[TupleRow]]

SQL_EN_SERVICE = """
SELECT encodage_id, corpus_id, modele, revision, dimension, precision_calcul,
       prefixe_document, prefixe_requete, outil, outil_version
  FROM bench.v_encodage_en_service
"""
SQL_ENCODAGE = (
    "SELECT termine_le IS NOT NULL, nb_fragments FROM bench.encodages"
    " WHERE encodage_id = %s"
)
SQL_CORPUS = (
    "SELECT clos_le IS NOT NULL, nb_documents, nb_fragments FROM bench.corpus"
    " WHERE corpus_id = %s"
)
SQL_TITRES_SERIES = (
    "SELECT series_id, series_title FROM manga.ms_series_enriched"
    " WHERE series_id = ANY(%s)"
)
SQL_TITRES_KITSU = (
    "SELECT kitsu_id, title FROM bench.corpus_docs WHERE corpus_id = %s"
    " AND source = 'kitsu_synopsis' AND kitsu_id = ANY(%s)"
)
SQL_PASSAGES = """
SELECT k.chunk_id, k.doc_key, d.source, k.chunk_text
  FROM bench.corpus_chunks k
  JOIN bench.corpus_docs d ON d.corpus_id = k.corpus_id AND d.doc_key = k.doc_key
 WHERE k.corpus_id = %s AND k.chunk_id = ANY(%s)
"""


@dataclass(frozen=True, slots=True)
class EncodageEnService:
    encodage_id: int
    corpus_id: str
    modele: str
    revision: str
    dimension: int
    precision_calcul: str
    prefixe_document: str
    prefixe_requete: str
    outil: str
    outil_version: str

    def ligne_du_registre(self) -> dict[str, object]:
        """Les champs que `confronter` (module 09) compare à ce que sert le service."""
        return {
            "modele": self.modele,
            "revision": self.revision,
            "precision_calcul": self.precision_calcul,
            "prefixe_requete": self.prefixe_requete,
            "outil_version": self.outil_version,
        }


def creer_pool(reglages: Reglages) -> Pool:
    return ConnectionPool(
        reglages.dsn,
        min_size=reglages.pool_min,
        max_size=reglages.pool_max,
        timeout=reglages.pool_attente_s,
        name="api-modele",
        open=False,
        kwargs={
            "application_name": "api-modele",
            "options": "-c default_transaction_read_only=on",
            "connect_timeout": max(1, round(reglages.pool_attente_s)),
        },
    )


@contextmanager
def transaction(
    pool: Pool, reglages: Reglages, delai_s: float | None = None
) -> Generator[Connection[TupleRow]]:
    """Une connexion du pool, sa transaction bornée par le délai de la base (ou par
    `delai_s`, pour le chargement du démarrage)."""
    delai = reglages.delai_base_s if delai_s is None else delai_s
    with pool.connection(timeout=reglages.pool_attente_s) as cx:
        cx.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (f"{round(delai * 1000)}ms",),
        )
        yield cx


def lire_en_service(cx: Connection[TupleRow]) -> EncodageEnService | None:
    ligne = cx.execute(SQL_EN_SERVICE).fetchone()
    if ligne is None:
        return None
    return EncodageEnService(
        encodage_id=int(ligne[0]),
        corpus_id=str(ligne[1]),
        modele=str(ligne[2]),
        revision=str(ligne[3]),
        dimension=int(ligne[4]),
        precision_calcul=str(ligne[5]),
        prefixe_document=str(ligne[6]),
        prefixe_requete=str(ligne[7]),
        outil=str(ligne[8]),
        outil_version=str(ligne[9]),
    )


def lire_encodage(
    cx: Connection[TupleRow], encodage_id: int
) -> tuple[bool, int | None]:
    """(terminé, nombre de fragments encodés) de l'encodage."""
    ligne = cx.execute(SQL_ENCODAGE, (encodage_id,)).fetchone()
    if ligne is None:
        return False, None
    return bool(ligne[0]), None if ligne[1] is None else int(ligne[1])


def lire_corpus(
    cx: Connection[TupleRow], corpus_id: str
) -> tuple[bool, int | None, int | None]:
    """(clos, documents, fragments) du corpus."""
    ligne = cx.execute(SQL_CORPUS, (corpus_id,)).fetchone()
    if ligne is None:
        return False, None, None
    return (
        bool(ligne[0]),
        None if ligne[1] is None else int(ligne[1]),
        None if ligne[2] is None else int(ligne[2]),
    )


def lire_titres(
    cx: Connection[TupleRow],
    corpus_id: str,
    series_ids: list[int],
    kitsu_ids: list[int],
) -> tuple[dict[int, str], dict[int, str]]:
    """Le titre MS de chaque série ; celui du synopsis de chaque œuvre Kitsu."""
    series = {
        int(i): str(t)
        for i, t in cx.execute(SQL_TITRES_SERIES, (series_ids,)).fetchall()
        if t is not None
    }
    kitsu = {
        int(i): str(t)
        for i, t in cx.execute(SQL_TITRES_KITSU, (corpus_id, kitsu_ids)).fetchall()
        if t is not None
    }
    return series, kitsu


def lire_passages(
    cx: Connection[TupleRow], corpus_id: str, chunk_ids: list[int]
) -> dict[int, tuple[str, str, str]]:
    """(doc_key, source, texte) de chaque fragment demandé, tel qu'au corpus."""
    return {
        int(c): (str(d), str(s), str(t))
        for c, d, s, t in cx.execute(SQL_PASSAGES, (corpus_id, chunk_ids)).fetchall()
    }
