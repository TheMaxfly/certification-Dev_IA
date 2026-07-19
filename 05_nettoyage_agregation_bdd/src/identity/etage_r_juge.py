"""Étage R — le juge LLM. Client Batch, régime AVIS-SEULEMENT.

    uv run python -m identity.etage_r_juge --help

CE MODULE N'ÉCRIT QUE DANS manga.llm_avis. Ni match_decision, ni
work_identity : au run 1 le juge rend des avis, l'humain promeut. Ce n'est pas
une convention de politesse — c'est pourquoi la migration 010 a créé une table
séparée : aucun bug ni copier-coller ne peut transformer un avis en décision.

MODÈLE ET CONTRAT DE SORTIE. `claude-sonnet-5`, via la **Batch API** (moitié
prix, aucune latence à tenir : tout l'enrichissement est batch offline, le
chatbot ne touche jamais l'API en ligne). Le verdict est contraint par
`output_config.format` — le schéma EST le contrat, il n'y a pas de validation-
relance côté client à écrire.

  ⚠️ AUCUN PARAMÈTRE D'ÉCHANTILLONNAGE. `temperature` est refusée (400) sur
  les modèles à sorties structurées. La stabilité d'un run ne vient donc pas
  d'une température mais du couple (modele, prompt_version) : le prompt est
  figé, versionné, et journalisé avec chaque avis.

CLÉ D'API. Lue dans l'environnement, jamais en dur, jamais journalisée. Son
absence est une ERREUR EXPLICITE au démarrage — pas un plantage au premier
appel après vingt minutes d'assemblage.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

MODULE = Path(__file__).resolve().parents[2]

MODELE = "claude-sonnet-5"

# Version du prompt : la clé de stabilité du run, journalisée avec chaque avis.
# Toute retouche du texte ci-dessous DOIT incrémenter cette valeur — sinon deux
# runs incomparables porteraient la même étiquette.
PROMPT_VERSION = "r1-2026-07-19"

# Le schéma EST le contrat de sortie. Contraint par l'API : le modèle ne peut
# pas répondre autre chose.
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
    """Erreur attendue : message lisible, pas de trace."""


def cle_api() -> str:
    """Lit la clé dans l'environnement. Absence = erreur explicite, immédiate.

    La clé n'est ni journalisée, ni écrite, ni incluse dans un message d'erreur.
    Le contrôle est fait AU DÉMARRAGE pour qu'un run mal configuré échoue en une
    seconde plutôt qu'après l'assemblage complet des dossiers.
    """
    for variable in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        valeur = os.environ.get(variable)
        if valeur:
            return valeur
    raise ErreurJuge(
        "Aucune clé d'API Anthropic dans l'environnement — STOP.\n"
        "  export ANTHROPIC_API_KEY='...'   (ou ANTHROPIC_AUTH_TOKEN)\n"
        "La clé se lit dans l'environnement uniquement : jamais en dur dans le "
        "code, jamais dans un fichier versionné, jamais journalisée."
    )


def construire_requete(custom_id: str, texte_dossier: str) -> dict:
    """Une entrée de lot Batch. Forme pure — testable sans réseau."""
    return {
        "custom_id": custom_id,
        "params": {
            "model": MODELE,
            "max_tokens": 1024,
            "system": PROMPT_SYSTEME,
            "output_config": {
                "format": {"type": "json_schema", "schema": SCHEMA_VERDICT}
            },
            "messages": [{"role": "user", "content": texte_dossier}],
        },
    }


def identifiant(
    phase: str, series_id: int, candidat_type: str, candidat_id: str
) -> str:
    """L'identifiant qui rattache une réponse à son dossier.

    Les résultats d'un lot Batch arrivent DANS UN ORDRE QUELCONQUE : la seule
    façon correcte de les rattacher est cette clé, jamais la position.
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


def client_anthropic():
    """Construit le client. Importé tardivement : R-a n'a pas besoin du SDK."""
    try:
        import anthropic
    except ModuleNotFoundError as erreur:
        raise ErreurJuge(
            "Le SDK anthropic n'est pas installé — STOP.\n  uv add anthropic"
        ) from erreur
    return anthropic.Anthropic(api_key=cle_api())


app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def verifier() -> None:
    """Contrôle que le jalon R-b est exécutable : clé, SDK, modèle."""
    typer.echo(f"modèle          : {MODELE}")
    typer.echo(f"prompt_version  : {PROMPT_VERSION}")
    typer.echo(f"prompt système  : {len(PROMPT_SYSTEME)} caractères")
    try:
        cle_api()
        typer.echo("clé d'API       : présente dans l'environnement")
    except ErreurJuge:
        typer.echo("clé d'API       : ABSENTE → jalon R-b non exécutable")
        raise
    client_anthropic()
    typer.echo("SDK anthropic   : importable, client construit")
    typer.echo("→ prêt pour R-b.")


def main() -> int:
    try:
        app()
    except ErreurJuge as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
