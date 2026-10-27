"""Le contrat OpenAPI : le fichier versionné égale le contrat généré (motif du 02)."""

from __future__ import annotations

import copy
import json

from jsonschema import Draft202012Validator
from openapi_spec_validator import OpenAPIV31SpecValidator

import api_modele
from api_modele.contrat import DESTINATION, contrat, exporter, serialiser

ROUTES_PROTEGEES = {"/v1/info", "/v1/recherche", "/v1/reponse"}
#: Le contrat publié à la livraison 1.0.0 (étiquette `api-modele`), figé à côté du
#: contrat courant.
REFERENCE_1_0_0 = DESTINATION.parent / "openapi_v1.0.0.json"
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

    assert chemins == {"/v1/sante", "/v1/info", "/v1/recherche", "/v1/reponse"}
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
    chemins = contrat()["paths"]
    attendus = {
        ("/v1/info", "get"): ("401", "429"),
        ("/v1/recherche", "post"): ("401", "413", "422", "429", "503"),
        ("/v1/reponse", "post"): ("401", "413", "422", "429", "503"),
    }
    for (chemin, methode), statuts in attendus.items():
        reponses = chemins[chemin][methode]["responses"]
        for statut in statuts:
            schema = reponses[statut]["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/Erreur"}, (chemin, statut)


def test_le_422_natif_de_fastapi_n_apparait_pas():
    """La forme d'erreur unique : pas de `HTTPValidationError` (la dette du 02)."""
    document = contrat()

    assert "HTTPValidationError" not in document["components"]["schemas"]


def test_l_entree_de_la_recherche_porte_les_bornes_du_contrat():
    schema = contrat()["components"]["schemas"]["Recherche"]
    proprietes = schema["properties"]

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["question"]
    assert proprietes["question"]["maxLength"] == 500
    nombre = proprietes["nombre_series"]
    assert (nombre["default"], nombre["minimum"], nombre["maximum"]) == (10, 1, 50)
    assert proprietes["passages_par_serie"]["default"] == 1
    assert proprietes["passages_par_serie"]["maximum"] == 5
    assert proprietes["catalogue_seul"]["default"] is True


def test_la_sante_declare_son_503():
    reponses = contrat()["paths"]["/v1/sante"]["get"]["responses"]

    assert set(reponses) == {"200", "503"}


def test_l_exemple_du_503_de_la_sante_est_un_vrai_503(api):
    """Dette de l'étape 2 soldée : l'exemple de `/v1/docs` est celui que l'API rend
    quand la base ne répond pas — valide contre le schéma `Sante`, et égal."""
    document = contrat()
    reponse = document["paths"]["/v1/sante"]["get"]["responses"]["503"]
    exemple = reponse["content"]["application/json"]["example"]
    Draft202012Validator(
        {"$ref": "#/components/schemas/Sante", "components": document["components"]}
    ).validate(exemple)
    api.app.state.etat.pool.close()  # type: ignore[attr-defined]

    r = api.get("/v1/sante")

    assert r.status_code == 503
    assert r.json() == exemple


def test_la_version_du_contrat_est_celle_du_paquet():
    assert contrat()["info"]["version"] == api_modele.__version__


def test_le_contrat_est_servi_sans_cle(api):
    r = api.get("/v1/openapi.json")

    assert r.status_code == 200
    assert r.json() == contrat()


def test_exporter_ecrit_le_contrat(tmp_path):
    fichier = exporter(tmp_path / "openapi_v1.json")

    assert fichier.read_text(encoding="utf-8") == serialiser(contrat())


