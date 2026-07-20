"""Étage R — le juge LLM Anthropic. Client Batch, régime AVIS-SEULEMENT.

    uv run python -m identity.etage_r_juge --help

CE MODULE N'ÉCRIT QUE DANS manga.llm_avis. Ni match_decision, ni
work_identity : au run 1 le juge rend des avis, l'humain promeut. Ce n'est pas
une convention de politesse — c'est pourquoi la migration 010 a créé une table
séparée : aucun bug ni copier-coller ne peut transformer un avis en décision.

MODÈLE ET CONTRAT DE SORTIE. `claude-sonnet-5`, via la **Batch API** (moitié
prix, aucune latence à tenir : tout l'enrichissement est batch offline, le
chatbot ne touche jamais l'API en ligne). Le verdict est contraint par
`output_config.format` — le schéma EST le contrat (défini dans
`etage_r_contrat`), il n'y a pas de validation-relance côté client à écrire.

  ⚠️ AUCUN PARAMÈTRE D'ÉCHANTILLONNAGE. `temperature` est refusée (400) sur
  les modèles à sorties structurées. La stabilité d'un run ne vient donc pas
  d'une température mais du couple (modele, prompt_version) : le prompt est
  figé, versionné, et journalisé avec chaque avis.

FOURNISSEUR. Ce module vise Anthropic. Le juge OpenAI vit dans
`etage_r_juge_openai` et importe le MÊME contrat (`etage_r_contrat`) : changer
de fournisseur ne touche ni le prompt ni le schéma.

CLÉ D'API. Lue dans l'environnement, jamais en dur, jamais journalisée. Son
absence est une ERREUR EXPLICITE au démarrage — pas un plantage au premier
appel après vingt minutes d'assemblage.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from identity.etage_r_contrat import (
    PROMPT_SYSTEME,
    PROMPT_VERSION,
    SCHEMA_VERDICT,
    ErreurJuge,
    identifiant,
    relire_identifiant,
)

# Ré-exportés depuis le contrat neutre : disponibles sous `etage_r_juge.*` pour
# le code d'appel et les tests, mais définis une seule fois (etage_r_contrat).
__all__ = [
    "ErreurJuge",
    "MODELE",
    "PROMPT_SYSTEME",
    "PROMPT_VERSION",
    "SCHEMA_VERDICT",
    "cle_api",
    "construire_requete",
    "identifiant",
    "relire_identifiant",
]

MODULE = Path(__file__).resolve().parents[2]

MODELE = "claude-sonnet-5"


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
    """Contrôle que le jalon R-b (Anthropic) est exécutable : clé, SDK, modèle."""
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
