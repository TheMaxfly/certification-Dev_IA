"""L'accès : une clé en en-tête par consommateur, comparée en temps constant ; puis
le débit de cette clé.

Repris du module 02 (`app/security.py`), recopié faute d'être importable (paquet
`app` non installable) : en-tête `X-API-Key`, `auto_error=False` (401 et non 403
quand il manque), comparaison `secrets.compare_digest` sur octets, sans `break`
(la durée ne dépend pas du rang de la clé), un seul message que la clé manque ou
soit fausse (pas d'oracle). Le **nom** de la clé entre dans le journal, jamais sa
valeur.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from typing import Annotated

from fastapi import Request, Security
from fastapi.security import APIKeyHeader

from api_modele.configuration import Cle
from api_modele.erreurs import ErreurApi
from api_modele.etat import etat_de

ENTETE_CLE = "X-API-Key"

schema_cle = APIKeyHeader(
    name=ENTETE_CLE,
    auto_error=False,
    description="Clé d'accès du consommateur, une par application.",
)


def consommateur(cles: Iterable[Cle], presentee: str) -> str | None:
    """Le nom du consommateur dont la clé est présentée ; None sinon."""
    presente = presentee.encode("utf-8")
    trouve: str | None = None
    for cle in cles:
        if secrets.compare_digest(presente, cle.secret.encode("utf-8")):
            trouve = cle.nom
    return trouve


def exiger_cle(
    request: Request,
    presentee: Annotated[str | None, Security(schema_cle)] = None,
) -> str:
    """Dépendance des routes protégées : 401 sans clé valide, 429 au-delà du débit."""
    etat = etat_de(request)
    nom = consommateur(etat.reglages.cles, presentee) if presentee else None
    if nom is None:
        raise ErreurApi(401, "cle_invalide", "Clé d'accès absente ou invalide.")
    request.state.cle = nom
    attente = etat.limiteur.admettre(nom)
    if attente is not None:
        etat.metriques.debit_refuse.labels(cle=nom).inc()
        raise ErreurApi(
            429,
            "debit_depasse",
            "Débit dépassé pour cette clé.",
            {
                "limite_par_minute": etat.reglages.debit_par_minute,
                "reessayer_dans_s": attente,
            },
            {"Retry-After": str(attente)},
        )
    return nom
