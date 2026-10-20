"""Le contrat OpenAPI : le fichier versionné égale le contrat généré (motif du 02)."""

from __future__ import annotations

import json

from openapi_spec_validator import OpenAPIV31SpecValidator

import api_modele
from api_modele.contrat import DESTINATION, contrat, exporter, serialiser

ROUTES_PROTEGEES = {"/v1/info"}
ROUTES_OUVERTES = {"/v1/sante", "/v1/openapi.json"}


def test_le_contrat_versionne_correspond_au_schema_genere():
    versionne = DESTINATION.read_text(encoding="utf-8")
    attendu = serialiser(contrat())

    assert json.loads(versionne) == json.loads(attendu), (
        "contrat périmé : uv run python -m api_modele.contrat"
    )
    assert versionne == attendu, (
        "forme du fichier : uv run python -m api_modele.contrat"
    )


def test_le_contrat_est_un_openapi_3_1_valide():
    OpenAPIV31SpecValidator(contrat()).validate()


def test_les_routes_du_contrat_sont_sous_v1():
    chemins = set(contrat()["paths"])

    assert chemins == {"/v1/sante", "/v1/info"}
    assert all(c.startswith("/v1/") for c in chemins)


def test_un_seul_schema_de_securite_la_cle_en_en_tete():
    document = contrat()
    schemas = document["components"]["securitySchemes"]

    assert schemas == {
        "APIKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "Clé d'accès du consommateur, une par application.",
        }
    }
    for chemin, operations in document["paths"].items():
        protegee = any("security" in op for op in operations.values())
        assert protegee == (chemin in ROUTES_PROTEGEES), chemin


def test_les_erreurs_des_routes_protegees_sont_declarees():
    reponses = contrat()["paths"]["/v1/info"]["get"]["responses"]

    for statut in ("401", "429"):
        schema = reponses[statut]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/Erreur"}


def test_la_sante_declare_son_503():
    reponses = contrat()["paths"]["/v1/sante"]["get"]["responses"]

    assert set(reponses) == {"200", "503"}


def test_la_version_du_contrat_est_celle_du_paquet():
    assert contrat()["info"]["version"] == api_modele.__version__


def test_le_contrat_est_servi_sans_cle(api):
    r = api.get("/v1/openapi.json")

    assert r.status_code == 200
    assert r.json() == contrat()


def test_exporter_ecrit_le_contrat(tmp_path):
    fichier = exporter(tmp_path / "openapi.json")

    assert fichier.read_text(encoding="utf-8") == serialiser(contrat())
