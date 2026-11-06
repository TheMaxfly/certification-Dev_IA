"""L'exporteur de qualité : la dernière mesure de l'encodage en service.

Lue dans `bench.eval_mesures`, seule table qui porte le chiffre : `eval_runs` n'a pas de
mesure, et la plupart des runs de l'encodage en service n'en ont aucune. Session en
lecture seule, membre de `verif_ro` ; refus sinon. Rien n'est écrit.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg

from monitorage import journal
from monitorage.metriques import Mesure, Metriques

SQL_SESSION = (
    "SELECT current_setting('default_transaction_read_only'),"
    " pg_has_role(current_user, 'verif_ro', 'MEMBER')"
)
SQL_MESURE = (
    "SELECT m.valeur, EXTRACT(EPOCH FROM m.mesure_le)::float8"
    " FROM bench.eval_mesures m"
    " JOIN bench.eval_runs r USING (run_id)"
    " JOIN bench.v_encodage_en_service s"
    " ON r.encodage_id = s.encodage_id AND r.corpus_id = s.corpus_id"
    " WHERE m.metrique = 'hit_rate' AND m.k = 10 AND m.portee = 'global'"
    " AND m.jeu_version = 'v2' AND m.perimetre = 'toutes'"
    " ORDER BY m.mesure_le DESC LIMIT 1"
)
OPTIONS = "-c default_transaction_read_only=on"


def connecter_postgres(dsn: str) -> Any:
    return psycopg.connect(dsn, options=OPTIONS, connect_timeout=5)


class SessionRefusee(Exception):
    """La session n'est ni en lecture seule, ni membre de `verif_ro`."""


class LecteurMesure:
    def __init__(
        self,
        dsn: str,
        metriques: Metriques,
        connecter: Callable[[str], Any] = connecter_postgres,
    ) -> None:
        self._dsn = dsn
        self._metriques = metriques
        self._connecter = connecter

    def lire(self) -> Mesure | None:
        """La dernière mesure, publiée ; `None` sans mesure ou en échec."""
        try:
            with self._connecter(self._dsn) as cx:
                lecture_seule, membre = cx.execute(SQL_SESSION).fetchone()
                if lecture_seule != "on" or membre is not True:
                    raise SessionRefusee
                ligne = cx.execute(SQL_MESURE).fetchone()
        except (psycopg.Error, SessionRefusee) as exc:
            self._metriques.erreurs.labels(cause="autre").inc()
            journal.evenement("exporteur_echec", detail=type(exc).__name__)
            return None
        if ligne is None:
            journal.evenement("exporteur_sans_mesure")
            return None
        mesure = Mesure(hit_rate_10=float(ligne[0]), horodatage=float(ligne[1]))
        self._metriques.publier_mesure(mesure)
        return mesure
