"""Les métriques : un registre propre au module, `/metrics` sur le bouclage seulement.

Matière de l'étape de surveillance : requêtes par route et statut, appels à l'API et
leurs erreurs par code, latence, et l'état de l'API vu d'ici : deux lectures, deux
jauges, écrites à une page ou à un `/sante`. Aucune ne se relit seule : la panne de
l'API se lit sur la cible de Prometheus, pas ici.
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
#: Les états de l'API vus d'ici : `VueApi.etat`.
ETATS_API = ("ok", "ko", "cle")
#: Les états du catalogue (l'API data du 02) vus d'ici, posés à chaque appel.
ETATS_CATALOGUE = ("ok", "indisponible", "non_configure", "cle")


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
        self.api_info_etat = Gauge(
            "application_api_info_etat",
            "L'état de l'API à la dernière lecture de /v1/info (1 sur l'état constaté)"
            " : ok, ko (elle ne répond pas), cle (clé refusée).",
            ["etat"],
            registry=self.registre,
        )
        self.base_etat = Gauge(
            "application_base_etat",
            "L'état de la base des comptes à sa dernière lecture (1 sur l'état"
            " constaté)"
            " : ok, indisponible (elle ne répond pas), non_configuree (aucun DSN).",
            ["etat"],
            registry=self.registre,
        )
        self.catalogue_appels = Counter(
            "application_catalogue_appels",
            "Appels à l'API data du catalogue (module 02), par route et issue : ok,"
            " introuvable, indisponible, cle (clé refusée), corps (hors contrat).",
            ["route_catalogue", "statut"],
            registry=self.registre,
        )
        self.catalogue_etat = Gauge(
            "application_catalogue_etat",
            "L'état du catalogue au dernier appel (1 sur l'état constaté) : ok,"
            " indisponible, non_configure (aucune clé), cle (clé refusée).",
            ["etat"],
            registry=self.registre,
        )
        self.api_sante_ok = Gauge(
            "application_api_sante_ok",
            "1 si /v1/sante a répondu 200 à sa dernière lecture (démarrage, /sante).",
            registry=self.registre,
        )

    def etat_catalogue(self, etat: str) -> None:
        """Pose l'état du catalogue : 1 sur l'état constaté, 0 sur les autres."""
        for possible in ETATS_CATALOGUE:
            self.catalogue_etat.labels(etat=possible).set(1 if possible == etat else 0)

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
