"""Les réglages de l'application qu'un administrateur change (E4, lot 5) : la table
`application.reglages` (migration 027 : `cle`, `valeur`, `modifie_par`, `modifie_le`).

- **Une liste fermée de clés** (`CLES`) : `profil_actif` seul dans ce lot ; toute autre
  clé est une erreur de programmation (`ValueError`), jamais une clé venue d'un
  formulaire.
- **Un réglage se remplace, ne s'efface pas** : `app_rw` a `SELECT, INSERT, UPDATE` sur
  la table, aucun `DELETE`.
- **Chaque changement est journalisé dans la même transaction** (`journal_actions`,
  `profil_actif_change`, `objet_type = 'reglage'`, `details = {"profil": <nom>}` :
  un nom de profil, ni texte libre ni secret). La même valeur qu'avant n'écrit rien : ni
  réglage, ni journal (`inchange`).

Le profil actif (D12) est lu **à chaque question**, sans cache au-delà de la requête ;
il n'est appliqué que s'il est proposé — disponible et non exclu — dans le dernier
`/v1/info` (`presentation.profil_par_defaut`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from psycopg.types.json import Jsonb

from application.base import Base

#: Les clés que l'application écrit ; la 027 n'en contraint que la forme.
CLES = ("profil_actif",)
#: La clé → l'action de journal qui trace son changement (027, `journal_action_connue`).
ACTIONS = {"profil_actif": "profil_actif_change"}


@dataclass(frozen=True, slots=True)
class Valeur:
    """Un réglage : sa valeur, qui l'a posée (son nom affiché ; None si le compte a été
    supprimé depuis) et quand."""

    valeur: str
    modifie_par: int | None
    nom_affiche: str | None
    modifie_le: datetime


def _cle_connue(cle: str) -> None:
    if cle not in CLES:
        raise ValueError(f"clé de réglage inconnue : {cle!r}")


async def lire(base: Base, cle: str) -> Valeur | None:
    """Le réglage et son auteur ; None s'il n'a jamais été posé. `BaseIndisponible`
    remonte : l'appelant décide (une question part avec le défaut)."""
    _cle_connue(cle)
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT r.valeur, r.modifie_par, c.nom_affiche, r.modifie_le"
            " FROM application.reglages r"
            " LEFT JOIN application.comptes c ON c.id = r.modifie_par"
            " WHERE r.cle = %s",
            (cle,),
        )
        ligne = await curseur.fetchone()
    if ligne is None:
        return None
    valeur, par, nom, le = cast(tuple[str, int | None, str | None, datetime], ligne)
    return Valeur(valeur, par, nom, le)


async def ecrire(
    base: Base, cle: str, valeur: str, par_compte_id: int
) -> Literal["fait", "inchange"]:
    """Pose ou remplace le réglage, et sa ligne de journal, en une transaction ; la
    même valeur qu'avant : rien n'est écrit."""
    _cle_connue(cle)
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "INSERT INTO application.reglages (cle, valeur, modifie_par)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur,"
            " modifie_par = EXCLUDED.modifie_par, modifie_le = now()"
            " WHERE reglages.valeur IS DISTINCT FROM EXCLUDED.valeur"
            " RETURNING cle",
            (cle, valeur, par_compte_id),
        )
        if await curseur.fetchone() is None:
            return "inchange"
        await cx.execute(
            "INSERT INTO application.journal_actions"
            " (par_compte_id, action, objet_type, objet_id, details)"
            " VALUES (%s, %s, 'reglage', NULL, %s)",
            (par_compte_id, ACTIONS[cle], Jsonb({"profil": valeur})),
        )
    return "fait"
