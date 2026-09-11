"""Tirage stratifie pour la reconnaissance.

Motif : le canari du module 04 a montre qu'un echantillon concentre en tete
biaise les projections dans le sens optimiste. La stratification importe
d'autant plus ici que le parcours reel sera ordonne par popularite — sans elle,
la projection decrirait le meilleur cas.

Le tirage est deterministe a graine fixe : deux executions rendent le meme
echantillon, et la mesure est donc rejouable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .perimetre import Serie

#: Graine fixe. La changer change l'echantillon, donc la mesure : ne le faire
#: qu'en le disant au rapport.
GRAINE = 20260911

#: Part du perimetre formant chaque strate. Le premier decile porte 52,9 % des
#: articles francais du catalogue : c'est la tranche ou la source est la plus
#: dense, et la queue en est le contrepoint le plus eloigne.
PART_STRATE = 0.10


@dataclass(frozen=True)
class Echantillon:
    tete: list[Serie]
    queue: list[Serie]
    taille_strate_tete: int
    taille_strate_queue: int
    graine: int
    part_strate: float

    @property
    def tout(self) -> list[Serie]:
        return [*self.tete, *self.queue]


def tirer(
    series: list[Serie],
    *,
    par_strate: int = 20,
    graine: int = GRAINE,
    part_strate: float = PART_STRATE,
) -> Echantillon:
    """Tire `par_strate` series dans le premier et le dernier decile.

    `series` est suppose deja ordonne par popularite decroissante — c'est ce
    que rend `perimetre.deriver`. Le tri est reapplique par securite : un
    echantillon tire sur un ordre incertain ne serait pas rejouable.
    """
    ordonnees = sorted(series, key=lambda s: (s.rang_popularite, s.series_id))
    if len(ordonnees) < 2 * par_strate:
        raise ValueError(
            f"perimetre trop petit ({len(ordonnees)}) pour deux strates de {par_strate}"
        )
    taille = max(par_strate, int(len(ordonnees) * part_strate))
    strate_tete = ordonnees[:taille]
    strate_queue = ordonnees[-taille:]

    alea = random.Random(graine)  # noqa: S311 — tirage reproductible, pas crypto
    return Echantillon(
        tete=sorted(
            alea.sample(strate_tete, par_strate), key=lambda s: s.rang_popularite
        ),
        queue=sorted(
            alea.sample(strate_queue, par_strate), key=lambda s: s.rang_popularite
        ),
        taille_strate_tete=len(strate_tete),
        taille_strate_queue=len(strate_queue),
        graine=graine,
        part_strate=part_strate,
    )
