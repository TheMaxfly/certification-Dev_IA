"""Les comptes : lecture du compte courant, création, connexion et son verrou.

Toutes les requêtes sont des littéraux paramétrés : aucune valeur dans le texte d'une
requête. Les mots de passe ne passent qu'au `Hacheur`, hors de la boucle d'événements ;
ni eux ni leurs empreintes ne sortent de ce module, sauf l'empreinte vers la base.

**Anti-force brute** (E4, lot 1, décision 8) : un compte verrouillé, inactif, ou un
mauvais mot de passe rendent le MÊME refus à l'écran ; un pseudo inconnu passe par une
vérification factice (même travail, même refus). Tous les chemins font le même travail :
une lecture sans verrou de ligne, une vérification hors transaction, une écriture (vraie
ou vide) ; rien n'attend qu'un autre essai sur le même pseudo ait fini. Chaque échec sur
un compte actif et sans verrou ajoute un au compteur, en une instruction atomique ; au
seuil, le verrou est posé pour la durée configurée et le compteur repart de zéro ; un
succès le remet à zéro et lève le verrou échu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

import psycopg
from psycopg import errors
from starlette.concurrency import run_in_threadpool

from application.base import Base
from application.motdepasse import Hacheur

type Role = Literal["client", "libraire", "admin"]
#: Le motif d'un refus de connexion, borné (journal `connexion_refusee`).
type Motif = Literal["mot_de_passe", "verrou", "inactif"]
type Action = Literal[
    "compte_cree", "compte_desactive", "compte_reactive", "mot_de_passe_change"
]


@dataclass(frozen=True, slots=True)
class Compte:
    """Ce que l'application relit à chaque requête ; ni pseudo, ni empreinte."""

    id: int
    role: str
    actif: bool
    generation: int
    doit_changer_mdp: bool
    nom_affiche: str


@dataclass(frozen=True, slots=True)
class Refus:
    motif: Motif
    #: Le compte refusé, s'il existe (pour le journal : son identifiant seul).
    compte_id: int | None
    #: Vrai si ce refus vient de poser le verrou.
    verrou_pose: bool = False


class PseudoPris(Exception):
    """Le pseudo existe déjà."""


class CompteInvalide(Exception):
    """Une valeur hors des règles du compte (forme du pseudo, longueur du nom). Le
    message ne porte aucune valeur : ni pseudo ni empreinte n'en sortent."""


#: La forme d'un pseudo, ramené en minuscules (décision 7 ; contrainte de la 027).
FORME_PSEUDO = re.compile(r"[a-z0-9_-]{3,30}")


def normaliser(pseudo: str) -> str:
    """Le pseudo tel que la base le garde : sans espaces autour, en minuscules."""
    return pseudo.strip().lower()


#: Les six colonnes du compte courant, dans l'ordre des requêtes de ce module.
type LigneCompte = tuple[int, str, bool, int, bool, str]


def _compte(ligne: tuple[object, ...]) -> Compte:
    """Une ligne `id, role, actif, generation, doit_changer_mdp, nom_affiche` : les
    types sont ceux des colonnes (NOT NULL, migration 027)."""
    ident, role, actif, generation, doit, nom = cast(LigneCompte, ligne[:6])
    return Compte(ident, role, actif, generation, doit, nom)


async def lire(base: Base, ident: int) -> Compte | None:
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT id, role, actif, generation, doit_changer_mdp, nom_affiche"
            " FROM application.comptes WHERE id = %s",
            (ident,),
        )
        ligne = await curseur.fetchone()
    return None if ligne is None else _compte(ligne)


async def journaliser(
    cx: psycopg.AsyncConnection,
    par_compte_id: int | None,
    action: Action,
    compte_vise: int,
) -> None:
    """Une ligne du journal des actions, sur un compte : des identifiants seuls."""
    await cx.execute(
        "INSERT INTO application.journal_actions"
        " (par_compte_id, action, objet_type, objet_id) VALUES (%s, %s, 'compte', %s)",
        (par_compte_id, action, compte_vise),
    )


async def creer(
    base: Base,
    hacheur: Hacheur,
    *,
    pseudo: str,
    nom_affiche: str,
    role: Role,
    mot_de_passe: str,
    doit_changer_mdp: bool,
    par_compte_id: int | Literal["soi"] | None,
) -> Compte:
    """Un compte neuf et sa ligne de journal (`compte_cree`), en une transaction.

    `par_compte_id` : l'auteur ; `"soi"` pour une inscription (le compte s'est créé
    lui-même) ; None pour le premier administrateur, créé par une commande. Toute
    violation d'une règle de la base devient une erreur nommée, sans valeur : le message
    de PostgreSQL porterait la ligne refusée, pseudo et empreinte compris."""
    pseudo = normaliser(pseudo)
    if not FORME_PSEUDO.fullmatch(pseudo) or not 1 <= len(nom_affiche) <= 60:
        raise CompteInvalide
    empreinte = await run_in_threadpool(hacheur.hacher, mot_de_passe)
    try:
        async with base.transaction() as cx:
            curseur = await cx.execute(
                "INSERT INTO application.comptes"
                " (pseudo, nom_affiche, role, hachage, doit_changer_mdp)"
                " VALUES (%s, %s, %s, %s, %s)"
                " RETURNING id, role, actif, generation, doit_changer_mdp, nom_affiche",
                (pseudo, nom_affiche, role, empreinte, doit_changer_mdp),
            )
            # `RETURNING` d'un `INSERT` réussi : une ligne, toujours.
            compte = _compte(cast(tuple[object, ...], await curseur.fetchone()))
            auteur = compte.id if par_compte_id == "soi" else par_compte_id
            await journaliser(cx, auteur, "compte_cree", compte.id)
    except errors.UniqueViolation:
        raise PseudoPris from None
    except errors.IntegrityError:
        raise CompteInvalide from None
    return compte


