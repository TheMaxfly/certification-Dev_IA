"""Le plafond de coût du jour (E4, lot 2b, spec 02) : persisté dans
`application.couts_jour` (migration 027), par jour civil UTC, profil et mode.

- **Relu avant chaque appel** : la somme des coûts du jour ; atteinte, la question ne
  part pas (503, l'écran du budget, `Retry-After` jusqu'à minuit UTC).
- **Ajouté après chaque réponse** : le `cout_estime_usd` reçu (0 s'il manque, et le
  journal le dit), sous le profil et le mode **renvoyés par l'API**, pas ceux demandés.
- **Base indisponible ou non configurée** : le plafond n'est pas vérifiable ; le
  conseiller répond quand même (décision 11 du lot 1 : sans base, il fonctionne), avec
  l'événement `plafond_non_verifiable` et l'état `inconnu`.

Lu par l'administration (E4, lot 5, A4) : `historique` et `totaux`, en lecture seule. La
table ne porte que jour, profil, mode, appels et coût : **jamais un texte de question**.

Le jour est passé par l'appelant (l'horloge de l'application, en UTC) : jamais
`current_date`, qui suit le fuseau de la session de la base.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import cast

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


# --------------------------------------------------------------------------- #
#  Les lectures de l'administration (E4, lot 5, A4) : en lecture seule, paramétrées
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Ligne:
    """Une ligne de `couts_jour` : jamais un texte de question (la table n'en a pas)."""

    jour: date
    profil: str
    mode: str
    appels: int
    cout_usd: Decimal


@dataclass(frozen=True, slots=True)
class Agregat:
    appels: int
    cout_usd: Decimal


@dataclass(frozen=True, slots=True)
class Totaux:
    """Toute la table : le total, par profil, par mode, et son premier jour (None : la
    table est vide)."""

    total_table: Decimal
    appels: int
    par_profil: dict[str, Agregat]
    par_mode: dict[str, Agregat]
    premier_jour: date | None


async def historique(base: Base, jour: date, n_jours: int) -> list[Ligne]:
    """Les `n_jours` derniers jours civils UTC, `jour` compris : jour décroissant, puis
    profil, puis mode."""
    debut = jour - timedelta(days=n_jours - 1)
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT jour, profil, mode, appels, cout_usd FROM application.couts_jour"
            " WHERE jour BETWEEN %s AND %s ORDER BY jour DESC, profil, mode",
            (debut, jour),
        )
        lignes = await curseur.fetchall()
    return [
        Ligne(*cast(tuple[date, str, str, int, Decimal], ligne)) for ligne in lignes
    ]


async def totaux(base: Base) -> Totaux:
    """Une requête d'agrégats (`GROUPING SETS`) : par profil, par mode, en tout."""
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT profil, mode, GROUPING(profil), GROUPING(mode),"
            " coalesce(sum(appels), 0), coalesce(sum(cout_usd), 0), min(jour)"
            " FROM application.couts_jour"
            " GROUP BY GROUPING SETS ((profil), (mode), ())"
        )
        lignes = await curseur.fetchall()
    total, appels, premier = Decimal(0), 0, None
    par_profil: dict[str, Agregat] = {}
    par_mode: dict[str, Agregat] = {}
    for ligne in lignes:
        profil, mode, sans_profil, sans_mode, n, cout, jour = cast(
            tuple[str | None, str | None, int, int, int, Decimal, date | None], ligne
        )
        agregat = Agregat(int(n), Decimal(cout))
        if sans_profil and sans_mode:
            total, appels, premier = agregat.cout_usd, agregat.appels, jour
        elif not sans_profil and profil is not None:
            par_profil[profil] = agregat
        elif mode is not None:
            par_mode[mode] = agregat
    return Totaux(total, appels, par_profil, par_mode, premier)
