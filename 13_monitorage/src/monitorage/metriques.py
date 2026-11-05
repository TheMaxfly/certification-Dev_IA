"""Les métriques de la sonde et de l'exporteur, dans un registre propre au module."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Gauge, generate_latest


class Metriques:
    def __init__(self, lecture_base: bool) -> None:
        self.registre = CollectorRegistry()
        self.base_activee = Gauge(
            "modele_exporteur_base_activee",
            "1 si l'exporteur lit la dernière mesure en base, 0 sinon.",
            registry=self.registre,
        )
        self.base_activee.set(1 if lecture_base else 0)

    def exposer(self) -> bytes:
        return generate_latest(self.registre)