def test_depuis_la_1_0_0_le_contrat_ne_recoit_que_des_ajouts():
    """Chaque route, schéma et champ du contrat 1.0.0 est inchangé ; seul `Info`
    gagne un champ (`modele_langage`) ; la route de réponse, ses schémas et l'exemple
    du 503 de `/v1/sante` s'ajoutent ; la version passe à 1.1.0 (mineure)."""
    avant = json.loads(REFERENCE_1_0_0.read_text(encoding="utf-8"))
    apres = contrat()

    assert (avant["info"]["version"], apres["info"]["version"]) == ("1.0.0", "1.1.0")
    assert apres["info"]["title"] == avant["info"]["title"]
    assert apres["info"]["description"].startswith(avant["info"]["description"])
    assert (
        apres["components"]["securitySchemes"]
        == (avant["components"]["securitySchemes"])
    )
    for chemin, operations in avant["paths"].items():
        actuelles = copy.deepcopy(apres["paths"][chemin])
        if chemin == "/v1/sante":  # l'exemple ajouté au 503, rien d'autre
            contenu = actuelles["get"]["responses"]["503"]["content"]
            assert contenu["application/json"].pop("example")["statut"] == "degrade"
        assert actuelles == operations, chemin
    assert set(apres["paths"]) - set(avant["paths"]) == {"/v1/reponse"}
    schemas = apres["components"]["schemas"]
    for nom, schema in avant["components"]["schemas"].items():
        if nom != "Info":
            assert schemas[nom] == schema, nom
            continue
        info = schemas["Info"]
        assert set(info["properties"]) - set(schema["properties"]) == {"modele_langage"}
        for champ, forme in schema["properties"].items():
            assert info["properties"][champ] == forme, champ
        assert set(schema["required"]) <= set(info["required"])
        reste = {k: v for k, v in info.items() if k not in ("properties", "required")}
        assert reste == {
            k: v for k, v in schema.items() if k not in ("properties", "required")
        }
    nouveaux = set(schemas) - set(avant["components"]["schemas"])
    assert nouveaux == {
        "AppelModele",
        "BorneReelle",
        "ContexteReponse",
        "DemandeReponse",
        "HistoriqueDecrit",
        "Latences",
        "ModeDecrit",
        "ModeleLangage",
        "ProfilDecrit",
        "ReglagesReponse",
        "ReglagesReponseAppliques",
        "Reponse",
        "SerieCitee",
        "Tour",
    }


def test_la_reference_1_0_0_est_le_contrat_livre():
    """Le figé ne bouge pas : son empreinte est celle du fichier livré à 1.0.0."""
    import hashlib

    empreinte = hashlib.sha256(REFERENCE_1_0_0.read_bytes()).hexdigest()
    assert empreinte.startswith("b3ee377d1492e7e2")


def resoudre(document: dict, noeud: dict) -> dict:
    """Un schéma, ses `$ref` locaux (`#/components/…`) suivis depuis la racine."""
    while "$ref" in noeud:
        cible = noeud["$ref"]
        noeud = document
        for partie in cible.removeprefix("#/").split("/"):
            noeud = noeud[partie]
    return noeud


def test_compatibilite_avec_le_contrat_1_0_0():
    """Pour chaque route de 1.0.0 : mêmes codes, mêmes champs d'entrée, et tous les
    champs de sortie de 1.0.0 présents, avec leur forme (spec 04, A.1)."""
    avant = json.loads(REFERENCE_1_0_0.read_text(encoding="utf-8"))
    apres = contrat()

    for chemin, operations in avant["paths"].items():
        for methode, operation in operations.items():
            courante = apres["paths"][chemin][methode]
            assert set(courante["responses"]) == set(operation["responses"]), chemin
            if "requestBody" in operation:
                entree_avant = resoudre(
                    avant,
                    operation["requestBody"]["content"]["application/json"]["schema"],
                )
                entree = resoudre(
                    apres,
                    courante["requestBody"]["content"]["application/json"]["schema"],
                )
                assert entree["properties"] == entree_avant["properties"], chemin
                assert entree.get("required") == entree_avant.get("required")
            for statut, reponse in operation["responses"].items():
                sortie_avant = resoudre(
                    avant, reponse["content"]["application/json"]["schema"]
                )
                sortie = resoudre(
                    apres,
                    courante["responses"][statut]["content"]["application/json"][
                        "schema"
                    ],
                )
                for champ, forme in sortie_avant.get("properties", {}).items():
                    assert champ in sortie["properties"], (chemin, statut, champ)
                    assert sortie["properties"][champ] == forme, (chemin, champ)
