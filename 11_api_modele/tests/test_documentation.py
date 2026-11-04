"""La documentation interactive (`/v1/docs`) : absente par défaut ; sur réglage, une
page Swagger UI vendue, sous empreintes et politique de contenu, qui ne charge rien hors
de l'API et lit le contrat publié. Les routes protégées y demandent la clé, comme
ailleurs."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import replace

import pytest
from conftest import ServiceDoublure, entetes
from fastapi.testclient import TestClient

from api_modele import documentation
from api_modele.application import creer_application
from api_modele.contrat import contrat


@pytest.fixture
def api_documentee(reglages, service_doublure: ServiceDoublure):
    application = creer_application(
        replace(reglages, documentation=True), instance=service_doublure.instance
    )
    with TestClient(application) as client:
        yield client


def test_par_defaut_la_documentation_interactive_n_existe_pas(api):
    r = api.get("/v1/docs")

    assert r.status_code == 404
    assert r.json()["code"] == "route_inconnue"
    assert api.get("/docs").status_code == 404


def test_par_defaut_les_fichiers_vendus_ne_sont_pas_servis(api):
    r = api.get(f"/v1/docs/{documentation.SCRIPT}")

    assert r.status_code == 404
    assert r.json()["code"] == "route_inconnue"


def test_sur_reglage_la_page_lit_le_contrat_publie(api_documentee):
    r = api_documentee.get("/v1/docs")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '"url": "/v1/openapi.json"' in r.text
    assert '"validatorUrl": null' in r.text
    assert api_documentee.get("/v1/openapi.json").json() == contrat()


def test_la_page_ne_charge_rien_hors_de_l_api(api_documentee):
    texte = api_documentee.get("/v1/docs").text
    sources = re.findall(r'(?:src|href)="([^"]+)"', texte)

    assert sources == ["/v1/docs/swagger-ui.css", "/v1/docs/swagger-ui-bundle.js"]
    assert "http://" not in texte and "https://" not in texte


@pytest.mark.parametrize(
    ("nom", "type_"),
    [
        ("swagger-ui-bundle.js", "text/javascript"),
        ("swagger-ui.css", "text/css"),
    ],
)
def test_les_deux_fichiers_vendus_sont_servis_tels_quels(api_documentee, nom, type_):
    r = api_documentee.get(f"/v1/docs/{nom}")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith(type_)
    assert r.content == (documentation.STATIQUE / nom).read_bytes()


@pytest.mark.parametrize(
    "nom",
    [
        "EMPREINTES",
        "LICENCE-swagger-ui",
        "inconnu.js",
        "..%2Fdocumentation.py",
        "..%2F..%2F..%2Fconfig%2Freglages.toml",
    ],
)
def test_aucun_autre_fichier_n_est_servi(api_documentee, nom):
    assert api_documentee.get(f"/v1/docs/{nom}").status_code == 404


def test_les_empreintes_des_fichiers_vendus_sont_exactes():
    lignes = [
        ligne.split()
        for ligne in (documentation.STATIQUE / "EMPREINTES")
        .read_text(encoding="utf-8")
        .splitlines()
        if ligne and not ligne.startswith("#")
    ]

    assert {nom for _, nom in lignes} == set(documentation.TYPES)
    for empreinte, nom in lignes:
        contenu = (documentation.STATIQUE / nom).read_bytes()
        assert hashlib.sha256(contenu).hexdigest() == empreinte, nom


def test_swagger_ui_est_vendu_a_version_exacte_avec_sa_licence():
    statique = documentation.STATIQUE
    empreintes = (statique / "EMPREINTES").read_text(encoding="utf-8")

    assert f"Swagger UI {documentation.VERSION_SWAGGER_UI}," in empreintes
    licence = (statique / "LICENCE-swagger-ui").read_text(encoding="utf-8")
    assert licence.split()[:2] == ["Apache", "License"]
    assert "Version 2.0" in licence
    avis = (statique / "NOTICE-swagger-ui").read_text(encoding="utf-8")
    assert "SmartBear" in avis


def test_la_politique_de_contenu_ne_laisse_passer_que_l_amorce_et_l_api(
    api_documentee,
):
    r = api_documentee.get("/v1/docs")
    politique = r.headers["Content-Security-Policy"]
    amorce = re.search(r"<script>(.*)</script>", r.text)
    assert amorce is not None
    empreinte = base64.b64encode(
        hashlib.sha256(amorce.group(1).encode()).digest()
    ).decode()

    assert "default-src 'none'" in politique
    assert f"script-src 'self' 'sha256-{empreinte}'" in politique
    assert "style-src 'self' 'unsafe-inline'" in politique
    assert "connect-src 'self'" in politique
    assert "frame-ancestors 'none'" in politique
    assert "http" not in politique


def test_la_documentation_n_entre_pas_au_contrat(api_documentee):
    chemins = api_documentee.get("/v1/openapi.json").json()["paths"]

    assert not [c for c in chemins if "/docs" in c]


def test_depuis_la_documentation_les_routes_demandent_la_cle(api_documentee):
    assert api_documentee.get("/v1/info").status_code == 401
    assert api_documentee.get("/v1/info", headers=entetes()).status_code == 200
