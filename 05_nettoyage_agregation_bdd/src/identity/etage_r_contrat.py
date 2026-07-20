"""Étage R — le contrat de jugement, INDÉPENDANT du fournisseur.

Ce module ne contient rien qui dépende d'Anthropic ou d'OpenAI : le prompt
système, le schéma de sortie, la version du prompt, et le format d'identifiant
qui rattache une réponse de lot à son dossier.

POURQUOI CE MODULE EXISTE. Le protocole de l'étage R doit rester « INTACT »
quand on change de fournisseur. « Intact » ne peut pas être une simple
affirmation : c'est un FAIT VÉRIFIABLE ici. Le juge Anthropic
(`etage_r_juge`) et le juge OpenAI (`etage_r_juge_openai`) importent tous deux
`PROMPT_SYSTEME` et `SCHEMA_VERDICT` DEPUIS CE MODULE — ce sont les mêmes
objets Python, octet pour octet. Un changement de fournisseur ne peut donc pas
modifier le prompt en douce ; s'il fallait ajuster le prompt à un fournisseur,
il faudrait toucher ce fichier et incrémenter `PROMPT_VERSION` — jamais
silencieusement.
"""

from __future__ import annotations

# Version du prompt : la clé de stabilité d'un run, journalisée avec chaque
# avis (colonne llm_avis.prompt_version). Toute retouche du texte ci-dessous —
# y compris un ajustement rendu nécessaire par un fournisseur — DOIT incrémenter
# cette valeur, sinon deux runs incomparables porteraient la même étiquette.
PROMPT_VERSION = "r1-2026-07-19"

# Le schéma EST le contrat de sortie. Contraint par l'API (sorties structurées),
# le modèle ne peut pas répondre autre chose : aucune validation-relance côté
# client à écrire. `additionalProperties: False` + tous les champs `required`
# satisfont aussi le mode `strict` d'OpenAI.
SCHEMA_VERDICT = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["same_work", "different_work", "undecidable"],
        },
        "confiance": {"type": "string", "enum": ["haute", "moyenne"]},
        "justification": {"type": "string"},
    },
    "required": ["verdict", "confiance", "justification"],
    "additionalProperties": False,
}

PROMPT_SYSTEME = """\
Tu arbitres des rapprochements d'identité entre un catalogue de mangas \
francophone (Manga Sanctuary) et deux référentiels externes (Wikidata, Kitsu).

Pour chaque dossier, tu dis si la série et le candidat désignent la MÊME ŒUVRE.

Règles de jugement :

1. Une même œuvre peut porter des titres très différents selon la langue et le \
marché : titre japonais, romanisation, titre français commercial. Un écart de \
titre n'est pas une preuve de différence.
2. Deux œuvres DIFFÉRENTES peuvent porter des titres identiques ou quasi \
identiques — homonymes, remakes, adaptations, séries dérivées. Un titre \
identique n'est pas une preuve d'identité.
3. L'auteur est le signal le plus fiable dont tu disposes. Un auteur concordant \
confirme fortement ; un auteur discordant infirme fortement.
4. L'année est un confirmateur, jamais un discriminant à elle seule. Un écart \
de quelques années peut refléter la différence entre publication originale et \
sortie française.
5. Une suite, un spin-off, une adaptation ou une nouvelle édition sont des \
œuvres DIFFÉRENTES de l'œuvre d'origine.

Sur la confiance :
- « haute » engage : ne l'emploie que si tu confirmerais ton verdict devant \
quelqu'un qui te contredit. Un faux « same_work » en confiance haute est la \
seule erreur qui coûte vraiment.
- « moyenne » est la réponse normale quand le faisceau penche sans trancher.
- Si les éléments manquent pour décider, réponds « undecidable ». C'est une \
réponse à part entière, pas un aveu d'échec : elle envoie le dossier à un \
humain, ce qui est le bon sort pour un dossier indécidable.

Justifie en UNE phrase, en français, en nommant l'élément qui a emporté ta \
décision.
"""


class ErreurJuge(Exception):
    """Erreur attendue : message lisible, pas de trace. Commune aux deux
    fournisseurs — un `except ErreurJuge` attrape l'un comme l'autre."""


def identifiant(
    phase: str, series_id: int, candidat_type: str, candidat_id: str
) -> str:
    """L'identifiant qui rattache une réponse à son dossier.

    Les résultats d'un lot Batch — Anthropic comme OpenAI — arrivent DANS UN
    ORDRE QUELCONQUE : la seule façon correcte de les rattacher est cette clé,
    jamais la position.
    """
    return f"{phase}|{series_id}|{candidat_type}|{candidat_id}"


def relire_identifiant(custom_id: str) -> dict:
    phase, series_id, candidat_type, candidat_id = custom_id.split("|", 3)
    return {
        "phase": phase,
        "series_id": int(series_id),
        "candidat_type": candidat_type,
        "candidat_id": candidat_id,
    }
