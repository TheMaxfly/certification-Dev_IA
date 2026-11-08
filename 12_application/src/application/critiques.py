"""Les critiques signées des libraires (E4, lot 3, spec 01) : règles et accès à la base.

Sur le modèle de `comptes` : des fonctions asynchrones, des requêtes paramétrées, une
transaction par opération, des erreurs nommées sans valeur, et la ligne du journal des
actions écrite **dans la même transaction** que l'action — une erreur après l'écriture
ne laisse ni critique ni ligne de journal.

- **Qui écrit** : un libraire publie, modifie et retire les siennes ; un administrateur
  retire n'importe laquelle et n'en écrit aucune — pas même une qui serait à son nom
  (cadre, décision 2).
- **Une critique publiée par (libraire, série, tome)** : l'index `critiques_une_publiee`
  le garantit ; deux envois croisés se rattrapent en `DejaPubliee`, jamais en 500.
- **Retirer** est un statut, jamais une suppression : ce module ne supprime rien, et le
  rôle `app_rw` ne le pourrait pas (027).
- **Le journal** : `critique_publiee`, `critique_modifiee`, `critique_retiree`, objet
  `critique`, et des `details` faits d'identifiants et de codes — **jamais le texte**.
- **Le texte** est gardé tel que saisi (espaces des bords retirés, sauts de ligne
  normalisés) : l'échappement est l'affaire des gabarits.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

import psycopg
from psycopg import errors
from psycopg.types.json import Jsonb

from application.base import Base
from application.comptes import Compte
from application.config import Critiques as Regles

type EnTantQue = Literal["auteur", "admin"]
type Action = Literal["critique_publiee", "critique_modifiee", "critique_retiree"]
type Issue = Literal["fait", "deja_retiree"]
type Statut = Literal["publiee", "retiree"]

#: Les contraintes de la 027 → le champ qu'elles visent (une violation nommée).
CONTRAINTES = {
    "critiques_texte_longueur": "texte",
    "critiques_note_sur_dix": "note",
    "critiques_label_forme": "label",
    "critiques_tome_complet": "volume_url",
}
#: Les colonnes d'une critique lue, dans l'ordre de `_critique`.
COLONNES = (
    "c.id, c.compte_id, a.nom_affiche, c.series_id, c.volume_url, c.volume_libelle,"
    " c.texte, c.note, c.label, c.statut, c.publie_le, c.modifie_le"
    " FROM application.critiques c JOIN application.comptes a ON a.id = c.compte_id"
)


@dataclass(frozen=True, slots=True)
class Critique:
    id: int
    compte_id: int
    nom_affiche: str
    series_id: int
    volume_url: str | None
    volume_libelle: str | None
    texte: str
    note: int
    label: str | None
    statut: str
    publie_le: datetime
    modifie_le: datetime

    @property
    def publiee(self) -> bool:
        return self.statut == "publiee"

    @property
    def modifiee(self) -> bool:
        return self.modifie_le != self.publie_le


@dataclass(frozen=True, slots=True)
class MoyenneLibraires:
    #: La moyenne des notes des critiques publiées, arrondie au dixième.
    note: float
    nombre: int


@dataclass(frozen=True, slots=True)
class Libraire:
    """Un libraire, pour le filtre de la modération : son nom affiché, jamais son
    pseudo."""

    id: int
    nom_affiche: str
    actif: bool


class CritiqueInvalide(Exception):
    """Une saisie hors des règles : le champ visé et un motif borné, jamais la
    valeur."""

    def __init__(self, champ: str, motif: str) -> None:
        super().__init__(champ, motif)
        self.champ = champ
        self.motif = motif


class DejaPubliee(Exception):
    """Le libraire a déjà une critique publiée sur ce couple (série, tome)."""

    def __init__(self, ident: int | None) -> None:
        super().__init__(ident)
        #: L'identifiant de la critique existante (None : retirée entre-temps).
        self.ident = ident


class CritiqueIntrouvable(Exception):
    """Aucune critique publiée à cet identifiant (inconnue, ou déjà retirée)."""


class PasLAuteur(Exception):
    """La critique existe, mais ce compte n'a pas le droit d'agir dessus."""


# --------------------------------------------------------------------------- #
#  Les règles de saisie
# --------------------------------------------------------------------------- #
def normaliser_texte(texte: str) -> str:
    """Les sauts de ligne en `\\n`, les espaces des bords retirés ; rien d'autre."""
    return texte.replace("\r\n", "\n").strip()


