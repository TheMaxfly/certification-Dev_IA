"""Le contrat, route par route et code par code (spec 04, A.2).

Chaque statut que le contrat versionné (`contrat/openapi_v1.json`) déclare pour une
opération est produit ici par un scénario, et la réponse a la forme déclarée : son
corps est validé contre le schéma du contrat. Un statut déclaré sans scénario, ou un
scénario hors du contrat, fait échouer `test_chaque_statut_declare_a_son_scenario`.

L'inverse — aucun statut non déclaré n'est produit — est tenu pour toutes les
requêtes de la suite par la fixture `aucun_statut_hors_contrat` (conftest.py).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from conftest import QUESTION, ServiceDoublure, entetes, statuts_hors_contrat
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from api_modele.contrat import DESTINATION
from api_modele.erreurs import ENTETE_IDENTIFIANT

CONTRAT = json.loads(DESTINATION.read_text(encoding="utf-8"))
DECLARES = sorted(
    (chemin, methode.upper(), int(statut))
    for chemin, operations in CONTRAT["paths"].items()
    for methode, operation in operations.items()
    for statut in operation["responses"]
)

type Scenario = Callable[[TestClient, ServiceDoublure], httpx.Response]


def epuiser_le_debit(api: TestClient) -> None:
    """La fenêtre de la clé `application` remplie : la requête suivante est de trop."""
    etat = api.app.state.etat  # type: ignore[attr-defined]
    for _ in range(etat.reglages.debit_par_minute):
        assert etat.limiteur.admettre("application") is None


def sante_200(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    return api.get("/v1/sante")


def sante_503(api: TestClient, service: ServiceDoublure) -> httpx.Response:
    service.arreter()
    return api.get("/v1/sante")


def info_200(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    return api.get("/v1/info", headers=entetes())


def info_401(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    return api.get("/v1/info")


def info_429(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    epuiser_le_debit(api)
    return api.get("/v1/info", headers=entetes())


def rechercher(
    api: TestClient, corps: object = None, **options: object
) -> httpx.Response:
    corps = {"question": QUESTION} if corps is None else corps
    return api.post("/v1/recherche", json=corps, **options)  # type: ignore[arg-type]


def recherche_200(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    return rechercher(api, headers=entetes())


def recherche_401(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    return rechercher(api)


def recherche_413(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    return api.post(
        "/v1/recherche",
        content=b'{"question": "' + b"x" * 16400 + b'"}',
        headers=entetes() | {"Content-Type": "application/json"},
    )


def recherche_422(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    return rechercher(
        api, {"question": QUESTION, "nombre_series": 51}, headers=entetes()
    )


def recherche_429(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    epuiser_le_debit(api)
    return rechercher(api, headers=entetes())


def recherche_503(api: TestClient, service: ServiceDoublure) -> httpx.Response:
    service.arreter()
    return rechercher(api, headers=entetes())


# La route de réponse : sur l'API à profils factices (`api_reponse`), doublures du
# bouclage seulement.
def repondre(
    api: TestClient, corps: object = None, **options: object
) -> httpx.Response:
    corps = {"question": QUESTION} if corps is None else corps
    return api.post("/v1/reponse", json=corps, **options)  # type: ignore[arg-type]


def reponse_200(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    return repondre(api, headers=entetes())


def reponse_401(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    return repondre(api)


def reponse_413(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    return api.post(
        "/v1/reponse",
        content=b'{"question": "' + b"x" * 16400 + b'"}',
        headers=entetes() | {"Content-Type": "application/json"},
    )


def reponse_422(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    return repondre(api, {"question": QUESTION, "temperature": 1.5}, headers=entetes())


def reponse_429(api: TestClient, _: ServiceDoublure) -> httpx.Response:
    epuiser_le_debit(api)
    return repondre(api, headers=entetes())


def reponse_503(api: TestClient, service: ServiceDoublure) -> httpx.Response:
    service.arreter()
    return repondre(api, headers=entetes())


SCENARIOS: dict[tuple[str, str, int], tuple[Scenario, str | None]] = {
    # (route, méthode, statut) → (scénario, code d'erreur attendu)
    ("/v1/sante", "GET", 200): (sante_200, None),
    ("/v1/sante", "GET", 503): (sante_503, None),
    ("/v1/info", "GET", 200): (info_200, None),
    ("/v1/info", "GET", 401): (info_401, "cle_invalide"),
    ("/v1/info", "GET", 429): (info_429, "debit_depasse"),
    ("/v1/recherche", "POST", 200): (recherche_200, None),
    ("/v1/recherche", "POST", 401): (recherche_401, "cle_invalide"),
    ("/v1/recherche", "POST", 413): (recherche_413, "corps_trop_volumineux"),
    ("/v1/recherche", "POST", 422): (recherche_422, "entree_invalide"),
    ("/v1/recherche", "POST", 429): (recherche_429, "debit_depasse"),
    ("/v1/recherche", "POST", 503): (recherche_503, "service_embedding_indisponible"),
    ("/v1/reponse", "POST", 200): (reponse_200, None),
    ("/v1/reponse", "POST", 401): (reponse_401, "cle_invalide"),
    ("/v1/reponse", "POST", 413): (reponse_413, "corps_trop_volumineux"),
    ("/v1/reponse", "POST", 422): (reponse_422, "entree_invalide"),
    ("/v1/reponse", "POST", 429): (reponse_429, "debit_depasse"),
    ("/v1/reponse", "POST", 503): (reponse_503, "service_embedding_indisponible"),
}


def schema_declare(chemin: str, methode: str, statut: int) -> dict[str, object]:
    reponse = CONTRAT["paths"][chemin][methode.lower()]["responses"][str(statut)]
    return reponse["content"]["application/json"]["schema"]


def test_chaque_statut_declare_a_son_scenario():
    assert sorted(SCENARIOS) == DECLARES


def test_le_contrat_declare_dix_sept_statuts_sur_quatre_routes():
    """Garde-fou de lecture : le contrat versionné n'a pas perdu d'opération."""
    assert len(DECLARES) == 17
    assert {(c, m) for c, m, _ in DECLARES} == {
        ("/v1/sante", "GET"),
        ("/v1/info", "GET"),
        ("/v1/recherche", "POST"),
        ("/v1/reponse", "POST"),
    }


def test_le_garde_fou_signale_un_statut_hors_contrat_et_lui_seul():
    vues = [
        ("/v1/info", "GET", 200),
        ("/v1/info", "GET", 500),  # une opération du contrat, un statut non déclaré
        ("/v1/sante", "DELETE", 405),  # une méthode non permise : hors contrat
        ("inconnue", "POST", 413),  # refusé avant le routage
        ("/v1/essai_panne", "GET", 500),  # une route d'essai
    ]

    assert statuts_hors_contrat(vues) == [("/v1/info", "GET", 500)]


@pytest.mark.parametrize(("chemin", "methode", "statut"), DECLARES)
def test_chaque_statut_declare_est_produit_dans_sa_forme(
    request: pytest.FixtureRequest,
    service_doublure: ServiceDoublure,
    chemin: str,
    methode: str,
    statut: int,
):
    scenario, code = SCENARIOS[chemin, methode, statut]
    # La route de réponse sur les profils factices ; les autres, sur l'API versionnée.
    api = request.getfixturevalue("api_reponse" if chemin == "/v1/reponse" else "api")
    r = scenario(api, service_doublure)

    assert r.status_code == statut, r.text
    # Le corps a la forme que le contrat déclare pour ce statut ; le schéma du
    # contrat est lu tel quel (ses `$ref` résolus dans le document).
    schema = {
        **schema_declare(chemin, methode, statut),
        "components": CONTRAT["components"],
    }
    Draft202012Validator(schema).validate(r.json())
    if code is not None:
        assert r.json()["code"] == code
    assert r.headers[ENTETE_IDENTIFIANT]
    assert r.headers["Cache-Control"] == "no-store"
