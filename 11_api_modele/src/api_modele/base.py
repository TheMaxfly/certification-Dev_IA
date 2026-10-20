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
def transaction(pool: Pool, reglages: Reglages) -> Generator[Connection[TupleRow]]:
    """Une connexion du pool, sa transaction bornée par le délai de la base."""
    with pool.connection(timeout=reglages.pool_attente_s) as cx:
        cx.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (f"{round(reglages.delai_base_s * 1000)}ms",),
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