def a_un_controle(texte: str) -> bool:
    """Un caractère de contrôle autre que le saut de ligne (octet nul, tabulation,
    retour chariot isolé…)."""
    return any(c != "\n" and unicodedata.category(c) == "Cc" for c in texte)


def erreurs(
    regles: Regles, texte: str, note: object, label: str | None
) -> dict[str, str]:
    """Les champs fautifs → leur motif (`longueur`, `controle`, `note`, `label`), sur un
    texte déjà normalisé. Vide : la saisie tient."""
    fautes: dict[str, str] = {}
    if not regles.texte_min <= len(texte) <= regles.texte_max:
        fautes["texte"] = "longueur"
    elif a_un_controle(texte):
        fautes["texte"] = "controle"
    if isinstance(note, bool) or not isinstance(note, int) or not 0 <= note <= 10:
        fautes["note"] = "note"
    if label is not None and label not in regles.codes:
        fautes["label"] = "label"
    return fautes


def _valider(regles: Regles, texte: str, note: int, label: str | None) -> str:
    texte = normaliser_texte(texte)
    fautes = erreurs(regles, texte, note, label)
    if fautes:
        champ, motif = next(iter(fautes.items()))
        raise CritiqueInvalide(champ, motif)
    return texte


def _critique(ligne: tuple[object, ...]) -> Critique:
    valeurs = cast(
        tuple[
            int,
            int,
            str,
            int,
            str | None,
            str | None,
            str,
            int,
            str | None,
            str,
            datetime,
            datetime,
        ],
        ligne,
    )
    return Critique(*valeurs)


async def _journaliser(
    cx: psycopg.AsyncConnection,
    par_compte_id: int,
    action: Action,
    critique: int,
    details: dict[str, object],
) -> None:
    """Une ligne du journal des actions sur une critique : identifiants et codes
    seuls."""
    await cx.execute(
        "INSERT INTO application.journal_actions"
        " (par_compte_id, action, objet_type, objet_id, details)"
        " VALUES (%s, %s, 'critique', %s, %s)",
        (par_compte_id, action, critique, Jsonb(details)),
    )


# --------------------------------------------------------------------------- #
#  Écrire
# --------------------------------------------------------------------------- #
async def publier(
    base: Base,
    compte: Compte,
    series_id: int,
    volume_url: str | None,
    volume_libelle: str | None,
    texte: str,
    note: int,
    label: str | None,
    *,
    regles: Regles,
) -> int:
    """Une critique neuve, publiée à l'instant, et sa ligne de journal ; son
    identifiant.

    La série et le tome sont validés par l'appelant contre le catalogue (le 02) ; ici,
    leur cohérence : un tome a sa clé ET son libellé, ou ni l'un ni l'autre."""
    if compte.role != "libraire":
        raise PasLAuteur
    texte = _valider(regles, texte, note, label)
    if (volume_url is None) != (volume_libelle is None) or "" in (
        volume_url,
        volume_libelle,
    ):
        raise CritiqueInvalide("volume_url", "tome")
    try:
        async with base.transaction() as cx:
            curseur = await cx.execute(
                "INSERT INTO application.critiques (compte_id, series_id,"
                " volume_url, volume_libelle, texte, note, label)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (compte.id, series_id, volume_url, volume_libelle, texte, note, label),
            )
            (ident,) = cast(tuple[int], await curseur.fetchone())
            await _journaliser(
                cx,
                compte.id,
                "critique_publiee",
                ident,
                {
                    "series_id": series_id,
                    "tome": volume_url is not None,
                    "par": "auteur",
                },
            )
    except errors.UniqueViolation:
        # Deux envois croisés : l'index a refusé le second. L'existante, relue dans
        # une transaction neuve ; ni le message ni la cause de PostgreSQL (la ligne
        # refusée).
        existante = await publiee_existante(base, compte.id, series_id, volume_url)
        raise DejaPubliee(existante) from None
    except errors.IntegrityError as exc:
        contrainte = getattr(exc.diag, "constraint_name", None) or ""
        raise CritiqueInvalide(
            CONTRAINTES.get(contrainte, "critique"), "base"
        ) from None
    return ident


