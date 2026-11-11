"""Le résumé servi d'une série (E4, lot 2b, contrat 0.6.0 ; seconde coupe : lot 6,
0.7.0) : le résumé Manga Sanctuary **coupé avant le premier extrait de critique**, sinon
le résumé Kitsu, sinon rien.

Pourquoi : la collecte du référentiel a laissé, dans 1 844 résumés Manga Sanctuary
(mesure du 2026-11-08), des critiques de lecteurs recopiées à la suite du résumé ; la
fiche les affichait comme un résumé. Les données du schéma `manga` ne changent pas (le
vrai nettoyage appartient à la collecte) : l'API sert le résumé corrigé, et le brut
reste au contrat (`synopsis`).

La règle (décision de Max du 8 novembre 2026) :

1. les **débuts** : les 60 premiers caractères (bords retirés) de chaque critique de la
   série (`ms_reviews_all`, 60 caractères au moins) ;
2. résumé et débuts **normalisés à longueur égale**
   (`recherche.normaliser_a_longueur_egale`, la table du lot 2a, un caractère pour un —
   `unaccent` change la longueur : « œ » → « oe ») ; la position la plus basse d'un
   début dans le résumé ;
3. la coupe à cette position, **ramenée à la fin de la phrase précédente** (le dernier
   `.`, `!`, `?` ou saut de ligne avant), bords retirés ;
4. au moins `plancher` caractères (`SYNOPSIS_MIN_CHARS`) : le résumé coupé reste le
   principal (`manga_sanctuary`) ; sinon le résumé **Kitsu** de la série (par le moyeu
   d'identité) s'il existe (`kitsu`) ; sinon aucun.

Un résumé sans extrait reste tel quel, quelle que soit sa longueur (le plancher juge
ce qu'une coupe laisse) ; un résumé Manga Sanctuary absent passe à Kitsu.

**La seconde coupe** (E4, lot 6, contrat 0.7.0 ; décision de Max du 9 novembre 2026, sur
la mesure du lot 4) : des extraits de critiques ou de chroniques **absents des tables de
critiques** restent après la première coupe. Après elle, et avant le plancher, le
résumé est cherché, normalisé comme ci-dessus (apostrophe typographique ramenée à
« ' »), par les cinq familles de marques de critique (`MARQUES_CRITIQUE`) ; s'il en
porte **au moins deux familles**, il est coupé à la **première marque**, toutes
familles confondues, ramenée à la fin de la phrase précédente ; puis le plancher,
Kitsu, rien. Une seule famille ne coupe rien : la première personne seule apparaît dans
des résumés d'éditeur.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from .recherche import normaliser_a_longueur_egale

type Source = Literal["manga_sanctuary", "kitsu"]
#: La longueur d'un début de critique cherché dans le résumé.
DEBUT_CARACTERES = 60
#: Ce qui finit une phrase, pour ramener la coupe.
FINS_DE_PHRASE = ".!?\n"
#: Les cinq familles de marques de critique (E4, lot 4, spec 01 : les motifs de la
#: mesure du 2026-11-09, tels qu'écrits) ; cherchées dans le texte normalisé, motifs
#: normalisés de la même façon.
MARQUES_CRITIQUE: Mapping[str, str] = {
    "tomes_lus": r"\btomes? \d+ (à|a|-|au) \d+\b",
    "premiere_personne": (
        r"\b(j'ai|je |j'|mon avis|à mon (sens|goût)|je (recommande|conseille))"
    ),
    "note_chiffree": r"\b\d{1,2} ?/ ?(10|20)\b",
    "adresse_au_lecteur": (
        r"\b(vous (allez|devez|pouvez)|lisez|foncez|à lire absolument)\b"
    ),
    "appreciation": (
        r"\b(quel plaisir|coup de c(œ|oe)ur|chef[- ]d'(œ|oe)uvre|déçu|décevant"
        r"|excellent|incontournable)\b"
    ),
}
#: Le nombre de familles à partir duquel la seconde coupe tombe.
FAMILLES_MIN = 2


def forme_des_marques(texte: str) -> str:
    """Le texte comme les marques le lisent : la table du repli, à longueur égale, et
    l'apostrophe typographique ramenée (un caractère pour un)."""
    return normaliser_a_longueur_egale(texte).replace("\u2019", "'")


MOTIFS = {
    famille: re.compile(forme_des_marques(motif), re.IGNORECASE)
    for famille, motif in MARQUES_CRITIQUE.items()
}


def familles_touchees(texte: str) -> set[str]:
    """Les familles de marques présentes dans le texte."""
    forme = forme_des_marques(texte)
    return {famille for famille, motif in MOTIFS.items() if motif.search(forme)}


def position_premiere_marque(texte: str) -> int | None:
    """La position de la première marque, toutes familles confondues ; None : aucune."""
    forme = forme_des_marques(texte)
    trouvees = [m.start() for motif in MOTIFS.values() if (m := motif.search(forme))]
    return min(trouvees) if trouvees else None


@dataclass(frozen=True, slots=True)
class Resume:
    texte: str | None
    source: Source | None


def coupure(resume: str, debuts: Iterable[str]) -> int | None:
    """La position du premier extrait de critique dans le résumé ; None : aucun."""
    forme = normaliser_a_longueur_egale(resume)
    positions = [
        forme.find(normaliser_a_longueur_egale(debut))
        for debut in debuts
        if len(debut) >= DEBUT_CARACTERES
    ]
    trouvees = [p for p in positions if p >= 0]
    return min(trouvees) if trouvees else None


def couper(resume: str, position: int) -> str:
    """Le résumé avant `position`, jusqu'à la fin de la phrase précédente, bords
    retirés ; rien s'il n'y a pas de phrase avant."""
    tete = resume[:position]
    fin = max(tete.rfind(signe) for signe in FINS_DE_PHRASE)
    return tete[: fin + 1].strip() if fin >= 0 else ""


def resume_servi(
    manga_sanctuary: str | None,
    kitsu: str | None,
    debuts: Iterable[str],
    plancher: int,
) -> Resume:
    """Le résumé et sa source, selon la règle du module."""
    if manga_sanctuary and manga_sanctuary.strip():
        position = coupure(manga_sanctuary, debuts)
        texte = manga_sanctuary
        coupe = position is not None
        if position is not None:
            texte = couper(manga_sanctuary, position)
        # La seconde coupe (E4, lot 6) : après la première, avant le plancher.
        marque = position_premiere_marque(texte)
        if marque is not None and len(familles_touchees(texte)) >= FAMILLES_MIN:
            texte = couper(texte, marque)
            coupe = True
        if not coupe:
            # Sans extrait ni marques, le résumé reste tel quel, court ou non : le
            # plancher juge ce qu'une coupe laisse, pas un résumé que rien n'a touché.
            return Resume(manga_sanctuary, "manga_sanctuary")
        if len(texte) >= plancher:
            return Resume(texte, "manga_sanctuary")
    if kitsu and kitsu.strip():
        return Resume(kitsu, "kitsu")
    return Resume(None, None)
