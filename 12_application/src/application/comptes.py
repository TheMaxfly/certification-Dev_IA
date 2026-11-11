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
    "compte_cree",
    "compte_desactive",
    "compte_reactive",
    "mot_de_passe_change",
    "compte_supprime",
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


class CompteNonSupprimable(Exception):
    """La base refuse la suppression : le compte a des critiques signées (contrainte
    `ON DELETE RESTRICT` de la 027). Un client n'en a pas ; la base le tient."""


class DejaSupprime(Exception):
    """Le compte n'existait plus au moment de la suppression (une autre session l'a
    supprimé) : la transaction est annulée, le journal n'a rien reçu."""


class CompteInvalide(Exception):
    """Une valeur hors des règles du compte (forme du pseudo, longueur du nom). Le
    message ne porte aucune valeur : ni pseudo ni empreinte n'en sortent."""


#: La forme d'un pseudo, ramené en minuscules (décision 7 ; contrainte de la 027).
FORME_PSEUDO = re.compile(r"[a-z0-9_-]{3,30}")


def normaliser(pseudo: str) -> str:
    """Le pseudo tel que la base le garde : sans espaces autour, en minuscules."""
    return pseudo.strip().lower()


def nom_valide(nom: str, maximum: int) -> bool:
    """De 1 à `maximum` caractères, tous imprimables (ni octet nul, ni caractère de
    contrôle) : la base refuserait l'octet nul par une erreur, pas par un refus."""
    return 1 <= len(nom) <= maximum and nom.isprintable()


def affichable(saisie: str, maximum: int) -> str:
    """Une saisie rendue à son champ : ses caractères imprimables seuls, bornée."""
    return "".join(c for c in saisie if c.isprintable())[:maximum]


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
    if not (FORME_PSEUDO.fullmatch(pseudo) and nom_valide(nom_affiche, 60)):
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
    # Un pseudo hors forme (un octet nul, trop long…) n'atteint jamais la base : le même
    # chemin le cherche sous une forme qui n'existe pas, et le refuse comme un inconnu.
    cle = pseudo if FORME_PSEUDO.fullmatch(pseudo) else ""
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT id, role, actif, generation, doit_changer_mdp, nom_affiche,"
            " hachage, verrou_jusqu_a FROM application.comptes WHERE pseudo = %s",
            (cle,),
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


# --------------------------------------------------------------------------- #
#  Le mot de passe, les sessions, l'administration (E4, lot 1, spec 03)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Ligne:
    """Une ligne de la liste d'administration : ce que l'écran montre, rien de plus."""

    id: int
    pseudo: str
    nom_affiche: str
    role: str
    actif: bool
    cree_le: datetime


async def changer_mot_de_passe(
    base: Base,
    hacheur: Hacheur,
    ident: int,
    ancien: str,
    nouveau: str,
    *,
    seuil: int,
    duree_verrou_s: int,
) -> Compte | Refus:
    """L'ancien vérifié, le nouveau haché ; la génération + 1 (les autres sessions du
    compte tombent), le mot de passe à changer levé, une ligne de journal. Un mauvais
    ancien compte un échec, comme à la connexion — même compteur, même verrou : une
    session laissée ouverte ne permet pas de le deviner sans limite."""
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT hachage, verrou_jusqu_a FROM application.comptes"
            " WHERE id = %s AND actif",
            (ident,),
        )
        ligne = await curseur.fetchone()
    if ligne is None:
        return Refus("inactif", ident)
    empreinte, verrou = cast(tuple[str, datetime | None], ligne)
    bon = await run_in_threadpool(hacheur.verifier, empreinte, ancien)
    if verrou is not None and verrou > datetime.now(UTC):
        return Refus("verrou", ident)
    if not bon:
        return await _compter_un_echec(base, ident, seuil, duree_verrou_s)
    nouvelle = await run_in_threadpool(hacheur.hacher, nouveau)
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "UPDATE application.comptes SET hachage = %s, generation = generation + 1,"
            " doit_changer_mdp = false, echecs_connexion = 0, verrou_jusqu_a = NULL,"
            " modifie_le = now() WHERE id = %s"
            " RETURNING id, role, actif, generation, doit_changer_mdp, nom_affiche",
            (nouvelle, ident),
        )
        compte = _compte(cast(tuple[object, ...], await curseur.fetchone()))
        await journaliser(cx, ident, "mot_de_passe_change", ident)
    return compte


