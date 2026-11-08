"""Les bornes du conseiller (E4, lot 2b, spec 02) : quotas et plafond de débit.

- **Quota par session** (visiteurs) : les horodatages des questions de la fenêtre, en
  secondes UTC entières, dans le cookie signé de la session (`quota`) ; la liste est
  purgée à chaque lecture et **bornée** (un cookie forgé ne fait pas enfler la page ni
  la mémoire). Vérifié avant l'appel, compté après une réponse 200 seulement.
- **Quota par compte client** : un compteur par compte et par jour civil UTC,
  persisté (`application.questions_jour`, migration 029) ; relu avant l'appel,
  incrémenté après une réponse 200. Libraires et administrateur n'en ont pas.
- **Inscriptions par session** : le même mécanisme que le quota de session
  (`inscriptions`), qui survit à l'ouverture et à la fermeture d'une session.
- **Plafond global de débit** : une fenêtre glissante d'une minute, en mémoire du
  processus, sur les seuls appels partis avec la clé publique — valable avec **un**
  processus (plusieurs demanderaient un compteur partagé : README).

Limite assumée, écrite au README : un cookie effacé remet le quota de session et le
compteur d'inscriptions à zéro ; le quota d'un compte client, lui, suit le compte.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Callable, MutableMapping
from datetime import UTC, date, datetime, timedelta
from typing import cast

from application.base import Base

#: Les clés des compteurs dans le cookie de la session.
CLE_QUOTA = "quota"
CLE_INSCRIPTIONS = "inscriptions"


def horodatages(
    session: MutableMapping[str, object],
    cle: str,
    maintenant: float,
    fenetre_s: float,
    maximum: int,
) -> list[int]:
    """Les horodatages de la fenêtre, lus dans la session : entiers seulement, purgés
    des anciens, bornés aux `maximum` plus récents ; la session est réécrite."""
    brut = session.get(cle)
    elements: list[object] = cast(list[object], brut) if isinstance(brut, list) else []
    lus = [v for v in elements if isinstance(v, int) and not isinstance(v, bool)]
    gardes = sorted(v for v in lus if maintenant - fenetre_s < v <= maintenant)
    gardes = gardes[-maximum:]
    session[cle] = gardes
    return gardes


def attente(
    gardes: list[int], maintenant: float, fenetre_s: float, maximum: int
) -> int | None:
    """None si une question de plus tient dans la fenêtre ; sinon les secondes à
    attendre avant que la plus ancienne n'en sorte (1 au moins)."""
    if len(gardes) < maximum:
        return None
    return max(1, math.ceil(gardes[-maximum] + fenetre_s - maintenant))


def noter(
    session: MutableMapping[str, object],
    cle: str,
    maintenant: float,
    fenetre_s: float,
    maximum: int,
) -> None:
    """Compte une question (ou une inscription) : l'horodatage s'ajoute, borné."""
    gardes = horodatages(session, cle, maintenant, fenetre_s, maximum)
    session[cle] = [*gardes, int(maintenant)][-maximum:]


def jour_utc(maintenant: float) -> date:
    return datetime.fromtimestamp(maintenant, UTC).date()


def minuit_suivant(maintenant: float) -> datetime:
    """Le prochain minuit UTC : la fin du jour civil des compteurs."""
    jour = jour_utc(maintenant)
    return datetime(jour.year, jour.month, jour.day, tzinfo=UTC) + timedelta(days=1)


def secondes_jusqu_a(instant: datetime, maintenant: float) -> int:
    return max(1, math.ceil(instant.timestamp() - maintenant))


def heure_locale(instant: datetime) -> str:
    """« 22:15 (heure du serveur, CET) » : l'heure dite au texte, dans le fuseau du
    serveur, nommé."""
    local = instant.astimezone()
    return f"{local:%H:%M} ({local.tzname()})"


async def questions_posees(base: Base, compte_id: int, jour: date) -> int:
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT questions FROM application.questions_jour"
            " WHERE compte_id = %s AND jour = %s",
            (compte_id, jour),
        )
        ligne = await curseur.fetchone()
    return 0 if ligne is None else int(ligne[0])


async def compter_question(base: Base, compte_id: int, jour: date) -> None:
    async with base.transaction() as cx:
        await cx.execute(
            "INSERT INTO application.questions_jour (compte_id, jour, questions)"
            " VALUES (%s, %s, 1)"
            " ON CONFLICT (compte_id, jour)"
            " DO UPDATE SET questions = application.questions_jour.questions + 1",
            (compte_id, jour),
        )


class Limiteur:
    """Une fenêtre glissante d'une minute, en mémoire du processus (le modèle de
    `debit.py` du module 11, réécrit : ce module ne connaît le 11 que par HTTP)."""

    def __init__(
        self, par_minute: int, horloge: Callable[[], float], fenetre_s: float = 60.0
    ) -> None:
        self._par_minute = par_minute
        self._horloge = horloge
        self._fenetre = fenetre_s
        self._appels: deque[float] = deque()
        self._verrou = threading.Lock()

    def admettre(self) -> int | None:
        """None si l'appel est admis (et compté) ; sinon les secondes à attendre."""
        with self._verrou:
            maintenant = self._horloge()
            while self._appels and self._appels[0] <= maintenant - self._fenetre:
                self._appels.popleft()
            if len(self._appels) >= self._par_minute:
                return max(1, math.ceil(self._appels[0] + self._fenetre - maintenant))
            self._appels.append(maintenant)
            return None
