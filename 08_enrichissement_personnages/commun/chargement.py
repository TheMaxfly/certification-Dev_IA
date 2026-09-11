"""Briques de chargement partagees par les sources.

**Le raw ne tient jamais en memoire.** Kitsu pese 121 Mo ; le perimetre AniList
est cinq fois plus large. Un chargeur qui lit un fichier d'un bloc marche
aujourd'hui et echoue demain, sans que rien n'ait change dans son code. D'ou le
tampon ci-dessous : les lignes sont versees par lots bornes, et la memoire ne
depend que de la taille du lot — jamais de celle du fichier.

La seule structure qui grandit avec la source est l'ensemble des identifiants
deja vus, en O(personnages distincts) et non en O(octets) : 34 293 chaines pour
Kitsu. C'est le prix a payer pour ne pas ecrire douze fois les memes formes,
et il reste borne.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from datetime import datetime

import psycopg

#: Lignes accumulees avant un versement. Assez grand pour que le COPY soit
#: rentable, assez petit pour que la memoire reste plate.
TAILLE_LOT = 5_000


def normaliser(forme: str) -> str:
    """Forme normalisee pour la recherche : minuscules, sans accent.

    Jamais affichee — `forme` porte la graphie d'origine. Sur du japonais la
    decomposition est sans effet, ce qui est le comportement voulu : une
    graphie CJK ne se normalise pas, elle se compare telle quelle.
    """
    plat = unicodedata.normalize("NFD", forme.strip().casefold())
    return "".join(c for c in plat if unicodedata.category(c) != "Mn")


def date_iso(valeur: str | None) -> str | None:
    """Valide une date de source et la rend en ISO canonique, ou leve.

    Le parsing se fait **en Python**, jamais par `to_date` : le serveur tourne
    en `lc_time = fr_FR.UTF-8`, et une conversion qui en depend rendrait le
    chargement sensible a la configuration de la machine.
    """
    if valeur is None or not str(valeur).strip():
        return None
    brut = str(valeur).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(brut).isoformat()
    except ValueError as erreur:
        raise ValueError(f"date de source illisible : {valeur!r}") from erreur


class TamponCopy:
    """Accumule des lignes et les verse par `COPY` quand le lot est plein."""

    def __init__(
        self,
        connexion: psycopg.Connection,
        table: str,
        colonnes: Sequence[str],
        taille_lot: int = TAILLE_LOT,
    ) -> None:
        self.connexion = connexion
        self.table = table
        self.colonnes = list(colonnes)
        self.taille_lot = taille_lot
        self._lot: list[tuple] = []
        self.lignes_versees = 0

    def ajouter(self, ligne: Sequence) -> None:
        self._lot.append(tuple(ligne))
        if len(self._lot) >= self.taille_lot:
            self.vider()

    def etendre(self, lignes: Iterable[Sequence]) -> None:
        for ligne in lignes:
            self.ajouter(ligne)

    def vider(self) -> None:
        """Verse le lot courant. Sans effet si le lot est vide."""
        if not self._lot:
            return
        colonnes = ", ".join(self.colonnes)
        ordre = f"COPY {self.table} ({colonnes}) FROM STDIN"
        with self.connexion.cursor() as curseur, curseur.copy(ordre) as copie:
            for ligne in self._lot:
                copie.write_row(ligne)
        self.lignes_versees += len(self._lot)
        self._lot.clear()

    def __enter__(self) -> TamponCopy:
        return self

    def __exit__(self, *_) -> None:
        self.vider()


# ---------------------------------------------------------------------------
# Promotion — l'ORDRE est une contrainte, pas une preference.
#
# Les personnages d'abord : les formes, descriptions et liens les referencent
# par cle etrangere. Inverser l'ordre ferait rejeter les trois autres tables,
# et le message d'erreur parlerait de cle etrangere, jamais d'ordre.
# ---------------------------------------------------------------------------

PROMOTIONS: list[tuple[str, str]] = [
    (
        "manga.characters",
        """
        INSERT INTO manga.characters (
            source, source_id, slug, canonical_name, mal_id,
            source_created_at, source_updated_at)
        SELECT DISTINCT ON (s.source, s.source_id)
               s.source, s.source_id,
               NULLIF(btrim(s.slug), ''),
               btrim(s.canonical_name),
               NULLIF(btrim(s.mal_id), ''),
               NULLIF(s.source_created_at, '')::timestamptz,
               NULLIF(s.source_updated_at, '')::timestamptz
        FROM staging.characters s
        WHERE btrim(COALESCE(s.canonical_name, '')) <> ''
        ORDER BY s.source, s.source_id
        ON CONFLICT (source, source_id) DO NOTHING
        """,
    ),
    (
        "manga.character_forms",
        """
        INSERT INTO manga.character_forms (
            character_uid, forme, forme_norm, forme_type, forme_lang, source)
        SELECT DISTINCT ON (c.character_uid, s.forme_norm, s.forme_type, s.source)
               c.character_uid, btrim(s.forme), s.forme_norm, s.forme_type,
               NULLIF(btrim(COALESCE(s.forme_lang, '')), ''), s.source
        FROM staging.character_forms s
        JOIN manga.characters c
          ON c.source = s.source AND c.source_id = s.source_id
        WHERE btrim(COALESCE(s.forme, '')) <> ''
        -- `forme_lang` entre dans le tri, et ce n'est pas decoratif : 12
        -- personnages portent la meme forme normalisee sous deux cles de
        -- `names` (« Easy » et « EASY », ou une valeur romanisee rangee
        -- sous `ja_jp`). La contrainte d'unicite n'en garde qu'une — a
        -- raison, c'est la meme cible de recherche — mais SANS ce critere
        -- la langue retenue dependrait de l'ordre physique des lignes, et
        -- deux rechargements pourraient ne pas rendre le meme resultat.
        ORDER BY c.character_uid, s.forme_norm, s.forme_type, s.source,
                 s.forme_lang NULLS LAST
        ON CONFLICT DO NOTHING
        """,
    ),
    (
        "manga.character_descriptions",
        """
        INSERT INTO manga.character_descriptions (
            character_uid, source, lang, texte, licence, provenance)
        SELECT DISTINCT ON (c.character_uid, s.source)
               c.character_uid, s.source,
               NULLIF(btrim(COALESCE(s.lang, '')), ''),
               s.texte,
               NULLIF(btrim(COALESCE(s.licence, '')), ''),
               NULLIF(btrim(COALESCE(s.provenance, '')), '')
        FROM staging.character_descriptions s
        JOIN manga.characters c
          ON c.source = s.source AND c.source_id = s.source_id
        WHERE btrim(COALESCE(s.texte, '')) <> ''
        ORDER BY c.character_uid, s.source
        ON CONFLICT DO NOTHING
        """,
    ),
    (
        "manga.character_work",
        """
        INSERT INTO manga.character_work (
            character_uid, oeuvre_source, oeuvre_id, role_source,
            role_normalise, source)
        SELECT DISTINCT ON (c.character_uid, s.oeuvre_source, s.oeuvre_id, s.source)
               c.character_uid, s.oeuvre_source, s.oeuvre_id,
               NULLIF(btrim(COALESCE(s.role_source, '')), ''),
               NULLIF(btrim(COALESCE(s.role_normalise, '')), ''),
               s.source
        FROM staging.character_work s
        JOIN manga.characters c
          ON c.source = s.source AND c.source_id = s.source_id
        WHERE btrim(COALESCE(s.oeuvre_id, '')) <> ''
        ORDER BY c.character_uid, s.oeuvre_source, s.oeuvre_id, s.source
        ON CONFLICT DO NOTHING
        """,
    ),
]


def promouvoir(connexion: psycopg.Connection) -> dict[str, int]:
    """Joue les quatre promotions DANS L'ORDRE et rend les comptes inseres."""
    inseres: dict[str, int] = {}
    with connexion.cursor() as curseur:
        for table, sql in PROMOTIONS:
            curseur.execute(sql)
            inseres[table] = curseur.rowcount
    return inseres
