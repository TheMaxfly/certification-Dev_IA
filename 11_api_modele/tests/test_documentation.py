"""La documentation interactive (`/v1/docs`) : absente par défaut ; sur réglage, une
page Swagger UI à version exacte, sous empreinte d'intégrité et politique de contenu,
qui lit le contrat publié. Les routes protégées y demandent la clé, comme ailleurs."""

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


def test_sur_reglage_la_page_lit_le_contrat_publie(api_documentee):
    r = api_documentee.get("/v1/docs")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '"url": "/v1/openapi.json"' in r.text
    assert '"validatorUrl": null' in r.text
    assert api_documentee.get("/v1/openapi.json").json() == contrat()


def test_swagger_ui_a_version_exacte_et_sous_empreinte(api_documentee):
    texte = api_documentee.get("/v1/docs").text
    sources = re.findall(r'(?:src|href)="(https://[^"]+)"', texte)

    assert sources == [documentation.STYLE[0], documentation.SCRIPT[0]]
    assert all("swagger-ui-dist@5.33.1/" in s for s in sources)
    for url, empreinte in (documentation.STYLE, documentation.SCRIPT):
        assert f'"{url}" integrity="{empreinte}" crossorigin="anonymous"' in texte


def test_la_politique_de_contenu_ne_laisse_passer_que_l_amorce_et_le_cdn(
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
    assert f"script-src {documentation.SCRIPT[0]} 'sha256-{empreinte}'" in politique
    assert "connect-src 'self'" in politique
    assert "frame-ancestors 'none'" in politique


def test_la_documentation_n_entre_pas_au_contrat(api_documentee):
    assert "/v1/docs" not in api_documentee.get("/v1/openapi.json").json()["paths"]


def test_depuis_la_documentation_les_routes_demandent_la_cle(api_documentee):
    assert api_documentee.get("/v1/info").status_code == 401
    assert api_documentee.get("/v1/info", headers=entetes()).status_code == 200