async def modifier(
    base: Base,
    compte: Compte,
    ident: int,
    texte: str,
    note: int,
    label: str | None,
    *,
    regles: Regles,
) -> None:
    """Le texte, la note, le label d'une critique publiée de son auteur — et rien
    d'autre : la série et le tome ne changent pas. `modifie_le` posé, une ligne de
    journal. Un administrateur ne modifie aucune critique, pas même une à son nom."""
    if compte.role != "libraire":
        raise PasLAuteur
    texte = _valider(regles, texte, note, label)
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "UPDATE application.critiques"
            " SET texte = %s, note = %s, label = %s, modifie_le = now()"
            " WHERE id = %s AND compte_id = %s AND statut = 'publiee'"
            " RETURNING series_id, volume_url IS NOT NULL",
            (texte, note, label, ident, compte.id),
        )
        ligne = await curseur.fetchone()
        if ligne is None:
            await _refus(cx, ident)
            raise CritiqueIntrouvable  # pragma: no cover — `_refus` lève toujours
        series_id, tome = cast(tuple[int, bool], ligne)
        await _journaliser(
            cx,
            compte.id,
            "critique_modifiee",
            ident,
            {"series_id": series_id, "tome": tome, "par": "auteur"},
        )


async def retirer(
    base: Base, par_compte: Compte, ident: int, en_tant_que: EnTantQue
) -> Issue:
    """Le statut `retiree`, jamais une suppression ; une ligne de journal au nom de qui
    retire (`details.par`). L'auteur retire les siennes ; l'administrateur, n'importe
    laquelle. Déjà retirée : « déjà retirée », et aucune ligne au journal."""
    if (en_tant_que, par_compte.role) not in (
        ("auteur", "libraire"),
        ("admin", "admin"),
    ):
        raise PasLAuteur
    auteur = par_compte.id if en_tant_que == "auteur" else None
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "UPDATE application.critiques SET statut = 'retiree', modifie_le = now()"
            " WHERE id = %(id)s AND statut = 'publiee'"
            " AND (%(auteur)s::bigint IS NULL OR compte_id = %(auteur)s)"
            " RETURNING series_id, volume_url IS NOT NULL",
            {"id": ident, "auteur": auteur},
        )
        ligne = await curseur.fetchone()
        if ligne is None:
            curseur = await cx.execute(
                "SELECT compte_id, statut FROM application.critiques WHERE id = %s",
                (ident,),
            )
            existe = await curseur.fetchone()
            if existe is None:
                raise CritiqueIntrouvable
            compte_id, statut = cast(tuple[int, str], existe)
            if auteur is not None and compte_id != auteur:
                raise PasLAuteur
            if statut == "retiree":
                return "deja_retiree"
            raise CritiqueIntrouvable  # pragma: no cover — publiée et à lui : retirée
        series_id, tome = cast(tuple[int, bool], ligne)
        await _journaliser(
            cx,
            par_compte.id,
            "critique_retiree",
            ident,
            {"series_id": series_id, "tome": tome, "par": en_tant_que},
        )
    return "fait"


async def _refus(cx: psycopg.AsyncConnection, ident: int) -> None:
    """Pourquoi une modification n'a touché aucune ligne : la critique n'existe pas ou
    n'est plus publiée (`CritiqueIntrouvable`), ou elle n'est pas à ce compte
    (`PasLAuteur`)."""
    curseur = await cx.execute(
        "SELECT statut FROM application.critiques WHERE id = %s", (ident,)
    )
    ligne = await curseur.fetchone()
    if ligne is None or ligne[0] != "publiee":
        raise CritiqueIntrouvable
    raise PasLAuteur


# --------------------------------------------------------------------------- #
#  Lire
# --------------------------------------------------------------------------- #
async def lire(base: Base, ident: int) -> Critique | None:
    async with base.transaction() as cx:
        curseur = await cx.execute(f"SELECT {COLONNES} WHERE c.id = %s", (ident,))
        ligne = await curseur.fetchone()
    return None if ligne is None else _critique(ligne)


async def publiees_de_la_serie(base: Base, series_id: int) -> list[Critique]:
    """Les critiques publiées d'une série, les plus récentes d'abord."""
    async with base.transaction() as cx:
        curseur = await cx.execute(
            f"SELECT {COLONNES} WHERE c.series_id = %s AND c.statut = 'publiee'"
            " ORDER BY c.publie_le DESC, c.id DESC",
            (series_id,),
        )
        return [_critique(ligne) for ligne in await curseur.fetchall()]


