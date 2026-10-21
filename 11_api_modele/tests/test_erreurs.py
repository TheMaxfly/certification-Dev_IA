"""Le modèle d'erreur unique : `code`, `message`, `details`, `identifiant_requete` ;
l'identifiant aussi en en-tête ; jamais de trace de pile dans une réponse."""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field

from api_modele.erreurs import ENTETE_IDENTIFIANT, champs

CLES_ERREUR = {"code", "message", "details", "identifiant_requete"}


# Des routes d'essai, ajoutées à l'API démarrée : leurs modèles au niveau du module,
# où FastAPI résout les annotations.
class Texte(BaseModel):
    model_config = ConfigDict(extra="forbid")
    texte: str


class Reglage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    nombre_series: int = Field(ge=1, le=50)
    autre: int = Field(ge=0, le=9)
    drapeau: bool


def echo(essai: Texte) -> dict[str, int]:
    return {"longueur": len(essai.texte)}


def reglage(essai: Reglage) -> dict[str, int]:
    return {"nombre": essai.nombre_series}


def verifier_forme(reponse, statut: int, code: str) -> dict:
    corps = reponse.json()
    assert reponse.status_code == statut
    assert set(corps) == CLES_ERREUR
    assert corps["code"] == code
    assert corps["identifiant_requete"] == reponse.headers[ENTETE_IDENTIFIANT]
    return corps


def test_route_inconnue_404(api):
    verifier_forme(api.get("/v1/inconnue"), 404, "route_inconnue")


def test_methode_non_permise_405(api):
    r = api.delete("/v1/sante")

    verifier_forme(r, 405, "methode_non_permise")
    assert r.headers["Allow"] == "GET"


def test_corps_trop_volumineux_413_avant_toute_lecture(api):
    r = api.post("/v1/sante", content=b"x" * 16385)

    corps = verifier_forme(r, 413, "corps_trop_volumineux")
    assert corps["details"] == {"maximum_octets": 16384}


def test_corps_trop_volumineux_sans_longueur_annoncee(api):
    def morceaux():
        for _ in range(5):
            yield b"x" * 4096

    r = api.post("/v1/sante", content=morceaux())

    verifier_forme(r, 413, "corps_trop_volumineux")


def test_un_corps_sous_la_limite_passe(api):
    """Le corps lu par l'enveloppe est rendu intact à la route."""
    api.app.router.add_api_route("/v1/essai_echo", echo, methods=["POST"])
    r = api.post("/v1/essai_echo", json={"texte": "é" * 5000})

    assert r.status_code == 200
    assert r.json() == {"longueur": 5000}


def test_entree_invalide_422_nomme_les_champs(api):
    api.app.router.add_api_route("/v1/essai_entree", reglage, methods=["POST"])
    r = api.post(
        "/v1/essai_entree",
        json={"nombre_series": 51, "autre": 10, "drapeau": "vrai", "corpus_id": "v1"},
    )

    corps = verifier_forme(r, 422, "entree_invalide")
    assert corps["details"]["champs"] == [
        # Un réglage connu : ses deux bornes, celles de la configuration.
        {"champ": "nombre_series", "raison": "hors_bornes", "bornes": [1, 50]},
        # Un autre champ : la borne que Pydantic dit franchie.
        {"champ": "autre", "raison": "hors_bornes", "bornes": [None, 9]},
        {"champ": "drapeau", "raison": "type"},
        {"champ": "corpus_id", "raison": "inconnu"},
    ]


def test_json_illisible_422(api):
    api.app.router.add_api_route("/v1/essai_json", echo, methods=["POST"])
    r = api.post(
        "/v1/essai_json",
        content=b"{pas du json",
        headers={"Content-Type": "application/json"},
    )

    corps = verifier_forme(r, 422, "entree_invalide")
    assert corps["details"]["champs"][0]["raison"] == "json_invalide"


def test_erreur_imprevue_500_sans_trace_de_pile(api, caplog):
    def panne() -> None:
        raise RuntimeError("détail interne à ne pas montrer")

    api.app.router.add_api_route("/v1/essai_panne", panne, methods=["GET"])
    with caplog.at_level(logging.INFO, logger="api_modele"):
        r = api.get("/v1/essai_panne")

    corps = verifier_forme(r, 500, "erreur_interne")
    assert "détail interne" not in r.text
    assert corps["details"] is None
    traces = [x for x in caplog.records if x.getMessage() == "erreur_interne"]
    assert traces and traces[0].exc_info is not None
    assert traces[0].champs["identifiant_requete"] == corps["identifiant_requete"]


def test_chaque_reponse_porte_un_identifiant_distinct(api):
    a = api.get("/v1/sante").headers[ENTETE_IDENTIFIANT]
    b = api.get("/v1/sante").headers[ENTETE_IDENTIFIANT]

    assert a != b
    assert len(a) == 36


def test_champs_rend_une_raison_stable_par_type():
    erreurs = [
        {"loc": ("body", "question"), "type": "missing"},
        {"loc": ("body",), "type": "model_attributes_type"},
        {"loc": ("body", "texte"), "type": "string_too_long", "ctx": {"max_length": 9}},
        {"loc": ("query", "x"), "type": "autre_chose"},
    ]

    assert champs(erreurs) == [
        {"champ": "question", "raison": "manquant"},
        {"champ": "corps", "raison": "type"},
        {"champ": "texte", "raison": "hors_bornes", "bornes": [None, 9]},
        {"champ": "query.x", "raison": "invalide"},
    ]
