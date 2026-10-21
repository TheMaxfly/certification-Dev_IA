"""Limitation de débit, par clé : une fenêtre glissante d'une minute.

En mémoire du processus : elle vaut pour UNE instance de l'API (limite écrite au
README). Les clés sont celles du trousseau, authentifiées avant d'être comptées :
leur nombre est borné, la mémoire aussi.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable


class Limiteur:
    def __init__(
        self,
        par_minute: int,
        horloge: Callable[[], float] = time.monotonic,
        fenetre_s: float = 60.0,
    ) -> None:
        self._par_minute = par_minute
        self._horloge = horloge
        self._fenetre = fenetre_s
        self._requetes: dict[str, deque[float]] = {}
        self._verrou = threading.Lock()

    def admettre(self, nom: str) -> int | None:
        """None si la requête est admise (et comptée) ; sinon, les secondes à
        attendre avant que la plus ancienne de la fenêtre n'en sorte (1 au moins)."""
        with self._verrou:
            maintenant = self._horloge()
            file = self._requetes.setdefault(nom, deque())
            while file and file[0] <= maintenant - self._fenetre:
                file.popleft()
            if len(file) >= self._par_minute:
                return max(1, math.ceil(file[0] + self._fenetre - maintenant))
            file.append(maintenant)
            return None
