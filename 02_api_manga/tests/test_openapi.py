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


# --------------------------------------------------------------------------
# Conformité au standard, prouvée par un validateur tiers
# --------------------------------------------------------------------------
def test_le_schema_est_conforme_a_openapi_31(schema: dict[str, Any]) -> None:
    """« Généré par FastAPI » n'est pas une preuve de conformité.

    Un générateur peut produire un document que le standard refuse — et le
    refus n'apparaîtrait nulle part, puisque c'est le même générateur qui sert
    `/docs`. Seule une lecture par un validateur INDÉPENDANT tranche. Il est en
    dépendance de dev : l'API ne l'embarque pas en production.
    """
    from openapi_spec_validator import OpenAPIV31SpecValidator

    assert schema["openapi"].startswith("3.1.")

    erreurs = [
        f"{list(e.absolute_path)} : {e.message}"
        for e in OpenAPIV31SpecValidator(schema).iter_errors()
    ]

    assert erreurs == [], "\n".join(erreurs)


# --------------------------------------------------------------------------
# Le contrat versionné
# --------------------------------------------------------------------------
def test_le_contrat_versionne_correspond_au_schema_genere() -> None:
    """`openapi.json` du dépôt EST le schéma servi, pas une copie qui vieillit.

    Sans ce test, le fichier versionné deviendrait une documentation parallèle :
    plausible, diffable, et fausse. Il est régénéré par
    `uv run python outils/exporter_openapi.py`.
    """
    import json

    from outils.exporter_openapi import DESTINATION, contrat, serialiser

    assert DESTINATION.exists(), (
        f"{DESTINATION.name} est absent — le régénérer par "
        "`uv run python outils/exporter_openapi.py`"
    )

    versionne = DESTINATION.read_text(encoding="utf-8")
    attendu = serialiser(contrat())

    assert json.loads(versionne) == json.loads(attendu), (
        f"{DESTINATION.name} a dérivé du schéma généré — le régénérer par "
        "`uv run python outils/exporter_openapi.py`"
    )
    assert versionne == attendu, (
        f"{DESTINATION.name} a le bon contenu mais pas la forme stable "
        "(clés triées, indentation 2) — le régénérer."
    )


# --------------------------------------------------------------------------
# La règle d'accès écrite COMME une règle
# --------------------------------------------------------------------------
# Routes servies par FastAPI lui-même : elles ne sont pas dans le schéma (un
# contrat ne se décrit pas dans son propre contrat), mais la règle d'accès doit
# les couvrir — ce sont elles que « la documentation est ouverte » désigne.
ROUTES_DE_DOCUMENTATION = {"/docs", "/redoc", "/openapi.json"}


def regle_du_readme() -> dict[str, bool]:
    """Le tableau d'autorisation du README, relu comme une donnée.

    Le critère 2 porte sur des règles DOCUMENTÉES. Un test qui ne lirait que le
    code vérifierait le comportement et raterait le critère : c'est le texte
    qui doit être exact, et c'est donc le texte qu'on relit.
    """
    import re

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(
        encoding="utf-8"
    )
    dans_le_tableau = re.search(
        r"^\| Routes \| Clé exigée \| Pourquoi \|\n\|[-| ]+\|\n((?:\|.*\n)+)",
        readme,
        re.MULTILINE,
    )
    assert dans_le_tableau, "le tableau d'autorisation est introuvable dans le README"

    regle: dict[str, bool] = {}
    for ligne in dans_le_tableau.group(1).strip().splitlines():
        colonnes = [c.strip() for c in ligne.strip().strip("|").split("|")]
        exigee = colonnes[1].strip("*") == "oui"
        for chemin in re.findall(r"`([^`]+)`", colonnes[0]):
            regle[chemin] = exigee
    return regle


def test_le_readme_enonce_la_regle_d_acces_route_par_route(
    schema: dict[str, Any],
) -> None:
    """Le tableau du README couvre TOUTES les routes, et dit vrai sur chacune.

    C'est ce test qui aurait dû tomber quand les cinq routes de catalogue sont
    arrivées : elles étaient protégées dans le code, absentes de la règle
    écrite. Le comportement était bon, la documentation ne l'était pas — et le
    critère porte sur la documentation.
    """
    regle = regle_du_readme()

    assert set(regle) == set(schema["paths"]) | ROUTES_DE_DOCUMENTATION

    for chemin, operation in operations(schema).items():
        protegee_dans_le_schema = bool(operation.get("security"))
        assert regle[chemin] == protegee_dans_le_schema, (
            f"le README dit « clé exigée : "
            f"{'oui' if regle[chemin] else 'non'} » pour {chemin}, "
            f"le schéma dit le contraire"
        )
    for chemin in ROUTES_DE_DOCUMENTATION:
        assert regle[chemin] is False, f"{chemin} doit rester ouverte"


def test_le_readme_couvre_les_quatre_regles_d_acces() -> None:
    """Les quatre points que le critère 2 exige d'écrire, et leur ancre."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(
        encoding="utf-8"
    )

    for ancre in (
        "la documentation est ouverte, les données",  # ce qui est protégé, et pourquoi
        "Le client final ne détient aucune clé",  # qui détient une clé
        "### Obtenir et faire tourner une clé",  # obtention et rotation
        "### Défaillance fermée",  # démarrage sans clé
    ):
        assert ancre in readme, f"le README ne couvre plus : {ancre}"
