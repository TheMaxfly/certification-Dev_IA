"""Les métriques de la sonde et de l'exporteur, dans un registre propre au module.

Les jauges de la sonde et de la dernière mesure ne sont publiées qu'une fois qu'une
valeur existe : une jauge sans valeur vaudrait 0, et une règle « sous 0,999 » se
déclencherait au démarrage. Les étiquettes sont fermées ; aucune ne porte un texte ni
une adresse.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

#: Les causes d'échec d'un passage de la sonde.
CAUSES = ("service_injoignable", "echantillon_absent", "autre")


@dataclass(frozen=True)
class EtatSonde:
    """Le dernier passage réussi de la sonde."""

    encodage: int
    cosinus_min: float
    cosinus_mediane: float
    tranches_sous_seuil: int
    horodatage: float
    duree_s: float


@dataclass(frozen=True)
class Mesure:
    """La mesure qui a fait promouvoir l'encodage en service, lue en base."""

    hit_rate_10: float
    horodatage: float


class _Collecteur(Collector):
    """Ce qui n'existe qu'une fois mesuré : rien tant qu'aucune valeur n'est connue."""

    def __init__(self) -> None:
        self.sonde: EtatSonde | None = None
        self.mesure: Mesure | None = None

    def collect(self) -> Iterable[Metric]:
        if self.sonde is not None:
            yield from self._familles_sonde(self.sonde)
        if self.mesure is not None:
            yield GaugeMetricFamily(
                "modele_derniere_mesure_hit_rate_10",
                "Taux de réussite à 10, mesure du run cité par la dernière promotion"
                " de l'encodage en service.",
                value=self.mesure.hit_rate_10,
            )
            yield GaugeMetricFamily(
                "modele_derniere_mesure_timestamp",
                "Date de cette mesure, en secondes.",
                value=self.mesure.horodatage,
            )

    @staticmethod
    def _familles_sonde(etat: EtatSonde) -> Iterable[Metric]:
        yield GaugeMetricFamily(
            "modele_sonde_equivalence_cosinus_min",
            "Cosinus minimal du dernier passage de la sonde d'équivalence.",
            value=etat.cosinus_min,
        )
        yield GaugeMetricFamily(
            "modele_sonde_equivalence_cosinus_mediane",
            "Cosinus médian du dernier passage de la sonde d'équivalence.",
            value=etat.cosinus_mediane,
        )
        yield GaugeMetricFamily(
            "modele_sonde_tranches_sous_seuil",
            "Tranches de longueur dont le cosinus minimal est sous le seuil.",
            value=etat.tranches_sous_seuil,
        )
        yield GaugeMetricFamily(
            "modele_sonde_derniere_execution_timestamp",
            "Date du dernier passage réussi de la sonde, en secondes.",
            value=etat.horodatage,
        )
        yield GaugeMetricFamily(
            "modele_sonde_duree_secondes",
            "Durée du dernier passage réussi de la sonde.",
            value=etat.duree_s,
        )
        encodage = GaugeMetricFamily(
            "modele_sonde_encodage",
            "L'encodage sondé (1).",
            labels=["encodage"],
        )
        encodage.add_metric([str(etat.encodage)], 1)
        yield encodage


class Metriques:
    def __init__(self, lecture_base: bool) -> None:
        self.registre = CollectorRegistry()
        self._collecteur = _Collecteur()
        self.registre.register(self._collecteur)
        self.erreurs = Counter(
            "modele_sonde_erreurs",
            "Passages de la sonde en échec, par cause.",
            ["cause"],
            registry=self.registre,
        )
        for cause in CAUSES:
            self.erreurs.labels(cause=cause)
        self.webhook_recus = Counter(
            "monitorage_webhook_essai_recus",
            "Alertes reçues sur le webhook d'essai local.",
            registry=self.registre,
        )
        self.base_activee = Gauge(
            "modele_exporteur_base_activee",
            "1 si l'exporteur lit la mesure en base, 0 sinon.",
            registry=self.registre,
        )
        self.base_activee.set(1 if lecture_base else 0)

    def publier_sonde(self, etat: EtatSonde) -> None:
        self._collecteur.sonde = etat

    def publier_mesure(self, mesure: Mesure) -> None:
        self._collecteur.mesure = mesure

    def retirer_mesure(self) -> None:
        """Plus de mesure à publier (promotion sans run ni référence, run sans
        mesure) : les deux jauges disparaissent, plutôt qu'un chiffre périmé."""
        self._collecteur.mesure = None

    def exposer(self) -> bytes:
        return generate_latest(self.registre)
