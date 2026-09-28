"""Découpage en fragments — la réplique exacte du banc de décembre.

1 200 caractères, 200 de recouvrement : une VALEUR DE CONTRÔLE, reprise pour que
le nouveau corpus reste comparable au banc de décembre, pas un choix. Elle sera
contestée sur le jeu d'évaluation.

La fonction reproduit `chunk_text_chars` de `06_.../Bench_embedding.py`, octet
pour octet, y compris ses deux particularités :

  - le texte est `strip()` AVANT découpage, et `char_start` / `char_end` sont des
    positions dans le texte ÉPURÉ, pas dans `doc_text` ;
  - un document de moins de 50 caractères n'est PAS découpé (filtre SQL
    `length(doc_text) >= 50` du banc). En décembre, 150 synopsis Kitsu sont
    restés sans fragment, donc introuvables, sans que rien ne le dise. Le
    plancher est conservé pour la comparabilité ; ces documents sont désormais
    COMPTÉS et rapportés, jamais passés sous silence.

Aucune troncature : un fragment est la tranche entière `[start:end]`. La preuve
de fidélité n'est pas ce commentaire mais le chargement lui-même : les 43 832
fragments Kitsu de décembre doivent ressortir identiques.
"""

from __future__ import annotations

import hashlib

TAILLE = 1_200
RECOUVREMENT = 200
#: En deçà, le banc de décembre ne découpait pas le document.
PLANCHER = 50


def decouper(
    texte: str, taille: int = TAILLE, recouvrement: int = RECOUVREMENT
) -> list[tuple[int, int, str]]:
    """Fragments `(debut, fin, texte)` d'un document, dans l'ordre."""
    if not texte:
        return []
    texte = texte.strip()
    if len(texte) <= taille:
        return [(0, len(texte), texte)]
    pas = max(1, taille - recouvrement)
    fragments = []
    debut = 0
    while debut < len(texte):
        fin = min(len(texte), debut + taille)
        fragment = texte[debut:fin].strip()
        if fragment:
            fragments.append((debut, fin, fragment))
        if fin == len(texte):
            break
        debut += pas
    return fragments


def est_decoupe(doc_text: str) -> bool:
    """Le banc de décembre découpait-il ce document ?"""
    return doc_text is not None and len(doc_text) >= PLANCHER


def empreinte_fragment(texte: str) -> str:
    """`chunk_hash` du banc : SHA-1 du texte du fragment."""
    return hashlib.sha1(texte.encode("utf-8")).hexdigest()
