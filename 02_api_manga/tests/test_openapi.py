"""Le schéma OpenAPI est un livrable : ce test le parcourt et le vérifie.

Le critère 2 de la certification porte sur des règles d'accès DOCUMENTÉES.
Un `/docs` laissé ouvert par inadvertance ne le satisfait pas : encore
faut-il que l'ouverture soit une décision inscrite dans le contrat. D'où un
test qui affirme, route par route, où l'autorisation s'applique et où elle ne
s'applique pas.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from app import API_VERSION
from app.main import create_app
from app.security import API_KEY_HEADER_NAME
from app.settings import ApiKey, Settings

# Le contrat, écrit ici plutôt que déduit du code : un test qui relirait le code
# pour en déduire l'attendu ne vérifierait rien.
ROUTES_PROTEGEES = {
    "/kitsu/{kitsu_id}",
    "/rag/preview",
    "/rag/export",
    "/rag/export/composition",
    "/rag/doc/{doc_key}",
    "/search",
    # 3b — catalogue, identité, couverture. Protégées comme les autres : la
    # règle d'accès n'a pas de variante par famille de ressource.
    "/series/{series_id}",
    "/series/{series_id}/volumes",
    "/series/{series_id}/reviews",
    "/identity/{work_uid}",
    "/coverage",
}
ROUTES_OUVERTES = {"/live", "/health"}

# Toute route qui interroge PostgreSQL peut renvoyer 503.
ROUTES_BASE_DE_DONNEES = ROUTES_PROTEGEES | {"/health"}

# Les routes qui lèvent un 404 dans le code. `/coverage` n'y figure pas :
# une mesure d'ensemble n'a pas d'identifiant à ne pas trouver.
ROUTES_404 = {
    "/kitsu/{kitsu_id}",
    "/rag/doc/{doc_key}",
    "/series/{series_id}",
    "/series/{series_id}/volumes",
    "/series/{series_id}/reviews",
    "/identity/{work_uid}",
}

# Routes portant des paramètres validés : FastAPI y génère un 422.
ROUTES_422 = {
    "/kitsu/{kitsu_id}",
    "/rag/preview",
    "/rag/export",
    "/rag/doc/{doc_key}",
    "/search",
    # 3b : `series_id` et `work_uid` sont bornés par `Path(ge=1)`, les
    # collections par `Query`. `/coverage` ne prend aucun paramètre et n'a
    # donc pas de 422 à déclarer.
    "/series/{series_id}",
    "/series/{series_id}/volumes",
    "/series/{series_id}/reviews",
    "/identity/{work_uid}",
}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    settings = Settings(api_keys=(ApiKey(label="app_backend", secret="x" * 40),))
    return create_app(settings).openapi()


def operations(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {path: operations["get"] for path, operations in schema["paths"].items()}


def test_le_schema_declare_le_mecanisme_d_autorisation(
    schema: dict[str, Any],
) -> None:
    schemes = schema["components"]["securitySchemes"]

    assert len(schemes) == 1
    (declaration,) = schemes.values()
    assert declaration["type"] == "apiKey"
    assert declaration["in"] == "header"
    assert declaration["name"] == API_KEY_HEADER_NAME


def test_toutes_les_routes_attendues_sont_publiees(schema: dict[str, Any]) -> None:
    assert set(schema["paths"]) == ROUTES_PROTEGEES | ROUTES_OUVERTES


def test_l_autorisation_porte_sur_exactement_les_routes_de_donnees(
    schema: dict[str, Any],
) -> None:
    """Exactement : ni une de moins (fuite), ni une de plus (sonde cassée)."""
    protegees = {
        path
        for path, operation in operations(schema).items()
        if operation.get("security")
    }

    assert protegees == ROUTES_PROTEGEES


def test_les_sondes_ne_portent_aucune_exigence_de_securite(
    schema: dict[str, Any],
) -> None:
    for path in ROUTES_OUVERTES:
        assert "security" not in operations(schema)[path], path


@pytest.mark.parametrize("champ", ["tags", "summary"])
def test_chaque_route_est_redigee(schema: dict[str, Any], champ: str) -> None:
    for path, operation in operations(schema).items():
        assert operation.get(champ), f"{path} n'a pas de {champ}"


def test_les_reponses_d_erreur_sont_declarees(schema: dict[str, Any]) -> None:
    for path, operation in operations(schema).items():
        codes = set(operation["responses"])
        attendus = {"200"}
        if path in ROUTES_PROTEGEES:
            attendus.add("401")
        if path in ROUTES_BASE_DE_DONNEES:
            attendus.add("503")
        if path in ROUTES_404:
            attendus.add("404")
        if path in ROUTES_422:
            attendus.add("422")

        assert codes == attendus, path


def test_l_application_est_titree_decrite_et_versionnee(
    schema: dict[str, Any],
) -> None:
    info = schema["info"]

    assert info["title"]
    assert info["description"].strip()
    assert info["version"] == API_VERSION


def test_les_tags_utilises_sont_tous_documentes(schema: dict[str, Any]) -> None:
    documentes = {tag["name"] for tag in schema["tags"]}
    utilises = {
        tag for operation in operations(schema).values() for tag in operation["tags"]
    }

    assert utilises == documentes


def test_review_url_est_documentee_comme_source_de_la_critique(
    schema: dict[str, Any],
) -> None:
    propriete = schema["components"]["schemas"]["Review"]["properties"]["review_url"]

    assert "published review at the source" in propriete["description"]
    assert "not the author's profile" in propriete["description"]


def test_la_version_du_module_suit_pyproject() -> None:
    """La constante et `pyproject.toml` ne peuvent pas diverger en silence."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    declaree = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
        "version"
    ]

    assert API_VERSION == declaree
