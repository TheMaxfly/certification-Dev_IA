"""E4, lot 5, spec 03 — l'état des services, lu à la demande de la page (A6).

Doublures : l'API du modèle (en mémoire, ou une adresse du bouclage où rien n'écoute),
le catalogue (en mémoire ; « indisponible » : sa base ne répond pas ; une adresse
fermée ; non configuré), la base (jetable, absente, ou une adresse fermée).
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from dataclasses import replace

import httpx
import psycopg
import pytest
from bs4 import BeautifulSoup
from conftest import HACHEUR_RAPIDE, Navigateur
from doublure_api import CLE_CATALOGUE_ATTENDUE, Doublure
from fastapi import FastAPI

from application.app import creer_application
from application.config import Monitorage, Reglages

CLE = "c" * 48
MDP = "mot de passe factice 1"
CLIENT = ("127.0.0.1", 50000)


def adresse_fermee() -> str:
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{prise.getsockname()[1]}"


def configure(
    reglages: Reglages,
    base_dsn: str | None,
    *,
    catalogue: bool = True,
    grafana: str | None = None,
    comptes: str | None = None,
) -> Reglages:
    return replace(
        reglages,
        base_dsn=base_dsn,
        cle_session=CLE,
        cle_api_comptes=comptes,
        catalogue=replace(
            reglages.catalogue, cle=CLE_CATALOGUE_ATTENDUE if catalogue else None
        ),
        monitorage=Monitorage(adresse_grafana=grafana),
    )


def demarrer(
    doublure: Doublure,
    reglages: Reglages,
    *,
    api: bool = True,
    catalogue_ferme: bool = False,
) -> FastAPI:
    memoire = httpx.ASGITransport(app=doublure.app)
    if not api:
        reglages = replace(reglages, adresse_api=adresse_fermee())
    if catalogue_ferme:
        reglages = replace(
            reglages, catalogue=replace(reglages.catalogue, adresse=adresse_fermee())
        )
    return creer_application(
        reglages,
        transport=memoire if api else None,
        # Fermé : un vrai transport, vers l'adresse où rien n'écoute (sans lui, le
        # catalogue prendrait la doublure en mémoire).
        transport_catalogue=httpx.AsyncHTTPTransport() if catalogue_ferme else memoire,
        hacheur=HACHEUR_RAPIDE,
    )


@pytest.fixture
def appli(doublure, reglages, base_app) -> Iterator[tuple[FastAPI, Navigateur]]:
    app = demarrer(doublure, configure(reglages, base_app))
    with Navigateur(app, client=CLIENT, raise_server_exceptions=False) as client:
        yield app, client


def entrer(client: Navigateur, dsn: str, pseudo: str, role: str = "admin") -> None:
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO application.comptes (pseudo, nom_affiche, role, hachage)"
            " VALUES (%s, %s, %s, %s)",
            (pseudo, f"Nom {pseudo}", role, HACHEUR_RAPIDE.hacher(MDP)),
        )
    r = client.post(
        "/connexion",
        data={"pseudo": pseudo, "mot_de_passe": MDP},
        follow_redirects=False,
    )
    assert r.status_code == 303, pseudo


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def lignes(page: BeautifulSoup) -> dict[str, dict[str, str]]:
    vues: dict[str, dict[str, str]] = {}
    for tr in page.select(".zone-services tbody tr"):
        etat, delai, details = tr.select("td")
        vues[tr["class"][0].removeprefix("service-")] = {
            "etat": etat.get_text(" ", strip=True),
            "delai": delai.get_text(strip=True),
            "details": details.get_text(" | ", strip=True),
        }
    return vues


def lire(
    app: FastAPI, base: str | None = None, pseudo: str = "admin_services"
) -> BeautifulSoup:
    with Navigateur(app, client=CLIENT, raise_server_exceptions=False) as client:
        if base is not None:
            entrer(client, base, pseudo)
        r = client.get("/admin/services")
        assert r.status_code == 200, r.status_code
        return soupe(r)


# --------------------------------------------------------------------------- #
#  Tout répond
# --------------------------------------------------------------------------- #
def test_a6_chaque_service_son_mot_son_icone_son_delai(appli, base, doublure):
    _, client = appli
    entrer(client, base, "admin_un")
    doublure.choisir(info="administration")

    page = soupe(client.get("/admin/services"))
    vues = lignes(page)

    assert list(vues) == ["api", "catalogue", "base", "cles"]
    for cle in ("api", "catalogue", "base"):
        assert vues[cle]["etat"] == "✓ joignable", cle
        assert vues[cle]["delai"].endswith(" ms"), cle
    assert vues["cles"] == {
        "etat": "– une",
        "delai": "sans objet",
        "details": "la clé des comptes n'est pas posée : la publique sert aux deux",
    }
    api = vues["api"]["details"]
    assert "version du contrat : 1.4.0" in api
    assert "corpus v2, encodage 3 (modele-embedding-factice)" in api
    assert "profil actif de l'API : mistral-large-2512" in api
    assert "profil actif de l'application : mistral-large-2512 (le défaut" in api
    assert "normalisation de la recherche : fonction" in vues["catalogue"]["details"]
    # L'icône est décorative ; le mot porte l'état (jamais l'icône seule).
    assert all(
        s["aria-hidden"] == "true" for s in page.select(".zone-services td span")
    )
    assert page.select(".zone-services-encart") == []
    temps = page.select_one(".zone-services time")
    assert temps["datetime"].endswith("Z") and temps.get_text().endswith("UTC")
    relire = page.select_one(".zone-services a[href='/admin/services']")
    assert relire.get_text(strip=True) == "Relire"
    assert page.select("main form") == []  # la page ne modifie rien
    assert "Grafana" in page.text and page.select("a[href^='http']") == []


def test_a6_deux_cles_d_api(doublure, reglages, base_app, base):
    app = demarrer(doublure, configure(reglages, base_app, comptes="d" * 40))

    vues = lignes(lire(app, base))

    assert vues["cles"]["etat"] == "✓ deux" and vues["cles"]["details"] == ""


def test_a6_le_profil_actif_regle_et_sa_disponibilite(appli, base, doublure):
    _, client = appli
    entrer(client, base, "admin_un")
    doublure.choisir(info="administration")
    client.post("/admin/reglages", data={"profil": "gpt-6-luna"})

    api = lignes(soupe(client.get("/admin/services")))["api"]["details"]
    assert "gpt-6-luna (choisi par l'administration, disponible)" in api
    doublure.choisir(info="profil_indisponible")
    api = lignes(soupe(client.get("/admin/services")))["api"]["details"]
    assert "mistral-large-2512 (le réglage est indisponible" in api


def test_a6_le_lien_grafana_seulement_si_l_adresse_est_posee(
    doublure, reglages, base_app, base
):
    app = demarrer(
        doublure, configure(reglages, base_app, grafana="http://127.0.0.1:3000")
    )

    lien = lire(app, base).select_one(".zone-services a[href='http://127.0.0.1:3000']")

    assert lien.get_text(strip=True) == "Ouvrir le tableau de bord"


# --------------------------------------------------------------------------- #
#  Un service manque
# --------------------------------------------------------------------------- #
def test_a6_api_injoignable_encart_et_le_reste_inchange(
    doublure, reglages, base_app, base
):
    app = demarrer(doublure, configure(reglages, base_app), api=False)

    page = lire(app, base)
    vues = lignes(page)

    assert vues["api"]["etat"] == "✗ injoignable" and vues["api"]["details"] == ""
    assert vues["catalogue"]["etat"] == vues["base"]["etat"] == "✓ joignable"
    encart = page.select_one(".zone-services-encart")
    assert "Injoignable : API du modèle." in encart.get_text()


def test_a6_catalogue_dont_la_base_ne_repond_pas(appli, base, doublure):
    _, client = appli
    entrer(client, base, "admin_un")
    doublure.choisir(catalogue="indisponible")

    vues = lignes(soupe(client.get("/admin/services")))

    assert vues["catalogue"]["etat"] == "✗ injoignable"
    assert "le catalogue répond, sa base non" in vues["catalogue"]["details"]


def test_a6_catalogue_injoignable_et_non_configure(doublure, reglages, base_app, base):
    fermee = demarrer(doublure, configure(reglages, base_app), catalogue_ferme=True)
    absent = demarrer(doublure, configure(reglages, base_app, catalogue=False))

    assert lignes(lire(fermee, base))["catalogue"]["etat"] == "✗ injoignable"
    vues = lignes(lire(absent, base, pseudo="admin_second"))
    assert vues["catalogue"] == {
        "etat": "– non configuré",
        "delai": "sans objet",
        "details": "",
    }


def test_a6_sans_base_la_page_reste_a_200_et_le_dit(doublure, reglages):
    app = demarrer(doublure, configure(reglages, None))

    page = lire(app)  # aucune session : la base manque, c'est elle qu'on lit
    vues = lignes(page)

    assert vues["base"]["etat"] == "– non configuré"
    assert vues["api"]["etat"] == "✓ joignable"
    assert (
        "profil actif de l'application : mistral-large-2512" in vues["api"]["details"]
    )


def test_a6_base_injoignable_la_page_reste_a_200(doublure, reglages):
    fermee = adresse_fermee().replace("http://", "")
    dsn = f"postgresql://personne@{fermee}/apimanga_essai"
    app = demarrer(doublure, configure(reglages, dsn))

    vues = lignes(lire(app))

    assert vues["base"]["etat"] == "✗ injoignable"


def test_a6_tout_injoignable_la_page_repond_quand_meme(doublure, reglages):
    fermee = adresse_fermee().replace("http://", "")
    dsn = f"postgresql://personne@{fermee}/apimanga_essai"
    app = demarrer(doublure, configure(reglages, dsn), api=False, catalogue_ferme=True)

    page = lire(app)
    vues = lignes(page)

    assert [vues[c]["etat"] for c in ("api", "catalogue", "base")] == [
        "✗ injoignable"
    ] * 3
    encart = page.select_one(".zone-services-encart").get_text()
    assert "API du modèle, Catalogue, Base de l'application" in encart


# --------------------------------------------------------------------------- #
#  Les rôles (la base répond)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("role", ["client", "libraire"])
def test_a6_un_autre_role_recoit_403(appli, base, role):
    _, client = appli
    entrer(client, base, f"{role}_un", role)

    assert client.get("/admin/services").status_code == 403


def test_a6_un_visiteur_est_renvoye_vers_la_connexion(appli):
    _, client = appli

    r = client.get("/admin/services", follow_redirects=False)

    assert r.headers["location"] == "/connexion?retour=/admin/services"


def test_a6_l_onglet_services_apparait_et_les_six_onglets_sont_la(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")

    nav = soupe(client.get("/admin/services")).select_one("nav.onglets")

    assert [a.get_text(strip=True) for a in nav.select("a")] == [
        "Comptes",
        "Modération",
        "Réglages",
        "Clés",
        "Coûts",
        "Services",
    ]
    assert nav.select_one("a[aria-current=page]")["href"] == "/admin/services"
