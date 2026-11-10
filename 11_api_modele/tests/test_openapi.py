"""Le contrat OpenAPI : le fichier versionné égale le contrat généré (motif du 02)."""

from __future__ import annotations

import copy
import json

from jsonschema import Draft202012Validator
from openapi_spec_validator import OpenAPIV31SpecValidator

import api_modele
from api_modele.contrat import DESTINATION, contrat, exporter, serialiser

ROUTES_PROTEGEES = {"/v1/info", "/v1/recherche", "/v1/reponse", "/v1/verification"}
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

    assert chemins == {
        "/v1/sante",
        "/v1/info",
        "/v1/recherche",
        "/v1/reponse",
        "/v1/verification",
    }
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
        ("/v1/verification", "post"): ("401", "429"),
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


def test_le_503_de_la_reponse_documente_retry_after_et_lui_seul():
    """Un en-tête documenté, sans champ nouveau ; la recherche n'appelle aucun
    fournisseur : son 503 n'en a pas."""
    chemins = contrat()["paths"]
    reponses = chemins["/v1/reponse"]["post"]["responses"]

    assert set(reponses["503"]["headers"]) == {"Retry-After"}
    entete = reponses["503"]["headers"]["Retry-After"]
    assert entete["schema"] == {"type": "integer", "minimum": 1}
    assert "headers" not in chemins["/v1/recherche"]["post"]["responses"]["503"]


