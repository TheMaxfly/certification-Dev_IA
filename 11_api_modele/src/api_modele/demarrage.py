"""Le contrôle de cohérence au démarrage : l'API ne sert que ce qui a été promu.

Dans l'ordre, et au premier écart l'API ne démarre pas (`DemarrageRefuse`, message
explicite) :

1. la base répond, sous le rôle de l'API ;
2. un encodage est en service (vue `bench.v_encodage_en_service`), terminé, sur un
   corpus clos ;
3. il est du modèle que sert l'instance configurée, et de la même dimension ;
4. le service répond, et `/info` est conforme à l'encodage (modèle, révision,
   précision, version de l'outil, préfixe de question) — `confronter` du module 09 ;
5. la borne de la question tient sous la longueur maximale annoncée par `/info`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import Error as ErreurBase
from psycopg_pool import PoolTimeout

from api_modele import base, service
from api_modele.base import EncodageEnService, Pool
from api_modele.configuration import Reglages
from api_modele.service import (
    ClientService,
    ErreurService,
    Instance,
    ServiceNonConforme,
)


class DemarrageRefuse(Exception):
    """Une condition de démarrage ne tient pas : l'API ne sert rien."""


@dataclass(frozen=True, slots=True)
class Coherence:
    """Ce qui a été vérifié au démarrage, et que l'API sert jusqu'à son arrêt."""

    encodage: EncodageEnService
    nb_fragments: int
    nb_documents: int
    info: dict[str, Any]


def verifier(
    pool: Pool, client: ClientService, instance: Instance, reglages: Reglages
) -> Coherence:
    try:
        with base.transaction(pool, reglages) as cx:
            encodage = base.lire_en_service(cx)
            if encodage is None:
                raise DemarrageRefuse(
                    "aucun encodage en service : bench.v_encodage_en_service est vide"
                )
            termine, nb_fragments = base.lire_encodage(cx, encodage.encodage_id)
            clos, nb_documents, _ = base.lire_corpus(cx, encodage.corpus_id)
    except (ErreurBase, PoolTimeout) as exc:
        raise DemarrageRefuse(f"base injoignable ou refusée : {exc}") from exc

    nom = f"encodage {encodage.encodage_id} (corpus {encodage.corpus_id})"
    if not termine or nb_fragments is None:
        raise DemarrageRefuse(f"{nom} : non terminé")
    if not clos or nb_documents is None:
        raise DemarrageRefuse(f"{nom} : le corpus n'est pas clos")
    if encodage.modele != instance.model_id:
        raise DemarrageRefuse(
            f"{nom} : modèle {encodage.modele}, l'instance « {instance.nom} » sert"
            f" {instance.model_id}"
        )
    if encodage.dimension != instance.dimension:
        raise DemarrageRefuse(
            f"{nom} : dimension {encodage.dimension}, l'instance en rend"
            f" {instance.dimension}"
        )

    try:
        info = service.confronter_au_registre(client, encodage)
    except ServiceNonConforme as exc:
        raise DemarrageRefuse(
            f"service d'embedding non conforme à l'{nom} : {exc}"
        ) from exc
    except ErreurService as exc:
        raise DemarrageRefuse(f"service d'embedding injoignable : {exc}") from exc

    limite = info.get("max_input_length")
    pire = service.jetons_au_plus(
        instance.prefixe("requete"), reglages.longueur_question_max
    )
    if not isinstance(limite, int) or pire > limite:
        raise DemarrageRefuse(
            f"une question de {reglages.longueur_question_max} caractères peut coûter"
            f" {pire} jetons ; le service en admet {limite}"
        )
    return Coherence(
        encodage=encodage,
        nb_fragments=nb_fragments,
        nb_documents=nb_documents,
        info=info,
    )
