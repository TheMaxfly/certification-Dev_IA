"""E4, lot 5 — les bords de l'administration : configuration fautive, formulaire
illisible, lectures trop lentes ou hors contrat, base perdue en cours de page, clé de
réglage inconnue. Chacun a son issue nommée, jamais une erreur 500."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace

import httpx
import psycopg
import pytest
from bs4 import BeautifulSoup
from conftest import HACHEUR_RAPIDE, Navigateur
from doublure_api import CLE_ATTENDUE, CLE_CATALOGUE_ATTENDUE, Doublure
from fastapi import FastAPI
from test_configuration import ENV, ecarts_de, fichier_modifie

from application import pages_admin, reglages_app
from application.app import creer_application
from application.base import Base, BaseIndisponible
from application.client_catalogue import ClientCatalogue
from application.config import DelaisCatalogue, Reglages
from application.metriques import Metriques

CLE = "c" * 48
MDP = "mot de passe factice 1"


# --------------------------------------------------------------------------- #
#  La configuration
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("avant", "apres", "attendu"),
    [
        (
            'procedure_cles = "https://',
            'procedure_cles = "http://',
            "[administration] procedure_cles : https:// attendu",
        ),
        (
            "cout_anterieur_usd = 0.709557",
            "cout_anterieur_usd = -1",
            "[couts] cout_anterieur_usd : positif ou nul attendu",
        ),
        (
            'adresse_grafana = ""',
            'adresse_grafana = "ftp://grafana"',
            "[monitorage] adresse_grafana : http(s)://hôte attendu",
        ),
        (
            'adresse_grafana = ""',
            "adresse_grafana = 3000",
            "[monitorage.adresse_grafana] texte attendu (vide admis)",
        ),
        ("historique_jours = 30", "historique_jours = 0", "[couts.historique_jours]"),
        (
            "budget_projet_usd = 20",
            "budget_projet_usd = 0",
            "[couts.budget_projet_usd]",
        ),
    ],
)
def test_une_configuration_fautive_de_l_administration_empeche_de_demarrer(
    tmp_path, avant, apres, attendu
):
    ecarts = ecarts_de(ENV, fichier_modifie(tmp_path, avant, apres))

    assert any(attendu in e for e in ecarts), ecarts


def test_l_anterieur_peut_valoir_zero_et_grafana_se_pose(tmp_path):
    from application.config import charger

    fichier = fichier_modifie(
        tmp_path, "cout_anterieur_usd = 0.709557", "cout_anterieur_usd = 0"
    )
    texte = fichier.read_text(encoding="utf-8").replace(
        'adresse_grafana = ""', 'adresse_grafana = "http://127.0.0.1:3000"'
    )
    fichier.write_text(texte, encoding="utf-8")

    lus = charger(ENV, fichier)

    assert lus.couts.cout_anterieur_usd == 0
    assert lus.monitorage.adresse_grafana == "http://127.0.0.1:3000"


# --------------------------------------------------------------------------- #
#  Les réglages : une clé hors de la liste fermée
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("geste", ["lire", "ecrire"])
def test_une_cle_de_reglage_inconnue_est_une_erreur_de_programmation(geste):
    base = Base(None, 1.0, Metriques())

    async def jouer() -> object:
        if geste == "lire":
            return await reglages_app.lire(base, "budget")
        return await reglages_app.ecrire(base, "budget", "x", 1)

    with pytest.raises(ValueError, match="clé de réglage inconnue"):
        asyncio.run(jouer())


# --------------------------------------------------------------------------- #
#  Les pages
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(doublure, reglages: Reglages, base_app, base) -> Iterator[Navigateur]:
    app: FastAPI = creer_application(
        replace(
            reglages,
            base_dsn=base_app,
            cle_session=CLE,
            catalogue=replace(reglages.catalogue, cle=CLE_CATALOGUE_ATTENDUE),
        ),
        transport=httpx.ASGITransport(app=doublure.app),
        hacheur=HACHEUR_RAPIDE,
    )
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO application.comptes (pseudo, nom_affiche, role, hachage)"
            " VALUES ('admin_bords', 'Nom admin', 'admin', %s)",
            (HACHEUR_RAPIDE.hacher(MDP),),
        )
    with Navigateur(
        app, client=("127.0.0.1", 50000), raise_server_exceptions=False
    ) as navigateur:
        r = navigateur.post(
            "/connexion",
            data={"pseudo": "admin_bords", "mot_de_passe": MDP},
            follow_redirects=False,
        )
        assert r.status_code == 303
        yield navigateur


@pytest.mark.parametrize("chemin", ["/admin/reglages", "/admin/cles/tester"])
def test_un_formulaire_illisible_est_une_demande_illisible(client, chemin):
    r = client.post(
        chemin,
        content=b"illisible",
        headers={"content-type": "multipart/form-data; boundary=x"},
    )

    assert r.status_code == 400


def test_une_lecture_trop_lente_des_services_est_dite_injoignable(
    client, doublure, monkeypatch
):
    monkeypatch.setattr(pages_admin, "DELAI_SERVICE_S", 0.05)
    doublure.choisir(sante="delai")

    r = client.get("/admin/services")

    assert r.status_code == 200
    assert "Injoignable : API du modèle." in r.text


def test_la_base_perdue_en_lisant_le_reglage_des_services(client, monkeypatch):
    async def perdue(*_: object) -> None:
        raise BaseIndisponible("interrompue")

    monkeypatch.setattr(reglages_app, "lire", perdue)

    r = client.get("/admin/services")

    assert r.status_code == 200
    texte = BeautifulSoup(r.text, "html.parser").get_text()
    assert "profil actif de l'application : mistral-large-2512 (le défaut" in texte


# --------------------------------------------------------------------------- #
#  Le catalogue : `/health` hors du contrat
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("statut", "corps"),
    [(500, b'{"status": "ok", "db": "ok"}'), (200, b"pas du json"), (503, b"{}")],
)
def test_un_health_hors_contrat_rend_aucun_etat(statut, corps):
    def repondre(_: httpx.Request) -> httpx.Response:
        return httpx.Response(statut, content=corps)

    async def jouer() -> object:
        client = ClientCatalogue(
            "http://127.0.0.1:8000",
            CLE_CATALOGUE_ATTENDUE,
            DelaisCatalogue(connexion_s=0.5, reponse_s=0.5),
            Metriques(),
            httpx.MockTransport(repondre),
        )
        try:
            return await client.etat()
        finally:
            await client.fermer()

    assert asyncio.run(jouer()) is None


def test_la_doublure_n_a_pas_vu_d_autre_cle(doublure: Doublure):
    assert all(c in (None, CLE_ATTENDUE) for c in doublure.cles_recues)