async def moyenne_de_la_serie(base: Base, series_id: int) -> MoyenneLibraires | None:
    """La moyenne des notes des critiques publiées, et leur nombre ; None sans
    critique."""
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT avg(note)::float8, count(*) FROM application.critiques"
            " WHERE series_id = %s AND statut = 'publiee'",
            (series_id,),
        )
        moyenne, nombre = cast(tuple[float | None, int], await curseur.fetchone())
    if not nombre or moyenne is None:
        return None
    return MoyenneLibraires(round(moyenne, 1), nombre)


async def publiee_existante(
    base: Base, compte_id: int, series_id: int, volume_url: str | None
) -> int | None:
    """L'identifiant de la critique publiée du couple (libraire, série, tome), ou None
    (pour dire « vous en avez déjà une » avant l'envoi)."""
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT id FROM application.critiques"
            " WHERE compte_id = %s AND series_id = %s"
            " AND coalesce(volume_url, '') = coalesce(%s, '') AND statut = 'publiee'",
            (compte_id, series_id, volume_url),
        )
        ligne = await curseur.fetchone()
    return None if ligne is None else cast(int, ligne[0])


def _page(page: int, total: int, par_page: int) -> int:
    """La page demandée, ramenée entre la première et la dernière."""
    return min(max(1, page), max(1, -(-total // par_page)))


async def les_miennes(
    base: Base,
    compte_id: int,
    page: int,
    *,
    par_page: int,
    statut: Statut | None = None,
) -> tuple[list[Critique], int, int]:
    """Toutes les critiques d'un libraire (publiées et retirées, ou d'un seul statut),
    les plus récemment changées d'abord ; leur nombre ; la page rendue."""
    filtre = {"compte": compte_id, "statut": statut}
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT count(*) FROM application.critiques WHERE compte_id = %(compte)s"
            " AND (%(statut)s::text IS NULL OR statut = %(statut)s)",
            filtre,
        )
        (total,) = cast(tuple[int], await curseur.fetchone())
        page = _page(page, total, par_page)
        curseur = await cx.execute(
            f"SELECT {COLONNES} WHERE c.compte_id = %(compte)s"
            " AND (%(statut)s::text IS NULL OR c.statut = %(statut)s)"
            " ORDER BY c.modifie_le DESC, c.id DESC"
            " LIMIT %(limite)s OFFSET %(decalage)s",
            filtre | {"limite": par_page, "decalage": (page - 1) * par_page},
        )
        lignes = [_critique(ligne) for ligne in await curseur.fetchall()]
    return lignes, total, page


async def toutes_publiees(
    base: Base, page: int, *, par_page: int, compte_id: int | None = None
) -> tuple[list[Critique], int, int]:
    """Pour la modération : les critiques publiées, les plus récentes d'abord, d'un
    libraire ou de tous ; leur nombre ; la page rendue."""
    filtre = {"compte": compte_id}
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT count(*) FROM application.critiques WHERE statut = 'publiee'"
            " AND (%(compte)s::bigint IS NULL OR compte_id = %(compte)s)",
            filtre,
        )
        (total,) = cast(tuple[int], await curseur.fetchone())
        page = _page(page, total, par_page)
        curseur = await cx.execute(
            f"SELECT {COLONNES} WHERE c.statut = 'publiee'"
            " AND (%(compte)s::bigint IS NULL OR c.compte_id = %(compte)s)"
            " ORDER BY c.publie_le DESC, c.id DESC"
            " LIMIT %(limite)s OFFSET %(decalage)s",
            filtre | {"limite": par_page, "decalage": (page - 1) * par_page},
        )
        lignes = [_critique(ligne) for ligne in await curseur.fetchall()]
    return lignes, total, page


async def libraires(base: Base) -> list[Libraire]:
    """Les comptes libraires, actifs et inactifs, par nom affiché (filtre de la
    modération)."""
    async with base.transaction() as cx:
        curseur = await cx.execute(
            "SELECT id, nom_affiche, actif FROM application.comptes"
            " WHERE role = 'libraire' ORDER BY nom_affiche, id"
        )
        return [
            Libraire(*cast(tuple[int, str, bool], ligne))
            for ligne in await curseur.fetchall()
        ]
