"""L'état de l'API, posé au démarrage, lu par les routes jusqu'à l'arrêt."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from api_modele.base import Pool
from api_modele.classement import Classeur
from api_modele.configuration import Reglages
from api_modele.debit import Limiteur
from api_modele.demarrage import Coherence
from api_modele.erreurs import ErreurApi
from api_modele.fournisseurs import Fournisseurs
from api_modele.metriques import Metriques
from api_modele.pseudonymes import Pseudonymes
from api_modele.service import ClientService


@dataclass(frozen=True, slots=True)
class EtatApi:
    reglages: Reglages
    pool: Pool
    client: ClientService
    coherence: Coherence
    classeur: Classeur
    limiteur: Limiteur
    metriques: Metriques
    fournisseurs: Fournisseurs
    pseudonymes: Pseudonymes


def etat_de(request: Request) -> EtatApi:
    etat: object = getattr(request.app.state, "etat", None)
    if not isinstance(etat, EtatApi):
        raise ErreurApi(503, "api_non_prete", "L'API n'a pas fini de démarrer.")
    return etat
