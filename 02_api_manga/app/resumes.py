"""Le résumé servi d'une série (E4, lot 2b, contrat 0.6.0) : le résumé Manga Sanctuary
**coupé avant le premier extrait de critique**, sinon le résumé Kitsu, sinon rien.

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

Un résumé sans extrait reste tel quel, quelle que soit sa longueur (le plancher juge ce
qu'une coupe laisse) ; un résumé Manga Sanctuary absent passe à Kitsu.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .recherche import normaliser_a_longueur_egale

type Source = Literal["manga_sanctuary", "kitsu"]
#: La longueur d'un début de critique cherché dans le résumé.
DEBUT_CARACTERES = 60
#: Ce qui finit une phrase, pour ramener la coupe.
FINS_DE_PHRASE = ".!?\n"


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
        if position is None:
            # Sans extrait, le résumé reste tel quel, court ou non : le plancher juge
            # ce qu'une coupe laisse, pas un résumé que rien n'a touché.
            return Resume(manga_sanctuary, "manga_sanctuary")
        texte = couper(manga_sanctuary, position)
        if len(texte) >= plancher:
            return Resume(texte, "manga_sanctuary")
    if kitsu and kitsu.strip():
        return Resume(kitsu, "kitsu")
    return Resume(None, None)
