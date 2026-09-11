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
