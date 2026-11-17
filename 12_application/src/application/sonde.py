"""La sonde des dépendances (E5, lot 1, spec 04) : l'application relit elle-même, à
intervalle régulier, l'état de ce dont elle dépend — qu'il y ait ou non un visiteur.

Le défaut qu'elle corrige, mesuré en staging le 13 novembre 2026 : l'état d'une
dépendance n'était relu qu'au passage d'une requête. Serveur PostgreSQL arrêté, aucune
jauge n'a bougé pendant 4 min 08 s, aucune alerte n'est partie ; revenu, les jauges sont
restées à `indisponible` 4 min 15 s. Sans visiteur, le monitorage ne pouvait rien dire ;
après le retour, il disait faux.

- **Une passe** joue toutes les lectures **en même temps**, chacune sous son délai
  (celui de son client) ; une lecture qui échoue n'empêche pas les autres. Chaque
  lecture pose la jauge qu'elle pose déjà au passage d'une requête : aucune jauge ne
  change de nom ni de sens.
- **Deux passes ne se chevauchent jamais** : une seule tâche les joue l'une après
  l'autre, et un verrou garde `passe()` de tout autre appelant.
- **La période** vient de la configuration (`[sonde]`) ; la première passe vient une
  période après le lancement, la suivante une période après le début de la précédente
  (aussitôt si une passe a dépassé la période).
- **L'heure de la dernière passe terminée** est une jauge, posée au lancement : l'alerte
  J du module 13 part quand elle vieillit de plus de quatre périodes.
- **Le journal ne s'emballe pas** : un événement `dependance_etat` par changement d'état
  d'une dépendance (son nom, l'état d'avant, l'état constaté), jamais un par passe. Une
  lecture qui lève est l'état `erreur`, journalisé de même, une fois.
- **Arrêtée avec l'application** : `arreter()` annule la tâche et l'attend.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress

from application import journal
from application.metriques import Metriques

#: Une lecture : elle pose sa jauge et rend l'état constaté (un mot d'une liste fermée).
type Lecture = Callable[[], Awaitable[str]]
#: L'état de départ d'une dépendance, avant sa première lecture par la sonde.
ETAT_INCONNU = "inconnu"
#: L'état d'une lecture qui a levé une exception : la jauge n'est pas touchée.
ETAT_ERREUR = "erreur"


class SondeDependances:
    def __init__(
        self,
        lectures: Mapping[str, Lecture],
        periode_s: float,
        metriques: Metriques,
        *,
        horloge: Callable[[], float] = time.time,
        monotone: Callable[[], float] = time.monotonic,
        attendre: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._lectures = dict(lectures)
        self.periode_s = periode_s
        self._metriques = metriques
        self._horloge = horloge
        self._monotone = monotone
        self._attendre = attendre
        self._verrou = asyncio.Lock()
        #: Le dernier état constaté de chaque dépendance (l'état du journal).
        self.etats: dict[str, str] = dict.fromkeys(self._lectures, ETAT_INCONNU)
        self.passes = 0
        self.tache: asyncio.Task[None] | None = None

    @property
    def dependances(self) -> tuple[str, ...]:
        return tuple(self._lectures)

    async def passe(self) -> None:
        """Une passe : toutes les lectures en même temps ; chaque état changé, au
        journal ; l'heure de la passe terminée, à sa jauge."""
        async with self._verrou:
            noms = list(self._lectures)
            resultats = await asyncio.gather(
                *(self._lectures[nom]() for nom in noms), return_exceptions=True
            )
            for nom, resultat in zip(noms, resultats, strict=True):
                if isinstance(resultat, BaseException) and not isinstance(
                    resultat, Exception
                ):
                    raise resultat  # une annulation remonte : l'arrêt de la tâche
                constate = resultat if isinstance(resultat, str) else ETAT_ERREUR
                avant = self.etats[nom]
                if constate != avant:
                    self.etats[nom] = constate
                    champs: dict[str, object] = {
                        "dependance": nom,
                        "avant": avant,
                        "etat": constate,
                    }
                    if isinstance(resultat, Exception):
                        champs["erreur"] = type(resultat).__name__
                    niveau = logging.INFO if constate == "ok" else logging.WARNING
                    journal.evenement("dependance_etat", champs, niveau)
            self.passes += 1
            self._metriques.sonde_derniere_passe.set(self._horloge())

    async def tourner(self) -> None:
        """La boucle de la tâche : une passe par période ; une exception n'arrête pas
        la boucle (elle est journalisée, sans détail), une annulation si."""
        attente = self.periode_s
        while True:
            await self._attendre(attente)
            debut = self._monotone()
            try:
                await self.passe()
            except Exception as exc:  # la tâche ne s'arrête pas sur une exception
                journal.evenement(
                    "sonde_passe_en_echec",
                    {"erreur": type(exc).__name__},
                    logging.ERROR,
                )
            attente = max(0.0, self.periode_s - (self._monotone() - debut))

    def lancer(self) -> None:
        """La tâche de fond, dans la boucle courante ; l'heure de lancement posée sur
        la jauge de la dernière passe (rien n'a encore manqué)."""
        self._metriques.sonde_derniere_passe.set(self._horloge())
        self.tache = asyncio.create_task(self.tourner(), name="sonde-dependances")

    async def arreter(self) -> None:
        """Annule la tâche et l'attend ; elle reste lisible (`tache.done()`)."""
        tache = self.tache
        if tache is None or tache.done():
            return
        tache.cancel()
        with suppress(asyncio.CancelledError):
            await tache
