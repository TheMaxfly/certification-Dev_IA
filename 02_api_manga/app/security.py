"""Autorisation par clé d'API : dépendance FastAPI et journalisation."""

from __future__ import annotations

import logging
from secrets import compare_digest
from typing import Annotated

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from .settings import ApiKey

LOGGER = logging.getLogger(__name__)

API_KEY_HEADER_NAME = "X-API-Key"

# Message unique aux deux refus. Distinguer « en-tête absent » de « clé
# invalide » dans la réponse offrirait un oracle : un appelant saurait que sa
# clé a été lue et rejetée, donc qu'elle est de la bonne forme. Le journal, lui,
# distingue les deux — il n'est pas lisible par l'appelant.
UNAUTHORIZED_DETAIL = "invalid or missing API key"

# `auto_error=False` : laissé à True, FastAPI répondrait 403 sur en-tête absent
# et 200 sinon, sans jamais consulter le trousseau. Le contrat du module exige
# 401 dans les deux cas de refus.
api_key_header = APIKeyHeader(
    name=API_KEY_HEADER_NAME,
    auto_error=False,
    description="Consumer API key.",
)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=UNAUTHORIZED_DETAIL,
    )


def resolve_consumer(api_keys: tuple[ApiKey, ...], presented: str) -> str | None:
    """Renvoie le libellé du consommateur, ou `None` si la clé est inconnue.

    Deux précautions, pour deux fuites différentes :

    - `compare_digest` plutôt que `==` protège CHAQUE comparaison : `==` sort au
      premier octet différent, ce qui laisse deviner un préfixe.
    - l'absence de `break` protège le TROUSSEAU : court-circuiter sur la
      première correspondance ferait dépendre la durée de réponse du rang de la
      clé dans la liste.

    Comparaison sur des octets : `compare_digest` refuse les `str` non-ASCII et
    lèverait `TypeError` sur un en-tête accentué — soit une 500 déclenchable
    depuis l'extérieur.
    """
    presented_bytes = presented.encode("utf-8")
    label: str | None = None
    for entry in api_keys:
        if compare_digest(presented_bytes, entry.secret.encode("utf-8")):
            label = entry.label
    return label


def require_api_key(
    request: Request,
    presented: Annotated[str | None, Security(api_key_header)],
) -> str:
    """Autorise l'appel et renvoie le libellé du consommateur.

    Le journal ne porte JAMAIS la clé — le libellé du consommateur quand il est
    connu, rien quand il ne l'est pas. Une clé rejetée reste une clé : la
    journaliser la publierait à quiconque lit les journaux, et une clé valide
    présentée à la mauvaise route s'y retrouverait en clair.
    """
    settings = request.app.state.settings

    if presented is None:
        LOGGER.warning(
            "appel refusé sur %s : en-tête %s absent",
            request.url.path,
            API_KEY_HEADER_NAME,
        )
        raise _unauthorized()

    label = resolve_consumer(settings.api_keys, presented)
    if label is None:
        LOGGER.warning("appel refusé sur %s : clé d'API inconnue", request.url.path)
        raise _unauthorized()

    LOGGER.info("appel autorisé sur %s, consommateur « %s »", request.url.path, label)
    return label
