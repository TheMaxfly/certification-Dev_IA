"""Les listes du client (E4, lot 4) : favori, à lire, à commander — un état par série
et par compte, le dernier geste gagne.

Toutes les requêtes sont des littéraux paramétrés. La table est celle de la migration
027 (`application.listes` : clé primaire (compte, série), cascade sur le compte) : rien
n'est ajouté au schéma. Le titre d'une série n'est jamais gardé ici : il se lit dans le
catalogue (le 02). Retirer, c'est supprimer la ligne : une liste est la donnée du
client, pas un contenu publié — rien n'est tracé au journal des actions.

**La borne** (`listes_max_par_etat`, configuration) : une liste ne dépasse pas ce
nombre de séries ; un ajout, ou un changement d'état vers une liste pleine, est refusé
(`ListePleine`). La borne est vérifiée dans la même transaction que l'écriture, la
ligne du compte tenue (`FOR NO KEY UPDATE`) : deux gestes simultanés d'un même compte
passent l'un après l'autre, et la borne tient.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from application.base import Base

type Etat = Literal["favori", "a_lire", "a_commander"]
type Issue = Literal["ajout", "changement", "inchange"]

#: Les trois états, dans l'ordre des écrans ; ceux de la contrainte `listes_etat_connu`.
ETATS: tuple[Etat, ...] = ("favori", "a_lire", "a_commander")
#: Les bornes d'un identifiant de série : le type `integer` de la colonne.
SERIE_MIN = 1
SERIE_MAX = 2_147_483_647


@dataclass(frozen=True, slots=True)
class Entree:
    """Une série d'une liste : son identifiant et sa date d'ajout ; rien d'autre."""

    series_id: int
    ajoute_le: datetime


class ListePleine(Exception):
    """La liste d'arrivée a déjà `listes_max_par_etat` séries."""


def etat_connu(valeur: str) -> Etat | None:
    return next((etat for etat in ETATS if etat == valeur), None)


def serie_valide(series_id: int) -> bool:
    return SERIE_MIN <= series_id <= SERIE_MAX


async def etat_de(base: Base, compte_id: int, series_id: int) -> Etat | None:
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT etat FROM application.listes"
            " WHERE compte_id = %s AND series_id = %s",
            (compte_id, series_id),
        )
        ligne = await curseur.fetchone()
    return None if ligne is None else cast(Etat, ligne[0])


async def etats_de(
    base: Base, compte_id: int, series_ids: Sequence[int]
) -> dict[int, Etat]:
    """Les états d'un compte pour plusieurs séries (les cartes du conseiller) : une
    requête."""
    ids = [i for i in series_ids if serie_valide(i)]
    if not ids:
        return {}
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT series_id, etat FROM application.listes"
            " WHERE compte_id = %s AND series_id = ANY(%s)",
            (compte_id, ids),
        )
        lignes = await curseur.fetchall()
    return {int(sid): cast(Etat, etat) for sid, etat in lignes}


async def compter(base: Base, compte_id: int, etat: Etat) -> int:
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT count(*) FROM application.listes"
            " WHERE compte_id = %s AND etat = %s",
            (compte_id, etat),
        )
        (n,) = cast(tuple[int], await curseur.fetchone())
    return n


async def poser(
    base: Base, compte_id: int, series_id: int, etat: Etat, *, maximum: int
) -> Issue:
    """L'état de la série pour ce compte : ajouté, changé, ou déjà posé (rien n'est
    écrit). L'état d'avant est lu dans la transaction, la ligne du compte tenue ; un
    ajout ou un changement vers une liste pleine lève `ListePleine` : rien d'écrit."""
    async with base.transaction() as cx:
        await cx.execute(
            "SELECT 1 FROM application.comptes WHERE id = %s FOR NO KEY UPDATE",
            (compte_id,),
        )
        curseur = await cx.execute(
            "SELECT etat FROM application.listes"
            " WHERE compte_id = %s AND series_id = %s",
            (compte_id, series_id),
        )
        ligne = await curseur.fetchone()
        avant = None if ligne is None else ligne[0]
        if avant == etat:
            return "inchange"
        curseur = await cx.execute(
            "SELECT count(*) FROM application.listes"
            " WHERE compte_id = %s AND etat = %s",
            (compte_id, etat),
        )
        (dans_la_liste,) = cast(tuple[int], await curseur.fetchone())
        if dans_la_liste >= maximum:
            raise ListePleine
        await cx.execute(
            "INSERT INTO application.listes (compte_id, series_id, etat)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (compte_id, series_id) DO UPDATE"
            " SET etat = EXCLUDED.etat, ajoute_le = now()"
            " WHERE listes.etat IS DISTINCT FROM EXCLUDED.etat",
            (compte_id, series_id, etat),
        )
    return "ajout" if avant is None else "changement"


async def retirer(base: Base, compte_id: int, series_id: int) -> bool:
    """La ligne supprimée ; vrai si la série était dans une liste."""
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "DELETE FROM application.listes"
            " WHERE compte_id = %s AND series_id = %s RETURNING series_id",
            (compte_id, series_id),
        )
        ligne = await curseur.fetchone()
    return ligne is not None


async def lister(
    base: Base, compte_id: int, *, maximum: int
) -> dict[Etat, list[Entree]]:
    """Les trois listes du compte, chacune par date d'ajout décroissante, bornée à
    `maximum` (la borne de l'écriture : rien n'est coupé en temps normal)."""
    listes: dict[Etat, list[Entree]] = {etat: [] for etat in ETATS}
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT etat, series_id, ajoute_le FROM ("
            "  SELECT etat, series_id, ajoute_le, row_number() OVER ("
            "    PARTITION BY etat ORDER BY ajoute_le DESC, series_id) AS rang"
            "  FROM application.listes WHERE compte_id = %s"
            ") x WHERE rang <= %s ORDER BY etat, rang",
            (compte_id, maximum),
        )
        lignes = await curseur.fetchall()
    for etat, series_id, ajoute_le in lignes:
        connu = etat_connu(str(etat))
        if connu is not None:
            listes[connu].append(Entree(int(series_id), cast(datetime, ajoute_le)))
    return listes
