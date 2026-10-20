"""Le versionnage par l'adresse (spec 04, A.3 ; règle du contrat, B.6).

`/v1/` répond ; `/v2/` n'existe pas. `/v1/` ne reçoit que des ajouts compatibles : un
champ ajouté à une réponse ne casse pas un client écrit sur le contrat v1 qui ignore
les champs inconnus — c'est ce que vérifie le test du même nom, sur une vraie
réponse enrichie. L'entrée, elle, reste fermée : un champ inconnu est refusé (le
client ne doit pas croire qu'il règle ce qu'il ne règle pas).
"""

from __future__ import annotations

import copy
import json

import pytest
from conftest import QUESTION, entetes
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict

from api_modele.configuration import ConfigurationInvalide, charger
from api_modele.contrat import DESTINATION

CONTRAT = json.loads(DESTINATION.read_text(encoding="utf-8"))
SCHEMAS = CONTRAT["components"]["schemas"]


# Un client écrit sur le contrat v1 : il lit les champs qu'il connaît, et ignore les
# autres (le défaut de Pydantic, écrit ici pour être lu).
class ClientPassage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fragment_id: int
    extrait: str


class ClientSerie(BaseModel):
    model_config = ConfigDict(extra="ignore")
    rang: int
    entite: str
    titre: str
    score: float
    passages: list[ClientPassage]


class ClientContexte(BaseModel):
    model_config = ConfigDict(extra="ignore")
    corpus_id: str
    encodage_id: int
    version_api: str


class ClientResultat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    series: list[ClientSerie]
    contexte: ClientContexte


def test_v1_repond(api):
    assert api.get("/v1/sante").status_code == 200
    assert (
        api.post(
            "/v1/recherche", json={"question": QUESTION}, headers=entetes()
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    ("methode", "chemin"),
    [
        ("GET", "/v2/sante"),
        ("GET", "/v2/info"),
        ("POST", "/v2/recherche"),
        ("GET", "/v2/openapi.json"),
        ("POST", "/recherche"),
    ],
)
def test_v2_et_l_adresse_sans_version_n_existent_pas(api, methode, chemin):
    r = api.request(methode, chemin, json={"question": QUESTION}, headers=entetes())

    assert r.status_code == 404
    assert r.json()["code"] == "route_inconnue"


def test_seule_v1_est_servie():
    environnement = {
        "API_MODELE_CLES": "essai:" + "e" * 32,
        "API_MODELE_DSN": "postgresql://essai@localhost/essai",
        "API_MODELE_VERSION": "v2",
    }
    with pytest.raises(ConfigurationInvalide, match="v2"):
        charger(environnement)


def test_un_champ_ajoute_ne_casse_pas_un_client_qui_ignore_les_champs_inconnus(api):
    r = api.post("/v1/recherche", json={"question": QUESTION}, headers=entetes())
    assert r.status_code == 200
    reponse = r.json()
    # Une version 1.x de l'API qui aurait ajouté un champ à chaque niveau.
    enrichie = copy.deepcopy(reponse)
    enrichie["suggestions"] = ["un champ ajouté"]
    enrichie["series"][0]["annee"] = 1997
    enrichie["series"][0]["passages"][0]["langue"] = "fr"
    enrichie["contexte"]["modele_de_langage"] = None

    # Le contrat v1 versionné admet toujours la réponse enrichie : ses sorties ne
    # ferment pas leurs champs.
    schema = {
        "$ref": "#/components/schemas/Resultat",
        "components": CONTRAT["components"],
    }
    Draft202012Validator(schema).validate(enrichie)
    # Et le client v1 y lit exactement ce qu'il lisait.
    assert ClientResultat.model_validate(enrichie) == ClientResultat.model_validate(
        reponse
    )


@pytest.mark.parametrize(
    "nom",
    ["Resultat", "Serie", "Passage", "Contexte", "ReglagesAppliques", "Info", "Sante"],
)
def test_les_sorties_du_contrat_ne_ferment_pas_leurs_champs(nom):
    assert SCHEMAS[nom].get("additionalProperties") is not False


def test_l_entree_reste_fermee(api):
    assert SCHEMAS["Recherche"]["additionalProperties"] is False
    r = api.post(
        "/v1/recherche",
        json={"question": QUESTION, "encodage_id": 2},
        headers=entetes(),
    )
    assert r.status_code == 422
    assert r.json()["details"]["champs"] == [
        {"champ": "encodage_id", "raison": "inconnu"}
    ]
