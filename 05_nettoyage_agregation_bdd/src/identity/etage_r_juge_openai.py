"""Étage R — le juge LLM OpenAI. Client Batch, régime AVIS-SEULEMENT.

    uv run python -m identity.etage_r_juge_openai --help

POURQUOI CE MODULE. R-b bascule d'Anthropic vers OpenAI (paiement Anthropic
indisponible — décision de contexte). Le PROTOCOLE est inchangé : mêmes
dossiers, même étalonnage seedé, même seuil de poursuite, étalonnage d'abord.
Ce qui change est le fournisseur de l'appel, rien d'autre. La preuve en est
que le prompt et le schéma sont importés de `etage_r_contrat` — les mêmes
objets que le juge Anthropic.

CE MODULE N'ÉCRIT QUE DANS manga.llm_avis (via le pilote de run, jalon suivant).
Régime avis-seulement : le juge rend des avis, l'humain promeut.

CONTRAT DE SORTIE. Sorties structurées OpenAI en mode `strict` :
`response_format.json_schema` porte `SCHEMA_VERDICT`. Le schéma EST le contrat,
comme côté Anthropic — aucune validation-relance côté client. Enveloppe visée :
Batch sur `/v1/chat/completions` (la surface Batch la plus stable ; reasoning
models compris via `reasoning_effort`). Si le modèle validé impose l'API
Responses, seule l'ENVELOPPE change — ni le prompt ni le schéma, donc
`prompt_version` inchangée.

  ⚠️ AUCUN PARAMÈTRE D'ÉCHANTILLONNAGE. Pas de `temperature`/`top_p` : refusés
  sur les modèles de raisonnement, et inutiles ici — la stabilité vient du
  couple (modele, prompt_version), pas d'une température.

MODÈLE — NON FIGÉ PAR DÉFAUT (protocole R-b, point 2). Coder un modèle en dur
ici court-circuiterait la règle : lister `/v1/models`, proposer le milieu de
gamme avec son tarif, et ATTENDRE une validation humaine avant tout appel
payant. Tant qu'aucun modèle n'est passé, `construire_requete` REFUSE.

CLÉ D'API. `OPENAI_API_KEY` (ou `OPENAI_AUTH_TOKEN`), lue dans l'environnement,
jamais en dur, jamais journalisée, jamais dans un message d'erreur. Son absence
est une erreur explicite et immédiate.
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

__all__ = [
    "ErreurJuge",
    "MODELE",
    "PROMPT_SYSTEME",
    "PROMPT_VERSION",
    "SCHEMA_VERDICT",
    "cle_api",
    "compter_tokens",
    "construire_requete",
    "identifiant",
    "relire_identifiant",
]

MODULE = Path(__file__).resolve().parents[2]

# AUCUN MODÈLE PAR DÉFAUT. Le choix passe par la commande `modeles` (liste
# /v1/models) puis validation humaine. Voir le docstring : un défaut ici
# contournerait la validation avant appel payant.
MODELE: str | None = None

# Plafond de la réponse. `max_completion_tokens` (et non `max_tokens`) est la
# forme acceptée par les modèles de raisonnement ; les tokens de raisonnement
# comptent dans ce plafond. Valeur généreuse pour ne pas tronquer un « medium ».
# Confirmée à la validation à sec (un seul dossier) avant le run complet.
MAX_TOKENS_SORTIE = 2048

# Encodage tiktoken de référence pour la volumétrie OpenAI. `o200k_base` est
# l'encodage des modèles récents (GPT-4o et suivants). Côté OpenAI, tiktoken
# est LE bon tokenizer — l'inverse de la volumétrie caractères imposée à R-a
# faute de clé Anthropic.
ENCODAGE_TOKENS = "o200k_base"


def cle_api() -> str:
    """Lit la clé OpenAI dans l'environnement. Absence = erreur immédiate.

    La clé n'est ni journalisée, ni écrite, ni incluse dans un message d'erreur.
    Contrôle AU DÉMARRAGE : un run mal configuré échoue en une seconde, pas
    après l'assemblage.
    """
    for variable in ("OPENAI_API_KEY", "OPENAI_AUTH_TOKEN"):
        valeur = os.environ.get(variable)
        if valeur:
            return valeur
    raise ErreurJuge(
        "Aucune clé d'API OpenAI dans l'environnement — STOP.\n"
        "  export OPENAI_API_KEY='...'   (ou OPENAI_AUTH_TOKEN)\n"
        "La clé se lit dans l'environnement uniquement : jamais en dur dans le "
        "code, jamais dans un fichier versionné, jamais journalisée."
    )


def construire_requete(
    custom_id: str,
    texte_dossier: str,
    modele: str | None = None,
    effort_raisonnement: str | None = None,
) -> dict:
    """Une entrée de lot Batch OpenAI. Forme pure — testable sans réseau.

    REFUSE si aucun modèle n'est choisi : la validation humaine du modèle
    (protocole R-b, point 2) précède toute construction de requête payante.
    """
    modele = modele or MODELE
    if not modele:
        raise ErreurJuge(
            "Aucun modèle choisi — STOP. Liste d'abord les modèles disponibles\n"
            "  uv run python -m identity.etage_r_juge_openai modeles\n"
            "puis fais valider le choix (milieu de gamme + tarif) AVANT tout "
            "appel payant, et passe-le explicitement à --modele."
        )
    body = {
        "model": modele,
        "messages": [
            {"role": "system", "content": PROMPT_SYSTEME},
            {"role": "user", "content": texte_dossier},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "verdict_identite",
                "strict": True,
                "schema": SCHEMA_VERDICT,
            },
        },
        "max_completion_tokens": MAX_TOKENS_SORTIE,
    }
    if effort_raisonnement:
        body["reasoning_effort"] = effort_raisonnement
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": body,
    }


def compter_tokens(texte: str, encodage: str = ENCODAGE_TOKENS) -> int:
    """Compte les tokens au tokenizer OpenAI. Ici tiktoken est exact (à la
    famille d'encodage près), contrairement au cas Anthropic de R-a."""
    import tiktoken

    return len(tiktoken.get_encoding(encodage).encode(texte))


def client_openai():
    """Construit le client. Importé tardivement : la construction des requêtes
    et la volumétrie n'ont pas besoin du SDK."""
    try:
        import openai
    except ModuleNotFoundError as erreur:
        raise ErreurJuge(
            "Le SDK openai n'est pas installé — STOP.\n  uv add openai"
        ) from erreur
    return openai.OpenAI(api_key=cle_api())


app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def modeles() -> None:
    """Liste les modèles disponibles via /v1/models (appel NON facturé).

    N'INVENTE AUCUN TARIF : /v1/models n'expose pas les prix. Les tarifs se
    confirment sur la page pricing d'OpenAI, et le choix (milieu de gamme)
    attend une validation humaine avant tout appel payant.
    """
    client = client_openai()
    ids = sorted(m.id for m in client.models.list().data)
    typer.echo(f"{len(ids)} modèles visibles :")
    for identifier in ids:
        typer.echo(f"  {identifier}")
    typer.echo("")
    typer.echo(
        "Tarifs : NON exposés par /v1/models — à confirmer sur la page pricing "
        "OpenAI. Aucun tarif n'est inventé ici."
    )
    typer.echo(
        "Étape suivante : proposer un milieu de gamme + tarif, obtenir la "
        "validation humaine, puis passer le modèle à --modele. Aucun appel "
        "payant avant cette validation."
    )


@app.command()
def verifier() -> None:
    """Contrôle l'exécutabilité de R-b (OpenAI) : clé, SDK, choix du modèle."""
    typer.echo(f"prompt_version  : {PROMPT_VERSION}")
    typer.echo(f"prompt système  : {len(PROMPT_SYSTEME)} caractères")
    typer.echo(
        "modèle          : "
        + (MODELE if MODELE else "NON CHOISI → valider via `modeles` d'abord")
    )
    try:
        cle_api()
        typer.echo("clé d'API       : présente dans l'environnement")
    except ErreurJuge:
        typer.echo("clé d'API       : ABSENTE → jalon R-b non exécutable")
        raise
    client_openai()
    typer.echo("SDK openai      : importable, client construit")
    if MODELE:
        typer.echo("→ prêt pour R-b.")
    else:
        typer.echo("→ clé et SDK OK ; reste à VALIDER le modèle avant tout appel.")


def main() -> int:
    try:
        app()
    except ErreurJuge as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
