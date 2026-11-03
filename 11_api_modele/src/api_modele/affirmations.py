"""Les affirmations que la ligne de titre contredit : deux contrôles de sortie.

La ligne de titre donne au modèle la disponibilité et le public de chaque série, et
l'invite les rend prioritaires sur ce qu'une critique dit. Une consigne ne tient pas
tout : ces deux contrôles rejettent les sorties qui disent le contraire de la ligne.

- `disponibilite` : le texte dit une série disponible, publiée, éditée, sortie ou
  commandable en France, alors que sa ligne dit `seulement_au_japon` ou `inconnue` ;
- `demographie` : le texte affirme un public (shōnen, seinen, shōjo, josei) différent de
  celui de la ligne, quand elle en porte un.

Les formes sont une liste versionnée (`config/formes_affirmations.toml`), affirmatives
seulement : une négation, une question, une comparaison ne sont pas des affirmations.

**À quelle série une forme se rapporte** : la marque `[S:<id>]` la plus proche avant
elle dans la phrase ; sinon, la première après elle dans la phrase ; sinon, la dernière
des phrases d'avant ; sans aucune marque, la série citée s'il n'y en a qu'une. Une
marque hors des séries rendues ne se contrôle pas : sa ligne n'existe pas.

Pas de contrôle sur les tomes ni le statut : leurs formes sont trop variées.
"""

from __future__ import annotations

import re
import tomllib
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from api_modele.configuration import RACINE_MODULE
from api_modele.modeles import Serie

FICHIER = RACINE_MODULE / "config" / "formes_affirmations.toml"
MARQUE = re.compile(r"\[s:(\d+)\]")
FIN_DE_PHRASE = re.compile(r"[.!?\n]")
BORNE_DE_PROPOSITION = re.compile(r"[,;:()—–]|\bmais\b")
DISPONIBILITES_MUETTES = ("seulement_au_japon", "inconnue", None)

type Motif = Literal["disponibilite", "demographie"]


@dataclass(frozen=True, slots=True)
class Formes:
    disponibilite: re.Pattern[str]
    publics: Mapping[str, re.Pattern[str]]
    reserves: re.Pattern[str]


def charger_formes(fichier: Path = FICHIER) -> Formes:
    """La liste versionnée, compilée ; une forme fautive empêche le démarrage."""
    donnees = tomllib.loads(fichier.read_text(encoding="utf-8"))
    demographie = donnees["demographie"]
    reserves = donnees["reserves"]
    return Formes(
        disponibilite=re.compile("|".join(donnees["disponibilite"]["formes"])),
        publics={
            nom: re.compile(
                "|".join(f.replace("{public}", motif) for f in demographie["formes"])
            )
            for nom, motif in demographie["publics"].items()
        },
        reserves=re.compile(f"{reserves['negations']}|{reserves['comparaisons']}"),
    )


FORMES = charger_formes()


def aplatir(texte: str) -> str:
    """Minuscules, sans accents, apostrophe droite : la forme des motifs."""
    decompose = unicodedata.normalize("NFKD", texte.replace("’", "'"))
    return "".join(c for c in decompose if not unicodedata.combining(c)).casefold()


def bornes_de_phrase(texte: str, debut: int, fin: int) -> tuple[int, int]:
    """Le début de la phrase de la forme, et sa fin (son signe final compris)."""
    avant = [m.end() for m in FIN_DE_PHRASE.finditer(texte, 0, debut)]
    apres = FIN_DE_PHRASE.search(texte, fin)
    return (avant[-1] if avant else 0, apres.end() if apres else len(texte))


def affirmee(texte: str, forme: re.Match[str]) -> bool:
    """Ni une question, ni une réserve dans la forme ou devant elle, dans sa
    proposition."""
    debut, fin = bornes_de_phrase(texte, forme.start(), forme.end())
    if texte[debut:fin].rstrip().endswith("?"):
        return False
    avant = MARQUE.sub(" ", texte[debut : forme.start()])
    proposition = BORNE_DE_PROPOSITION.split(avant)[-1]
    return FORMES.reserves.search(f"{proposition} {forme.group(0)}") is None


def serie_visee(texte: str, forme: re.Match[str], citees: Sequence[int]) -> int | None:
    """La série à laquelle la forme se rapporte (règle du module)."""
    debut, fin = bornes_de_phrase(texte, forme.start(), forme.end())
    marques = list(MARQUE.finditer(texte))
    dans_la_phrase_avant = [m for m in marques if debut <= m.start() < forme.start()]
    if dans_la_phrase_avant:
        return int(dans_la_phrase_avant[-1].group(1))
    dans_la_phrase_apres = [m for m in marques if forme.start() <= m.start() < fin]
    if dans_la_phrase_apres:
        return int(dans_la_phrase_apres[0].group(1))
    precedentes = [m for m in marques if m.start() < debut]
    if precedentes:
        return int(precedentes[-1].group(1))
    return citees[0] if len(citees) == 1 else None


def contredite(
    reponse: str, series: Sequence[Serie], citees: Sequence[int]
) -> tuple[Motif, int] | None:
    """Le motif et la série de la première affirmation que la ligne contredit, la
    disponibilité d'abord ; `None` s'il n'y en a pas."""
    lignes = {s.serie_id: s.metadonnees for s in series if s.serie_id is not None}
    texte = aplatir(reponse)
    for forme in FORMES.disponibilite.finditer(texte):
        serie_id = serie_visee(texte, forme, citees)
        if (
            serie_id in lignes
            and lignes[serie_id].disponibilite in DISPONIBILITES_MUETTES
            and affirmee(texte, forme)
        ):
            return "disponibilite", serie_id
    for public, motif in FORMES.publics.items():
        for forme in motif.finditer(texte):
            serie_id = serie_visee(texte, forme, citees)
            if serie_id not in lignes:
                continue
            ligne = lignes[serie_id].demographie
            if ligne in FORMES.publics and ligne != public and affirmee(texte, forme):
                return "demographie", serie_id
    return None
