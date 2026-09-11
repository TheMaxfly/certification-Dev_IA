"""Limiteur de cadence — un seul mecanisme, deux cadences.

Requetes serielles, jamais paralleles. L'horloge et la mise en attente sont
injectables : la cadence se verifie en test sans reseau et sans attente reelle.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class Limiteur:
    """Impose un intervalle minimal entre deux requetes.

    `signaler_attente` prend le pas sur la cadence nominale : c'est par la que
    passent `Retry-After` et un depassement de `maxlag`. En cas de depassement,
    on attend et on reessaie — jamais on ne force.
    """

    def __init__(
        self,
        intervalle_minimal_s: float,
        *,
        horloge: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        if intervalle_minimal_s < 0:
            raise ValueError("intervalle_minimal_s doit etre positif ou nul")
        self.intervalle_minimal_s = intervalle_minimal_s
        self._horloge = horloge
        self._dormir = dormir
        self._prochain_creneau: float | None = None
        self.attente_cumulee_s = 0.0
        self.requetes_autorisees = 0

    def attendre(self) -> float:
        """Bloque jusqu'au prochain creneau autorise. Rend l'attente consentie."""
        maintenant = self._horloge()
        if self._prochain_creneau is None:
            attente = 0.0
        else:
            attente = max(0.0, self._prochain_creneau - maintenant)
        if attente > 0:
            self._dormir(attente)
        self._prochain_creneau = self._horloge() + self.intervalle_minimal_s
        self.attente_cumulee_s += attente
        self.requetes_autorisees += 1
        return attente

    def signaler_attente(self, secondes: float) -> None:
        """Repousse le prochain creneau — `Retry-After`, `maxlag`, 429."""
        if secondes <= 0:
            return
        base = self._horloge()
        cible = base + secondes
        if self._prochain_creneau is None or cible > self._prochain_creneau:
            self._prochain_creneau = cible
