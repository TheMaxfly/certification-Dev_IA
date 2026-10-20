"""Les métriques Prometheus de l'API (`/metrics`), dans un registre qui lui est propre.

Requêtes par route, méthode et statut ; latence par route ; erreurs par code ; débit
refusé par nom de clé (cardinalité bornée par le trousseau) ; l'encodage en service.
Les routes inconnues comptent sous une seule étiquette : `inconnue`. Les métriques
du modèle lui-même viendront à l'étape 5.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from api_modele.configuration import Reseau

#: Bornes de l'histogramme de latence, en secondes.
SEUILS_S = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class Metriques:
    def __init__(self) -> None:
        self.registre = CollectorRegistry(auto_describe=True)
        self.requetes = Counter(
            "api_modele_requetes",
            "Requêtes servies, par route, méthode et statut.",
            ["route", "methode", "statut"],
            registry=self.registre,
        )
        self.duree = Histogram(
            "api_modele_duree_requete_secondes",
            "Durée d'une requête, mesurée par l'API, par route.",
            ["route"],
            buckets=SEUILS_S,
            registry=self.registre,
        )
        self.erreurs = Counter(
            "api_modele_erreurs",
            "Réponses d'erreur, par code stable.",
            ["code"],
            registry=self.registre,
        )
        self.debit_refuse = Counter(
            "api_modele_debit_refuse",
            "Requêtes refusées pour débit dépassé, par nom de clé.",
            ["cle"],
            registry=self.registre,
        )
        self.encodage = Gauge(
            "api_modele_encodage_en_service",
            "L'encodage servi (1), par corpus et encodage.",
            ["corpus_id", "encodage_id"],
            registry=self.registre,
        )

    def exposer(self) -> tuple[bytes, str]:
        return generate_latest(self.registre), CONTENT_TYPE_LATEST


def autorisee(adresse: str | None, reseaux: Iterable[Reseau]) -> bool:
    """L'adresse du client est-elle dans l'un des réseaux admis à lire `/metrics` ?"""
    if adresse is None:
        return False
    try:
        ip = ipaddress.ip_address(adresse)
    except ValueError:
        return False
    return any(ip in reseau for reseau in reseaux)
