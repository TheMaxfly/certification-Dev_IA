"""Le modèle d'erreur unique : `code`, `message`, `details`, `identifiant_requete`.

Un code stable par cas (contrat de l'étape 2, B.5). Les erreurs de validation, les
routes et méthodes inconnues et les erreurs de l'API passent par les gestionnaires
installés ici ; l'erreur imprévue (500) par l'enveloppe (`enveloppe.py`). Jamais de
trace de pile dans une réponse.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

ENTETE_IDENTIFIANT = "X-Identifiant-Requete"


class ErreurApi(Exception):
    """Une erreur prévue par le contrat : son statut, son code, son message."""

    def __init__(
        self,
        statut: int,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
        entetes: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.statut = statut
        self.code = code
        self.message = message
        self.details = dict(details) if details is not None else None
        self.entetes = dict(entetes or {})


class Erreur(BaseModel):
    """La forme de toute erreur de l'API."""

    code: str
    message: str
    details: dict[str, Any] | None
    identifiant_requete: str


def bornes_de(request: Request) -> dict[str, tuple[int, int]]:
    """Les bornes des réglages connus, posées par l'application à sa création."""
    bornes: object = getattr(request.app.state, "bornes", None)
    return cast(dict[str, tuple[int, int]], bornes) if isinstance(bornes, dict) else {}


def identifiant(request: Request) -> str:
    return str(getattr(request.state, "identifiant_requete", ""))


def reponse(
    request: Request,
    statut: int,
    code: str,
    message: str,
    details: Mapping[str, object] | None = None,
    entetes: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Une réponse d'erreur ; son code est noté pour le journal et les métriques."""
    request.state.code_erreur = code
    corps = Erreur(
        code=code,
        message=message,
        details=dict(details) if details is not None else None,
        identifiant_requete=identifiant(request),
    )
    return JSONResponse(corps.model_dump(), status_code=statut, headers=entetes)


#: Le type d'une erreur de Pydantic → la raison, stable, rendue au client.
RAISONS = {
    "missing": "manquant",
    "extra_forbidden": "inconnu",
    "greater_than_equal": "hors_bornes",
    "less_than_equal": "hors_bornes",
    "greater_than": "hors_bornes",
    "less_than": "hors_bornes",
    "string_too_short": "hors_bornes",
    "string_too_long": "hors_bornes",
    "int_type": "type",
    "bool_type": "type",
    "string_type": "type",
    "dict_type": "type",
    "model_attributes_type": "type",
    "json_invalid": "json_invalide",
    "string_pattern_mismatch": "forme",
    "vide": "vide",
}
#: Les clés du contexte de Pydantic qui disent une borne.
BORNES = (("ge", "le"), ("gt", "lt"), ("min_length", "max_length"))


def champs(
    erreurs: Sequence[Mapping[str, Any]],
    bornes_connues: Mapping[str, tuple[int, int]] | None = None,
) -> list[dict[str, object]]:
    """Les champs fautifs, dans l'ordre : `champ`, `raison`, et `bornes` s'il y en a.

    Pydantic ne dit que la borne franchie ; pour un réglage connu (configuration),
    les deux bornes sont rendues."""
    connues = bornes_connues or {}
    resultat: list[dict[str, object]] = []
    for erreur in erreurs:
        chemin = [str(p) for p in erreur.get("loc", ()) if p != "body"]
        element: dict[str, object] = {
            "champ": ".".join(chemin) or "corps",
            "raison": RAISONS.get(str(erreur.get("type", "")), "invalide"),
        }
        contexte = cast(Mapping[str, Any], erreur.get("ctx") or {})
        for bas, haut in BORNES:
            if bas in contexte or haut in contexte:
                element["bornes"] = [contexte.get(bas), contexte.get(haut)]
        champ = str(element["champ"])
        if element["raison"] == "hors_bornes" and champ in connues:
            element["bornes"] = list(connues[champ])
        resultat.append(element)
    return resultat


async def _erreur_api(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ErreurApi):  # garanti par l'enregistrement
        raise exc
    return reponse(request, exc.statut, exc.code, exc.message, exc.details, exc.entetes)


async def _validation(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    erreurs = list(exc.errors())
    for erreur in erreurs:
        # La question trop longue a son code : avant tout appel au service.
        if (
            tuple(erreur.get("loc", ())) == ("body", "question")
            and erreur.get("type") == "string_too_long"
        ):
            contexte = cast(Mapping[str, Any], erreur.get("ctx") or {})
            maximum = contexte.get("max_length")
            return reponse(
                request,
                422,
                "question_trop_longue",
                f"La question dépasse {maximum} caractères.",
                {
                    "longueur": len(str(erreur.get("input", ""))),
                    "maximum": maximum,
                    "unite": "caracteres",
                },
            )
    return reponse(
        request,
        422,
        "entree_invalide",
        "Entrée invalide.",
        {"champs": champs(erreurs, bornes_de(request))},
    )


async def _http(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    if exc.status_code == 404:
        return reponse(request, 404, "route_inconnue", "Route inconnue.")
    if exc.status_code == 405:
        return reponse(
            request,
            405,
            "methode_non_permise",
            "Méthode non permise.",
            entetes=exc.headers,
        )
    return reponse(request, exc.status_code, "erreur_http", str(exc.detail))


def installer(app: FastAPI) -> None:
    app.add_exception_handler(ErreurApi, _erreur_api)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(StarletteHTTPException, _http)
