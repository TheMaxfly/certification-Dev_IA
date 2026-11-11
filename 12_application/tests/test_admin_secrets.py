"""E4, lot 5, décision 8 du cadre — jamais une valeur de secret à l'écran.

Des valeurs factices **reconnaissables** sont posées dans l'environnement de
l'application (les quatre clés qu'elle lit), puis cherchées dans le HTML de **toutes**
les pages de l'administration — chaque onglet présent, et les états après un test des
clés —, dans `/sante` et dans `/metrics`. Aucune ne doit y paraître.
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import psycopg
import pytest
from conftest import ADRESSE_MEMOIRE, HACHEUR_RAPIDE, Navigateur, bornes_larges
from doublure_api import Doublure

from application import pages_admin
from application.app import creer_application
from application.config import charger

#: Les quatre secrets que l'application lit dans son environnement, et une valeur
#: factice reconnaissable pour chacun (un marqueur, son rang, un remplissage : rien
#: qui ressemble à une clé réelle ; la session en veut 32 octets au moins).
NOMS = (
    "APPLICATION_CLE_API",
    "APPLICATION_CLE_API_COMPTES",
    "APPLICATION_CLE_CATALOGUE",
    "APPLICATION_CLE_SESSION",
)
FACTICES = {
    nom: f"factice-{rang}-" + "secret-de-test-" * 3 for rang, nom in enumerate(NOMS)
}
MDP = "mot de passe factice des secrets"


@pytest.fixture
def client(doublure: Doublure, base_app: str, base: str):
    reglages = bornes_larges(
        replace(
            charger({**FACTICES, "APPLICATION_BASE_DSN": base_app}),
            adresse_api=ADRESSE_MEMOIRE,
        )
    )
    app = creer_application(
        reglages,
        transport=httpx.ASGITransport(app=doublure.app),
        hacheur=HACHEUR_RAPIDE,
    )
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO application.comptes (pseudo, nom_affiche, role, hachage)"
            " VALUES ('admin_secrets', 'Nom admin', 'admin', %s)",
            (HACHEUR_RAPIDE.hacher(MDP),),
        )
    with Navigateur(
        app, client=("127.0.0.1", 50000), raise_server_exceptions=False
    ) as navigateur:
        r = navigateur.post(
            "/connexion",
            data={"pseudo": "admin_secrets", "mot_de_passe": MDP},
            follow_redirects=False,
        )
        assert r.status_code == 303
        yield app, navigateur


def pages_de_l_administration(app) -> list[str]:
    presents = pages_admin.chemins_en_get(app.routes)
    return [chemin for chemin, _ in pages_admin.ONGLETS if chemin in presents]


def test_aucune_valeur_de_secret_dans_les_pages_d_administration_ni_ailleurs(
    client, doublure
):
    app, navigateur = client
    chemins = pages_de_l_administration(app)
    assert {"/admin/comptes", "/admin/reglages", "/admin/cles"} <= set(chemins)
    textes: dict[str, str] = {}
    for chemin in chemins:
        r = navigateur.get(chemin)
        assert r.status_code == 200, chemin
        textes[chemin] = r.text
    for scenario in ("nominale", "route_absente", "trop_tot"):
        doublure.choisir(verification=scenario)
        r = navigateur.post("/admin/cles/tester", data={})
        textes[f"/admin/cles ({scenario})"] = r.text
    textes["/sante"] = navigateur.get("/sante").text
    textes["/metrics"] = navigateur.get("/metrics").text

    for ou, texte in textes.items():
        for nom, valeur in FACTICES.items():
            assert valeur not in texte, (ou, nom)
            # Ni le marqueur qui la distingue des autres.
            assert valeur[:10] not in texte, (ou, nom)
