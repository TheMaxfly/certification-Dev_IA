"""Le plafond de coût du jour (E4, lot 2b, spec 02) : persisté dans
`application.couts_jour` (migration 027), par jour civil UTC, profil et mode.

- **Relu avant chaque appel** : la somme des coûts du jour ; atteinte, la question ne
  part pas (503, l'écran du budget, `Retry-After` jusqu'à minuit UTC).
- **Ajouté après chaque réponse** : le `cout_estime_usd` reçu (0 s'il manque, et le
  journal le dit), sous le profil et le mode **renvoyés par l'API**, pas ceux demandés.
- **Base indisponible ou non configurée** : le plafond n'est pas vérifiable ; le
  conseiller répond quand même (décision 11 du lot 1 : sans base, il fonctionne), avec
  l'événement `plafond_non_verifiable` et l'état `inconnu`.

Le jour est passé par l'appelant (l'horloge de l'application, en UTC) : jamais
`current_date`, qui suit le fuseau de la session de la base.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from application.base import Base

#: Les modes que la table admet (027, `couts_mode_connu`).
MODES = ("strict", "enrichi")


async def cumul_du_jour(base: Base, jour: date) -> Decimal:
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT coalesce(sum(cout_usd), 0) FROM application.couts_jour"
            " WHERE jour = %s",
            (jour,),
        )
        ligne = await curseur.fetchone()
    return Decimal(0) if ligne is None else Decimal(ligne[0])


async def ajouter(
    base: Base, jour: date, profil: str, mode: str, cout_usd: float
) -> None:
    async with base.transaction() as cx:
        await cx.execute(
            "INSERT INTO application.couts_jour (jour, profil, mode, appels, cout_usd)"
            " VALUES (%s, %s, %s, 1, %s)"
            " ON CONFLICT (jour, profil, mode) DO UPDATE SET"
            " appels = application.couts_jour.appels + 1,"
            " cout_usd = application.couts_jour.cout_usd + EXCLUDED.cout_usd",
            (jour, profil, mode, Decimal(str(cout_usd))),
        )