def test_depuis_la_1_0_0_le_contrat_ne_recoit_que_des_ajouts():
    """Chaque route, schéma et champ du contrat 1.0.0 est inchangé ; seuls `Info`
    (`modele_langage`) et `Serie` (`metadonnees`) gagnent un champ ; la route de
    réponse, ses schémas, `Metadonnees` et l'exemple du 503 de `/v1/sante` s'ajoutent ;
    la version est la courante (1.5.0 : la route de vérification des clés s'ajoute)."""
    avant = json.loads(REFERENCE_1_0_0.read_text(encoding="utf-8"))
    apres = contrat()

    assert (avant["info"]["version"], apres["info"]["version"]) == ("1.0.0", "1.5.0")
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
    assert set(apres["paths"]) - set(avant["paths"]) == {
        "/v1/reponse",
        "/v1/verification",
    }
    schemas = apres["components"]["schemas"]
    gagnants = {"Info": "modele_langage", "Serie": "metadonnees"}
    for nom, schema in avant["components"]["schemas"].items():
        if nom not in gagnants:
            assert schemas[nom] == schema, nom
            continue
        actuel = schemas[nom]
        assert set(actuel["properties"]) - set(schema["properties"]) == {gagnants[nom]}
        for champ, forme in schema["properties"].items():
            assert actuel["properties"][champ] == forme, champ
        assert set(schema["required"]) <= set(actuel["required"])
        reste = {k: v for k, v in actuel.items() if k not in ("properties", "required")}
        assert reste == {
            k: v for k, v in schema.items() if k not in ("properties", "required")
        }
    nouveaux = set(schemas) - set(avant["components"]["schemas"])
    assert nouveaux == {
        # 1.5.0 : le test des clés.
        "ControleCle",
        "Verification",
        "AppelModele",
        "BorneReelle",
        "ContexteReponse",
        "DemandeReponse",
        "HistoriqueDecrit",
        "Latences",
        "Metadonnees",
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


REFERENCE_1_3_1 = DESTINATION.parent / "openapi_v1.3.1.json"
#: Le contrat de `develop` à 6b38ba7 (E4, lot 4 fusionné), figé avant la 1.5.0.
REFERENCE_1_4_0 = DESTINATION.parent / "openapi_v1.4.0.json"
EMPREINTE_1_3_1 = "eaaecd704051c7a6"  # le contrat de develop à eae9f43


def test_depuis_la_1_3_1_le_mode_enrichi_n_ajoute_que_des_champs():
    """1.4.0 sur 1.3.1 (figé, `openapi_v1.3.1.json`) : chaque route et chaque schéma
    inchangés, sauf `DemandeReponse.mode` (énumération élargie à `enrichi`, défaut
    `strict`) et `Reponse`, qui gagne trois champs facultatifs ; la description de
    l'API gagne son paragraphe. Lu sur le contrat 1.4.0 figé : la 1.5.0 n'ajoute que sa
    route et ses deux schémas (test suivant)."""
    avant = json.loads(REFERENCE_1_3_1.read_text(encoding="utf-8"))
    apres = json.loads(REFERENCE_1_4_0.read_text(encoding="utf-8"))

    assert (avant["info"]["version"], apres["info"]["version"]) == ("1.3.1", "1.4.0")
    assert apres["info"]["description"].startswith(avant["info"]["description"])
    assert apres["paths"] == avant["paths"]
    schemas, schemas_avant = (
        apres["components"]["schemas"],
        avant["components"]["schemas"],
    )
    assert set(schemas) == set(schemas_avant)
    for nom, schema in schemas_avant.items():
        if nom not in ("DemandeReponse", "Reponse"):
            assert schemas[nom] == schema, nom
    demande = copy.deepcopy(schemas["DemandeReponse"])
    mode = demande["properties"].pop("mode")
    mode_avant = copy.deepcopy(schemas_avant["DemandeReponse"])
    mode_avant_forme = mode_avant["properties"].pop("mode")
    assert demande == mode_avant
    assert (mode_avant_forme["const"], mode["enum"]) == (
        "strict",
        ["strict", "enrichi"],
    )
    assert mode["default"] == mode_avant_forme["default"] == "strict"
    reponse = copy.deepcopy(schemas["Reponse"])
    ajoutes = {
        k: reponse["properties"].pop(k)
        for k in ("complement", "complement_titres", "complement_etiquette")
    }
    reponse.pop("description")
    assert reponse == schemas_avant["Reponse"]
    assert set(ajoutes) & set(schemas["Reponse"]["required"]) == set()


def test_la_reference_1_3_1_est_le_contrat_livre():
    """Le figé ne bouge pas : son empreinte est celle du contrat servi en 1.3.1."""
    import hashlib

    empreinte = hashlib.sha256(REFERENCE_1_3_1.read_bytes()).hexdigest()
    assert empreinte.startswith(EMPREINTE_1_3_1)


def test_depuis_la_1_4_0_la_verification_n_ajoute_qu_une_route_et_deux_schemas():
    """1.5.0 sur 1.4.0 (figé, `openapi_v1.4.0.json`) : chaque route et chaque schéma
    inchangés ; `POST /v1/verification` s'ajoute, avec `Verification` et `ControleCle` ;
    la description gagne son paragraphe ; la version passe à 1.5.0."""
    avant = json.loads(REFERENCE_1_4_0.read_text(encoding="utf-8"))
    apres = contrat()

    assert (avant["info"]["version"], apres["info"]["version"]) == ("1.4.0", "1.5.0")
    assert apres["info"]["description"].startswith(avant["info"]["description"])
    assert set(apres["paths"]) - set(avant["paths"]) == {"/v1/verification"}
    for chemin, operations in avant["paths"].items():
        assert apres["paths"][chemin] == operations, chemin
    schemas, schemas_avant = (
        apres["components"]["schemas"],
        avant["components"]["schemas"],
    )
    assert set(schemas) - set(schemas_avant) == {"Verification", "ControleCle"}
    for nom, schema in schemas_avant.items():
        assert schemas[nom] == schema, nom
    operation = apres["paths"]["/v1/verification"]["post"]
    assert "requestBody" not in operation
    assert set(operation["responses"]) == {"200", "401", "429"}
    assert operation["security"] == [{"APIKeyHeader": []}]


def test_le_controle_d_une_cle_ne_porte_que_des_noms_et_des_etats():
    """Aucun champ de `ControleCle` ne peut porter une valeur de clé : un nom de
    variable, des booléens, un statut."""
    schema = contrat()["components"]["schemas"]["ControleCle"]

    assert set(schema["properties"]) == {
        "nom",
        "fournisseur",
        "modele",
        "variable_cle",
        "configuree",
        "disponible",
        "statut",
    }
    assert schema["properties"]["configuree"]["type"] == "boolean"
    assert schema["properties"]["disponible"]["type"] == "boolean"
