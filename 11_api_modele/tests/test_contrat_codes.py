"""Le contrat, route par route, statut par statut et code par code (spec 04 de
l'étape 2, A.2 ; spec 04 de l'étape 3, A.2).

La matrice est lue dans le contrat versionné (`contrat/openapi_v1.json`), pas écrite à
la main : chaque statut qu'il déclare pour une opération, et, quand la description de
ce statut énumère ses codes d'erreur (entre accents graves), chacun de ces codes. Pour
chaque case, au moins un scénario produit ce statut — et ce code — avec un corps validé
contre le schéma du contrat. Une case sans scénario, ou un scénario hors du contrat,
fait échouer `test_chaque_case_declaree_a_son_scenario`.

L'inverse — aucun statut ni code non déclaré n'est produit — est tenu pour toutes les
requêtes de la suite par la fixture `aucun_statut_hors_contrat` (conftest.py).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace

import httpx
import psycopg
import pytest
from conftest import (
    CODES,
    DELAI_FACTICE_S,
    QUESTION,
    BaseApi,
    Doublure,
    DoublureFournisseur,
    Doublures,
    ServiceDoublure,
    entetes,
    inscrire_encodage,
    promouvoir,
    statuts_hors_contrat,
)
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from api_modele.contrat import DESTINATION
from api_modele.erreurs import ENTETE_IDENTIFIANT

CONTRAT = json.loads(DESTINATION.read_text(encoding="utf-8"))
#: (route, méthode, statut, code) ; code "" : le statut n'énumère pas ses codes.
type Case = tuple[str, str, int, str]
DECLARES: list[Case] = sorted(
    (chemin, methode.upper(), int(statut), code)
    for chemin, operations in CONTRAT["paths"].items()
    for methode, operation in operations.items()
    for statut in operation["responses"]
    for code in sorted(CODES[chemin, methode.upper(), int(statut)]) or [""]
)


@dataclass
class Banc:
    """Ce qu'un scénario peut toucher : l'API, la doublure du service, la base."""

    api: TestClient
    service: ServiceDoublure
    base: BaseApi
    doublures: Doublures | None


type Scenario = Callable[[Banc], httpx.Response]


def epuiser_le_debit(api: TestClient) -> None:
    """La fenêtre de la clé `application` remplie : la requête suivante est de trop."""
    etat = api.app.state.etat  # type: ignore[attr-defined]
    for _ in range(etat.reglages.debit_par_minute):
        assert etat.limiteur.admettre("application") is None


def trop_gros(api: TestClient, route: str) -> httpx.Response:
    return api.post(
        route,
        content=b'{"question": "' + b"x" * 16400 + b'"}',
        headers=entetes() | {"Content-Type": "application/json"},
    )


def poster(banc: Banc, route: str, **champs: object) -> httpx.Response:
    return banc.api.post(
        route, json={"question": QUESTION, **champs}, headers=entetes()
    )


def service_incoherent(banc: Banc) -> None:
    Doublure.refus_embed = 500
    Doublure.info["model_sha"] = "0" * 40


def encodage_change(banc: Banc) -> None:
    with psycopg.connect(banc.base.dsn_admin) as cx:
        autre = inscrire_encodage(cx, banc.service.instance, outil_version="1.9.5")
        promouvoir(cx, autre)


def profil_b_indisponible(banc: Banc) -> None:
    couche = banc.api.app.state.etat.fournisseurs  # type: ignore[attr-defined]
    couche.profils["factice-b"] = replace(couche.profils["factice-b"], disponible=False)


def fournisseurs_arretes(banc: Banc) -> None:
    assert banc.doublures is not None
    for serveur in banc.doublures.serveurs:
        serveur.shutdown()
        serveur.server_close()


def base_fermee(banc: Banc) -> None:
    banc.api.app.state.etat.pool.close()  # type: ignore[attr-defined]


def service_arrete(banc: Banc) -> None:
    banc.service.arreter()


def debit_epuise(banc: Banc) -> None:
    epuiser_le_debit(banc.api)


def regler(**reglages: object) -> Callable[[Banc], None]:
    def preparer(_: Banc) -> None:
        for nom, valeur in reglages.items():
            setattr(DoublureFournisseur, nom, valeur)

    return preparer


def apres(preparer: Callable[[Banc], None], scenario: Scenario) -> Scenario:
    def joue(banc: Banc) -> httpx.Response:
        preparer(banc)
        return scenario(banc)

    return joue


def obtenir(route: str, cle: bool = True) -> Scenario:
    return lambda b: b.api.get(route, headers=entetes() if cle else None)


def recherche(**champs: object) -> Scenario:
    return lambda banc: poster(banc, "/v1/recherche", **champs)


def reponse(**champs: object) -> Scenario:
    return lambda banc: poster(banc, "/v1/reponse", **champs)


def sans_cle(route: str) -> Scenario:
    return lambda b: b.api.post(route, json={"question": QUESTION})


REFUS = json.dumps(
    {
        "refus": True,
        "motif_refus": "inconnu",
        "reponse": "Les passages ne le disent pas.",
        "series_citees": [],
    }
)
ONZE_TOURS = [
    {"role": ("libraire", "assistant")[i % 2], "contenu": "x"} for i in range(11)
]

SCENARIOS: dict[Case, list[Scenario]] = {
    ("/v1/sante", "GET", 200, ""): [obtenir("/v1/sante", cle=False)],
    ("/v1/sante", "GET", 503, ""): [
        apres(service_arrete, obtenir("/v1/sante", cle=False))
    ],
    ("/v1/info", "GET", 200, ""): [obtenir("/v1/info")],
    ("/v1/info", "GET", 401, ""): [obtenir("/v1/info", cle=False)],
    ("/v1/info", "GET", 429, ""): [apres(debit_epuise, obtenir("/v1/info"))],
    ("/v1/recherche", "POST", 200, ""): [recherche()],
    ("/v1/recherche", "POST", 401, ""): [sans_cle("/v1/recherche")],
    ("/v1/recherche", "POST", 413, ""): [lambda b: trop_gros(b.api, "/v1/recherche")],
    ("/v1/recherche", "POST", 422, "entree_invalide"): [recherche(nombre_series=51)],
    ("/v1/recherche", "POST", 422, "question_trop_longue"): [
        recherche(question="x" * 501)
    ],
    ("/v1/recherche", "POST", 429, ""): [apres(debit_epuise, recherche())],
    ("/v1/recherche", "POST", 503, ""): [apres(service_arrete, recherche())],
    # La route de réponse : sur l'API à profils factices.
    ("/v1/reponse", "POST", 200, ""): [
        reponse(),
        apres(regler(contenu=REFUS), reponse()),
    ],
    ("/v1/reponse", "POST", 401, ""): [sans_cle("/v1/reponse")],
    ("/v1/reponse", "POST", 413, ""): [lambda b: trop_gros(b.api, "/v1/reponse")],
    ("/v1/reponse", "POST", 422, "entree_invalide"): [reponse(temperature=1.5)],
    ("/v1/reponse", "POST", 422, "question_trop_longue"): [reponse(question="x" * 501)],
    ("/v1/reponse", "POST", 422, "historique_invalide"): [
        reponse(historique=ONZE_TOURS)
    ],
    ("/v1/reponse", "POST", 422, "profil_inconnu"): [reponse(profil="absent")],
    ("/v1/reponse", "POST", 422, "profil_indisponible"): [
        apres(profil_b_indisponible, reponse(profil="factice-b"))
    ],
    ("/v1/reponse", "POST", 422, "reglage_non_expose"): [
        reponse(profil="factice-b", temperature=0.5)
    ],
    ("/v1/reponse", "POST", 429, ""): [apres(debit_epuise, reponse())],
    ("/v1/reponse", "POST", 503, "service_embedding_indisponible"): [
        apres(service_arrete, reponse())
    ],
    ("/v1/reponse", "POST", 503, "service_embedding_incoherent"): [
        apres(service_incoherent, reponse())
    ],
    ("/v1/reponse", "POST", 503, "base_indisponible"): [apres(base_fermee, reponse())],
    ("/v1/reponse", "POST", 503, "encodage_en_service_change"): [
        apres(encodage_change, reponse())
    ],
    ("/v1/reponse", "POST", 503, "fournisseur_injoignable"): [
        apres(fournisseurs_arretes, reponse())
    ],
    ("/v1/reponse", "POST", 503, "fournisseur_delai"): [
        apres(regler(retard_s=DELAI_FACTICE_S * 3), reponse())
    ],
    ("/v1/reponse", "POST", 503, "fournisseur_refus"): [
        apres(regler(statut=500), reponse())
    ],
    ("/v1/reponse", "POST", 503, "sortie_modele_invalide"): [
        apres(regler(contenu="pas du json"), reponse())
    ],
}
JOUES = [
    pytest.param(case, i, id=f"{case[0]}-{case[2]}-{case[3] or 'statut'}-{i}")
    for case, scenarios in SCENARIOS.items()
    for i in range(len(scenarios))
]


def schema_declare(chemin: str, methode: str, statut: int) -> dict[str, object]:
    reponse = CONTRAT["paths"][chemin][methode.lower()]["responses"][str(statut)]
    return reponse["content"]["application/json"]["schema"]


def test_chaque_case_declaree_a_son_scenario():
    assert sorted(SCENARIOS) == DECLARES
    assert all(SCENARIOS.values())


def test_la_matrice_est_celle_du_contrat():
    """Garde-fou de lecture : le contrat versionné n'a perdu ni opération ni code ;
    `/v1/reponse` : 200, 401, 413, 422 (six codes), 429, 503 (huit codes)."""
    assert {(c, m) for c, m, _, _ in DECLARES} == {
        ("/v1/sante", "GET"),
        ("/v1/info", "GET"),
        ("/v1/recherche", "POST"),
        ("/v1/reponse", "POST"),
    }
    cases = [(s, code) for c, _, s, code in DECLARES if c == "/v1/reponse"]
    assert sorted({s for s, _ in cases}) == [200, 401, 413, 422, 429, 503]
    assert len([code for s, code in cases if s == 422]) == 6
    assert len([code for s, code in cases if s == 503]) == 8


def test_le_garde_fou_signale_un_statut_ou_un_code_hors_contrat_et_eux_seuls():
    vues = [
        ("/v1/info", "GET", 200, None),
        ("/v1/info", "GET", 500, "erreur_interne"),  # statut non déclaré
        ("/v1/reponse", "POST", 503, "fournisseur_delai"),  # code déclaré
        ("/v1/reponse", "POST", 503, "inattendu"),  # code non déclaré
        ("/v1/recherche", "POST", 503, "inattendu"),  # le statut n'énumère rien
        ("/v1/sante", "DELETE", 405, "methode_non_permise"),  # hors contrat
        ("inconnue", "POST", 413, "corps_trop_volumineux"),  # refusé avant le routage
        ("/v1/essai_panne", "GET", 500, "erreur_interne"),  # une route d'essai
    ]

    assert statuts_hors_contrat(vues) == [
        ("/v1/info", "GET", 500, "erreur_interne"),
        ("/v1/reponse", "POST", 503, "inattendu"),
    ]


@pytest.mark.parametrize(("case", "rang"), JOUES)
def test_chaque_case_declaree_est_produite_dans_sa_forme(
    request: pytest.FixtureRequest,
    service_doublure: ServiceDoublure,
    base_api: BaseApi,
    case: Case,
    rang: int,
):
    chemin, methode, statut, code = case
    # La route de réponse sur les profils factices ; les autres, sur l'API versionnée.
    de_reponse = chemin == "/v1/reponse"
    banc = Banc(
        api=request.getfixturevalue("api_reponse" if de_reponse else "api"),
        service=service_doublure,
        base=base_api,
        doublures=request.getfixturevalue("doublures") if de_reponse else None,
    )
    r = SCENARIOS[case][rang](banc)

    assert r.status_code == statut, r.text
    # Le corps a la forme que le contrat déclare pour ce statut ; le schéma du
    # contrat est lu tel quel (ses `$ref` résolus dans le document).
    schema = {
        **schema_declare(chemin, methode, statut),
        "components": CONTRAT["components"],
    }
    Draft202012Validator(schema).validate(r.json())
    if code:
        assert r.json()["code"] == code
    assert r.headers[ENTETE_IDENTIFIANT]
    assert r.headers["Cache-Control"] == "no-store"
