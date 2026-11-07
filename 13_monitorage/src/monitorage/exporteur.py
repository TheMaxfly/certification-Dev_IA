"""L'exporteur de qualité : la mesure qui a fait promouvoir l'encodage en service.

Lue dans `bench.eval_mesures`, seule table qui porte le chiffre. **Quelle mesure** :
celle du run cité par la dernière promotion (`decision = 'promu'`) de l'encodage en
service — pas la dernière mesure de cet encodage, qu'un run d'une autre configuration
(un rejeu, une autre mesure) changerait sans que rien n'ait été promu. Une promotion
sans run (celle de l'encodage 2, écrite par la migration 021 le 2026-10-09) lit la
référence connue de son encodage (`REFERENCES`). Sans promotion, ou sans mesure : rien
n'est publié, et une valeur publiée avant est retirée ; une base injoignable laisse la
dernière valeur. Session en lecture seule, membre de `verif_ro` ; refus sinon. Rien
n'est écrit.
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
#: La dernière promotion de l'encodage en service : son run (NULL possible) et
#: l'encodage.
SQL_PROMOTION = (
    "SELECT p.run_id::text, s.encodage_id"
    " FROM bench.promotions p"
    " JOIN bench.v_encodage_en_service s ON p.encodage_id = s.encodage_id"
    " WHERE p.decision = 'promu'"
    " ORDER BY p.promotion_id DESC LIMIT 1"
)
#: La mesure d'un run : taux de réussite à 10, global, jeu v2, toutes les questions.
SQL_MESURE = (
    "SELECT m.valeur, EXTRACT(EPOCH FROM m.mesure_le)::float8"
    " FROM bench.eval_mesures m"
    " WHERE m.run_id = %s::uuid"
    " AND m.metrique = 'hit_rate' AND m.k = 10 AND m.portee = 'global'"
    " AND m.jeu_version = 'v2' AND m.perimetre = 'toutes'"
    " ORDER BY m.mesure_le DESC LIMIT 1"
)
#: Les promotions sans run, et le run de référence connu de leur encodage :
#: l'encodage 2, promu sans run par la migration 021, est celui de la mesure 8 sur le
#: corpus v1 (`28aa0e60`, la référence que rejoue la vérification d'un retour arrière).
REFERENCES = {2: "28aa0e60-e801-4b07-8bf4-8f5c7c2ce78a"}
OPTIONS = "-c default_transaction_read_only=on"


def connecter_postgres(dsn: str) -> Any:
    return psycopg.connect(dsn, options=OPTIONS, connect_timeout=5)


class SessionRefusee(Exception):
    """La session n'est ni en lecture seule, ni membre de `verif_ro`."""


def run_cite(promotion: tuple[Any, ...] | None) -> str | None:
    """Le run de la dernière promotion ; pour une promotion sans run, la référence
    connue de son encodage ; `None` sans promotion ou sans référence."""
    if promotion is None:
        return None
    run_id, encodage_id = promotion
    if run_id is not None:
        return str(run_id)
    return REFERENCES.get(int(encodage_id))


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
        """La mesure du run cité par la dernière promotion, publiée ; `None` sans
        mesure ou en échec."""
        try:
            with self._connecter(self._dsn) as cx:
                lecture_seule, membre = cx.execute(SQL_SESSION).fetchone()
                if lecture_seule != "on" or membre is not True:
                    raise SessionRefusee
                run = run_cite(cx.execute(SQL_PROMOTION).fetchone())
                ligne = (
                    None if run is None else cx.execute(SQL_MESURE, (run,)).fetchone()
                )
        except (psycopg.Error, SessionRefusee) as exc:
            self._metriques.erreurs.labels(cause="autre").inc()
            journal.evenement("exporteur_echec", detail=type(exc).__name__)
            return None
        if ligne is None:
            self._metriques.retirer_mesure()
            journal.evenement("exporteur_sans_mesure")
            return None
        mesure = Mesure(hit_rate_10=float(ligne[0]), horodatage=float(ligne[1]))
        self._metriques.publier_mesure(mesure)
        return mesure