async def authentifier(
    base: Base,
    hacheur: Hacheur,
    *,
    pseudo: str,
    mot_de_passe: str,
    seuil: int,
    duree_verrou_s: int,
) -> Compte | Refus:
    """La connexion : le compte, ou un refus au motif borné (même message à l'écran).

    Même chemin pour tout pseudo, connu ou non : une lecture courte, sans verrou de
    ligne ; la vérification, HORS de toute transaction (rien n'est tenu pendant le
    calcul Argon2id) ; puis une seconde transaction, toujours — l'écriture atomique du
    compteur et du verrou, ou une écriture vide. Ces écritures n'attendent pas la
    synchronisation du disque (`synchronous_commit = off`) : un essai perdu à un arrêt
    brutal du serveur ne coûte qu'un échec au compteur."""
    pseudo = normaliser(pseudo)
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT id, role, actif, generation, doit_changer_mdp, nom_affiche,"
            " hachage, verrou_jusqu_a FROM application.comptes WHERE pseudo = %s",
            (pseudo,),
        )
        ligne = await curseur.fetchone()
    if ligne is None:
        await run_in_threadpool(hacheur.verifier_factice, mot_de_passe)
        await _ecriture_vide(base)
        return Refus("mot_de_passe", None)
    compte = _compte(ligne)
    empreinte, verrou = cast(tuple[str, datetime | None], ligne[6:8])
    bon = await run_in_threadpool(hacheur.verifier, empreinte, mot_de_passe)
    if verrou is not None and verrou > datetime.now(UTC):
        await _ecriture_vide(base)
        return Refus("verrou", compte.id)
    if not compte.actif:
        await _ecriture_vide(base)
        return Refus("inactif", compte.id)
    if not bon:
        return await _compter_un_echec(base, compte.id, seuil, duree_verrou_s)
    nouvelle: str | None = None
    if hacheur.doit_rehacher(empreinte):
        nouvelle = await run_in_threadpool(hacheur.hacher, mot_de_passe)
    async with base.transaction() as cx:
        await cx.execute("SET LOCAL synchronous_commit = off")
        curseur = await cx.execute(
            "UPDATE application.comptes SET echecs_connexion = 0,"
            " verrou_jusqu_a = NULL, hachage = coalesce(%s, hachage)"
            " WHERE id = %s AND actif"
            " AND (verrou_jusqu_a IS NULL OR verrou_jusqu_a <= now())"
            " RETURNING id",
            (nouvelle, compte.id),
        )
        accepte = await curseur.fetchone()
    # Désactivé ou verrouillé entre la lecture et l'écriture : refusé comme tel.
    return compte if accepte is not None else Refus("verrou", compte.id)


async def _compter_un_echec(
    base: Base, ident: int, seuil: int, duree_verrou_s: int
) -> Refus:
    """Un échec de plus, en une instruction : au seuil, le verrou et le compteur à zéro.
    Le compteur lu est celui de la base, pas celui de la lecture : deux échecs
    simultanés comptent deux."""
    async with base.transaction() as cx:
        await cx.execute("SET LOCAL synchronous_commit = off")
        curseur = await cx.execute(
            "UPDATE application.comptes SET"
            " verrou_jusqu_a = CASE WHEN echecs_connexion + 1 >= %(seuil)s"
            "   THEN now() + %(duree)s * interval '1 second' ELSE verrou_jusqu_a END,"
            " echecs_connexion = CASE WHEN echecs_connexion + 1 >= %(seuil)s"
            "   THEN 0 ELSE echecs_connexion + 1 END"
            " WHERE id = %(id)s"
            " AND (verrou_jusqu_a IS NULL OR verrou_jusqu_a <= now())"
            " RETURNING echecs_connexion = 0",
            {"seuil": seuil, "duree": duree_verrou_s, "id": ident},
        )
        ligne = await curseur.fetchone()
    if ligne is None:  # verrouillé entre-temps par un essai simultané
        return Refus("verrou", ident)
    return Refus("mot_de_passe", ident, verrou_pose=bool(ligne[0]))


async def _ecriture_vide(base: Base) -> None:
    """L'aller-retour d'une écriture, sans rien écrire : tous les chemins de la
    connexion font le même nombre de transactions."""
    async with base.transaction() as cx:
        await cx.execute("SET LOCAL synchronous_commit = off")
        await cx.execute(
            "UPDATE application.comptes SET echecs_connexion = echecs_connexion"
            " WHERE false"
        )
