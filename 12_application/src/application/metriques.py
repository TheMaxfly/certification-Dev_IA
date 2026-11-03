"""Les métriques : un registre propre au module, `/metrics` sur le bouclage seulement.

Matière de l'étape de surveillance : requêtes par route et statut, appels à l'API et
leurs erreurs par code, latence, disponibilité de l'API vue d'ici.
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

from application.config import Reseau

SEAUX_LATENCE = (0.05, 0.25, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 45.0, 75.0)


class Metriques:
    def __init__(self) -> None:
        self.registre = CollectorRegistry()
        self.requetes = Counter(
            "application_requetes",
            "Requêtes servies, par gabarit de route et statut.",
            ["route", "statut"],
            registry=self.registre,
        )
        self.api_appels = Counter(
            "application_api_appels",
            "Appels à l'API du modèle, par route de l'API et statut.",
            ["route_api", "statut"],
            registry=self.registre,
        )
        self.api_erreurs = Counter(
            "application_api_erreurs",
            "Erreurs rendues par l'API du modèle ou en l'appelant, par code.",
            ["code"],
            registry=self.registre,
        )
        self.latence = Histogram(
            "application_latence_secondes",
            "Durée des requêtes servies, par gabarit de route.",
            ["route"],
            buckets=SEAUX_LATENCE,
            registry=self.registre,
        )
        self.api_disponible = Gauge(
            "application_api_disponible",
            "1 si l'API du modèle répondait à la dernière lecture, 0 sinon.",
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