async def couper_sessions(base: Base, ident: int) -> None:
    """La génération + 1 : toute session du compte, toute copie de son cookie, tombe."""
    async with base.transaction() as cx:
        await cx.execute(
            "UPDATE application.comptes SET generation = generation + 1 WHERE id = %s",
            (ident,),
        )


async def changer_etat(
    base: Base, ident: int, *, actif: bool, par_compte_id: int
) -> Literal["fait", "inchange", "inconnu"]:
    """Désactiver (génération + 1 : ses sessions tombent) ou réactiver un compte, et une
    ligne de journal — seulement s'il change d'état : un compte déjà dans cet état ne
    laisse aucune trace."""
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "UPDATE application.comptes SET actif = %s,"
            " generation = generation + CASE WHEN %s THEN 0 ELSE 1 END,"
            " modifie_le = now() WHERE id = %s AND actif <> %s RETURNING id",
            (actif, actif, ident, actif),
        )
        if await curseur.fetchone() is None:
            curseur = await cx.execute(
                "SELECT 1 FROM application.comptes WHERE id = %s", (ident,)
            )
            return "inchange" if await curseur.fetchone() is not None else "inconnu"
        await journaliser(
            cx, par_compte_id, "compte_reactive" if actif else "compte_desactive", ident
        )
    return "fait"


async def lister(
    base: Base, *, role: str | None, page: int, par_page: int
) -> tuple[list[Ligne], int, int]:
    """Une page de la liste, par date de création ; le nombre de comptes du filtre ; la
    page rendue (la demande, ramenée entre 1 et la dernière)."""
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT count(*) FROM application.comptes"
            " WHERE %(role)s::text IS NULL OR role = %(role)s",
            {"role": role},
        )
        (total,) = cast(tuple[int], await curseur.fetchone())
        # La page demandée, ramenée entre la première et la dernière.
        page = min(max(1, page), max(1, -(-total // par_page)))
        curseur = await cx.execute(
            "SELECT id, pseudo, nom_affiche, role, actif, cree_le"
            " FROM application.comptes"
            " WHERE %(role)s::text IS NULL OR role = %(role)s"
            " ORDER BY cree_le, id LIMIT %(limite)s OFFSET %(decalage)s",
            {"role": role, "limite": par_page, "decalage": (page - 1) * par_page},
        )
        lignes = [
            Ligne(*cast(tuple[int, str, str, str, bool, datetime], ligne))
            for ligne in await curseur.fetchall()
        ]
    return lignes, total, page


# --------------------------------------------------------------------------- #
#  L'effacement du compte par son titulaire (E4, lot 4, CL5)
# --------------------------------------------------------------------------- #
async def pseudo_de(base: Base, ident: int) -> str | None:
    """Le pseudo d'un compte, pour vérifier son mot de passe par le chemin même de la
    connexion (`authentifier`) ; il ne sort pas de l'application."""
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT pseudo FROM application.comptes WHERE id = %s", (ident,)
        )
        ligne = await curseur.fetchone()
    return None if ligne is None else str(ligne[0])


async def supprimer(base: Base, ident: int) -> None:
    """Le compte supprimé physiquement, en une transaction : d'abord la ligne
    `compte_supprime` du journal (par le compte lui-même, sur lui-même), puis le
    `DELETE` — la cascade emporte ses listes et ses compteurs de questions, la
    contrainte met `par_compte_id` à NULL : il reste « un compte n° n a été supprimé »,
    et ce numéro ne nomme plus personne. La ligne du compte est tenue d'abord ; absente
    (une autre session l'a supprimée) : `DejaSupprime`, la transaction annulée, rien au
    journal ; des critiques signées : `CompteNonSupprimable`, rien non plus."""
    try:
        async with base.transaction() as cx:
            # La ligne tenue d'abord : supprimée entre-temps par une autre session, la
            # ligne de journal n'aurait plus de compte à citer.
            curseur = await cx.execute(
                "SELECT 1 FROM application.comptes WHERE id = %s FOR UPDATE", (ident,)
            )
            if await curseur.fetchone() is None:
                raise DejaSupprime
            await journaliser(cx, ident, "compte_supprime", ident)
            # La ligne est tenue : le `DELETE` la trouve.
            await cx.execute("DELETE FROM application.comptes WHERE id = %s", (ident,))
    except errors.ForeignKeyViolation:
        raise CompteNonSupprimable from None
