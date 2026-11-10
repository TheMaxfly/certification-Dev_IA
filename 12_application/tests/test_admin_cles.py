"""E4, lot 5, spec 02 — l'état des clés par fournisseur et leur test (A3).

Base jetable ; la doublure de l'API sert `/v1/info` et `POST /v1/verification` (contrat
1.5.0), dont le scénario `route_absente` imite l'API 1.4.0 de la pile (404). Aucun
fournisseur n'existe ici : le test des clés est celui de l'API, doublée.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import replace

import httpx
import psycopg
import pytest
from bs4 import BeautifulSoup
from conftest import HACHEUR_RAPIDE, Navigateur, journal_complet
from doublure_api import CLE_ATTENDUE, CLE_CATALOGUE_ATTENDUE, Doublure
from fastapi import FastAPI

from application.app import creer_application
from application.client_api import (
    ClientApi,
    Indisponible,
    RouteAbsente,
    TropTot,
    Verification,
)
from application.config import Reglages
from application.metriques import Metriques

CLE = "c" * 48
MDP = "mot de passe factice 1"
CLIENT = ("127.0.0.1", 50000)
PROCEDURE = "11_api_modele/README.md#poser-ou-changer-une-cl%C3%A9"


def configure(reglages: Reglages, base_dsn: str | None) -> Reglages:
    return replace(
        reglages,
        base_dsn=base_dsn,
        cle_session=CLE,
        catalogue=replace(reglages.catalogue, cle=CLE_CATALOGUE_ATTENDUE),
    )


def demarrer(doublure: Doublure, reglages: Reglages) -> FastAPI:
    return creer_application(
        reglages,
        transport=httpx.ASGITransport(app=doublure.app),
        hacheur=HACHEUR_RAPIDE,
    )


@pytest.fixture
def appli(doublure, reglages, base_app) -> Iterator[tuple[FastAPI, Navigateur]]:
    app = demarrer(doublure, configure(reglages, base_app))
    with Navigateur(app, client=CLIENT, raise_server_exceptions=False) as client:
        yield app, client


def entrer(client: Navigateur, dsn: str, pseudo: str, role: str = "admin") -> int:
    with psycopg.connect(dsn) as cx:
        (ident,) = cx.execute(
            "INSERT INTO application.comptes (pseudo, nom_affiche, role, hachage)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (pseudo, f"Nom {pseudo}", role, HACHEUR_RAPIDE.hacher(MDP)),
        ).fetchone()
    r = client.post(
        "/connexion",
        data={"pseudo": pseudo, "mot_de_passe": MDP},
        follow_redirects=False,
    )
    assert r.status_code == 303, pseudo
    return ident


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def lignes(page: BeautifulSoup) -> dict[str, list[str]]:
    return {
        tr.select_one("th").get_text(strip=True): [
            td.get_text(strip=True) for td in tr.select("td")
        ]
        for tr in page.select(".zone-cles tbody tr")
    }


def lancer_test(client: Navigateur, **champs: str):
    return client.post("/admin/cles/tester", data=champs, follow_redirects=False)


# --------------------------------------------------------------------------- #
#  La page
# --------------------------------------------------------------------------- #
def test_a3_la_page_nomme_chaque_variable_et_l_etat_du_demarrage(appli, base, doublure):
    _, client = appli
    entrer(client, base, "admin_un")
    doublure.choisir(info="administration")

    r = client.get("/admin/cles")
    page = soupe(r)

    assert r.status_code == 200
    vues = lignes(page)
    assert list(vues) == [
        "mistral-large-2512",
        "mistral-large-4",
        "gpt-6-luna",
        "gpt-6-sol",
    ]
    assert vues["mistral-large-2512"] == [
        "Mistral",
        "modele-de-mistral-large-2512",
        "MISTRAL_CLE_FACTICE",
        "configurée (clé posée au démarrage de l'API)",
        "pas encore testée",
    ]
    assert vues["gpt-6-sol"][3] == "absente ou écartée au démarrage de l'API"
    assert "Aucun test dans cette session." in page.select_one(".zone-cles-test").text
    lien = page.select_one(".zone-cles a")
    assert lien["href"].endswith(PROCEDURE)
    assert page.select_one("table caption").get_text(strip=True)
    assert all(th["scope"] == "col" for th in page.select("thead th"))


def test_a3_api_injoignable_encart_et_rien_d_autre(appli, base, doublure):
    _, client = appli
    entrer(client, base, "admin_un")
    doublure.choisir(info="erreur_interne")

    r = client.get("/admin/cles")
    page = soupe(r)

    assert r.status_code == 503
    assert "L'API du modèle ne répond pas" in page.select_one("[role=alert]").text
    assert page.select(".zone-cles table") == []


# --------------------------------------------------------------------------- #
#  Le test
# --------------------------------------------------------------------------- #
def test_a3_le_test_appelle_la_route_et_rend_un_resultat_date_par_profil(
    appli, base, doublure, caplog
):
    caplog.set_level(logging.INFO, logger="application")
    _, client = appli
    entrer(client, base, "admin_un")

    r = lancer_test(client)

    assert r.status_code == 303
    assert r.headers["location"] == "/admin/cles?etat=teste"
    assert doublure.verifications == [CLE_ATTENDUE]
    page = soupe(client.get(r.headers["location"]))
    vues = lignes(page)
    assert vues["mistral-large-2512"][4] == "valide : le fournisseur liste le modèle"
    assert vues["gpt-6-luna"][4] == "refusée par le fournisseur (code 401)"
    assert vues["gpt-6-sol"][4] == "clé absente : aucun appel"
    temps = page.select_one(".zone-cles-test time")
    assert temps["datetime"] == "2026-11-10T09:00:00Z"
    assert "10/11/2026 09:00 UTC" in temps.get_text()
    assert page.select_one("#avis").get_text(strip=True) == (
        "Clés testées : le résultat est dans le tableau."
    )
    assert '"verification_cles"' in journal_complet(caplog)
    assert '"issue": "teste"' in journal_complet(caplog)


def test_a3_les_autres_etats_du_test(appli, base, doublure):
    _, client = appli
    entrer(client, base, "admin_un")
    doublure.choisir(verification="injoignable")

    lancer_test(client)
    vues = lignes(soupe(client.get("/admin/cles")))

    assert vues["mistral-large-2512"][4] == "fournisseur injoignable"
    assert vues["mistral-large-4"][4] == (
        "acceptée, mais le modèle n'est pas dans la liste du fournisseur"
    )
    assert vues["gpt-6-luna"][4] == "réponse du fournisseur illisible"
    assert vues["gpt-6-sol"][4] == "pas encore testée"


def test_a3_le_resultat_survit_au_rechargement_dans_la_meme_session_seulement(
    appli, base, doublure
):
    app, client = appli
    entrer(client, base, "admin_un")
    lancer_test(client)

    for _ in range(2):
        vues = lignes(soupe(client.get("/admin/cles")))
        assert vues["mistral-large-2512"][4].startswith("valide")
    autre = Navigateur(app, client=("127.0.0.1", 50001), raise_server_exceptions=False)
    entrer(autre, base, "admin_deux")
    assert lignes(soupe(autre.get("/admin/cles")))["mistral-large-2512"][4] == (
        "pas encore testée"
    )


def test_a3_repli_l_api_1_4_0_ne_connait_pas_la_route(appli, base, doublure):
    _, client = appli
    entrer(client, base, "admin_un")
    doublure.choisir(verification="route_absente")

    r = lancer_test(client)
    page = soupe(client.get(r.headers["location"]))

    assert r.headers["location"] == "/admin/cles?etat=route_absente"
    texte = page.select_one(".zone-cles-test").get_text(" ", strip=True)
    assert (
        "Le test n'existe pas sur cette version de l'API (1.4.0) ; l'état affiché est"
        " celui du démarrage."
    ) in texte
    vues = lignes(page)
    assert vues["mistral-large-2512"][3].startswith("configurée")
    assert vues["mistral-large-2512"][4] == "pas encore testée"


def test_a3_trop_tot_reessayez_dans_n_secondes(appli, base, doublure):
    _, client = appli
    entrer(client, base, "admin_un")
    doublure.choisir(verification="trop_tot")

    r = lancer_test(client)
    avis = soupe(client.get(r.headers["location"])).select_one("#avis")

    assert r.headers["location"] == "/admin/cles?etat=trop_tot"
    assert (
        avis.get_text(strip=True) == "Un test par minute au plus : réessayez dans 42 s."
    )
    # L'attente est lue une fois : la page rechargée ne la répète pas.
    relue = soupe(client.get(r.headers["location"])).select_one("#avis")
    assert "une minute" in relue.get_text()


@pytest.mark.parametrize("scenario", ["erreur_interne", "hors_contrat"])
def test_a3_l_api_ne_repond_pas_au_test(appli, base, doublure, scenario):
    _, client = appli
    entrer(client, base, "admin_un")
    doublure.choisir(verification=scenario)

    r = lancer_test(client)
    avis = soupe(client.get(r.headers["location"])).select_one("#avis")

    assert r.headers["location"] == "/admin/cles?etat=indisponible"
    assert "n'a pas répondu au test" in avis.get_text()


def test_a3_les_metriques_comptent_chaque_issue(appli, base, doublure):
    _, client = appli
    entrer(client, base, "admin_un")
    for scenario in ("nominale", "route_absente", "trop_tot", "erreur_interne"):
        doublure.choisir(verification=scenario)
        lancer_test(client)

    texte = client.get("/metrics").text

    for issue in ("teste", "route_absente", "trop_tot", "indisponible"):
        ligne = f'application_verifications_cles_total{{issue="{issue}"}} 1.0'
        assert ligne in texte, issue


def test_a3_sans_jeton_403_et_aucun_appel(appli, base, doublure):
    _, client = appli
    entrer(client, base, "admin_un")

    assert lancer_test(client, jeton="faux").status_code == 403
    assert doublure.verifications == []


@pytest.mark.parametrize("role", ["client", "libraire"])
def test_a3_un_autre_role_recoit_403_et_aucun_appel(appli, base, doublure, role):
    _, client = appli
    entrer(client, base, f"{role}_un", role)

    assert client.get("/admin/cles").status_code == 403
    assert lancer_test(client).status_code == 403
    assert doublure.verifications == []


def test_a3_un_visiteur_est_renvoye_vers_la_connexion(appli, doublure):
    _, client = appli

    r = client.get("/admin/cles", follow_redirects=False)

    assert r.headers["location"] == "/connexion?retour=/admin/cles"
    assert lancer_test(client).status_code == 401
    assert doublure.verifications == []


def test_a3_l_onglet_cles_apparait(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")

    nav = soupe(client.get("/admin/cles")).select_one("nav.onglets")

    assert "/admin/cles" in [a["href"] for a in nav.select("a")]
    assert nav.select_one("a[aria-current=page]")["href"] == "/admin/cles"


# --------------------------------------------------------------------------- #
#  Le client
# --------------------------------------------------------------------------- #
def client_sur(doublure: Doublure, reglages: Reglages, **delais: float) -> ClientApi:
    return ClientApi(
        "http://127.0.0.1:8010",
        CLE_ATTENDUE,
        replace(reglages.delais, **delais),
        Metriques(),
        httpx.ASGITransport(app=doublure.app),
    )


async def verifier(doublure: Doublure, reglages: Reglages, **delais: float):
    client = client_sur(doublure, reglages, **delais)
    try:
        return await client.verification()
    finally:
        await client.fermer()


@pytest.mark.parametrize(
    ("scenario", "attendu"),
    [
        ("nominale", Verification),
        ("route_absente", RouteAbsente),
        ("trop_tot", TropTot),
        ("erreur_interne", Indisponible),
        ("hors_contrat", Indisponible),
    ],
)
def test_le_client_lit_chaque_reponse_de_la_route(
    doublure, reglages, scenario, attendu
):
    doublure.choisir(verification=scenario)

    lu = asyncio.run(verifier(doublure, reglages))

    assert isinstance(lu, attendu)
    if isinstance(lu, TropTot):
        assert lu.retry_after == 42


def test_le_client_sans_retry_after_et_au_dela_du_delai(doublure, reglages):
    doublure.choisir(verification="trop_tot_sans_entete")
    assert asyncio.run(verifier(doublure, reglages)) == TropTot(None)
    doublure.choisir(verification="delai")
    lu = asyncio.run(
        verifier(doublure, reglages, connexion_s=0.05, verification_s=0.05)
    )
    assert isinstance(lu, Indisponible)
