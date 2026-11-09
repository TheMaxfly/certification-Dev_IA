"""E4, lot 5, spec 03 — les coûts par jour, profil et mode, et la jauge du budget (A4).

Base jetable garnie de lignes `couts_jour` sur 35 jours, deux profils, deux modes ;
horloge injectée (le jour UTC de la page). La table est la source unique : la page ne
lit qu'elle et la configuration (`[couts]`, `[conseiller.limites]`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import psycopg
import pytest
from bs4 import BeautifulSoup
from conftest import HACHEUR_RAPIDE, Navigateur
from doublure_api import CLE_CATALOGUE_ATTENDUE, Doublure
from fastapi import FastAPI

from application import couts
from application.app import creer_application
from application.config import Reglages

CLE = "c" * 48
MDP = "mot de passe factice 1"
CLIENT = ("127.0.0.1", 50000)
AUJOURD_HUI = date(2026, 11, 9)
INSTANT = datetime(2026, 11, 9, 12, 0, tzinfo=UTC).timestamp()


def configure(reglages: Reglages, base_dsn: str | None, **couts_: float) -> Reglages:
    return replace(
        reglages,
        base_dsn=base_dsn,
        cle_session=CLE,
        catalogue=replace(reglages.catalogue, cle=CLE_CATALOGUE_ATTENDUE),
        couts=replace(reglages.couts, **couts_),
    )


def demarrer(doublure: Doublure, reglages: Reglages) -> FastAPI:
    return creer_application(
        reglages,
        transport=httpx.ASGITransport(app=doublure.app),
        hacheur=HACHEUR_RAPIDE,
        horloge=lambda: INSTANT,
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


def poser(dsn: str, *lignes: tuple[date, str, str, int, str]) -> None:
    with psycopg.connect(dsn) as cx:
        for jour, profil, mode, appels, cout in lignes:
            cx.execute(
                "INSERT INTO application.couts_jour"
                " (jour, profil, mode, appels, cout_usd) VALUES (%s, %s, %s, %s, %s)",
                (jour, profil, mode, appels, Decimal(cout)),
            )


def garnir(dsn: str) -> None:
    """35 jours, aujourd'hui compris : chaque jour, `p-a` strict (2 appels, 0,01 $) et
    `p-b` enrichi (1 appel, 0,02 $) ; aujourd'hui, `p-a` enrichi en plus (0,10 $)."""
    lignes = []
    for i in range(35):
        jour = AUJOURD_HUI - timedelta(days=i)
        lignes += [
            (jour, "p-a", "strict", 2, "0.01"),
            (jour, "p-b", "enrichi", 1, "0.02"),
        ]
    lignes.append((AUJOURD_HUI, "p-a", "enrichi", 4, "0.10"))
    poser(dsn, *lignes)


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def texte(page: BeautifulSoup, zone: str) -> str:
    return page.select_one(f".{zone}").get_text(" ", strip=True)


# --------------------------------------------------------------------------- #
#  Le jour
# --------------------------------------------------------------------------- #
def test_a4_le_jour_son_plafond_et_sa_jauge(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")
    garnir(base)

    page = soupe(client.get("/admin/couts"))

    jour = texte(page, "zone-cout-jour")
    assert "Aujourd'hui (UTC) : 0,1300 $ sur 0,50 $ — plafond : ok." in jour
    jauge = page.select_one("#jauge-jour")
    assert jauge.name == "progress"
    assert Decimal(jauge["max"]) == Decimal("0.5")
    assert Decimal(jauge["value"]) == Decimal("0.13")
    assert "26,0 %" in jour  # en texte, à côté de la barre
    assert page.select_one("label[for=jauge-jour]").get_text(strip=True)
    assert "budget du jour" not in jour


def test_a4_le_plafond_atteint_a_0_50_exactement(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")
    poser(base, (AUJOURD_HUI, "p-a", "strict", 9, "0.50"))

    jour = texte(soupe(client.get("/admin/couts")), "zone-cout-jour")

    assert "0,5000 $ sur 0,50 $ — plafond : atteint." in jour
    assert "Le conseiller a atteint son budget du jour ; il reprend demain." in jour
    assert "100,0 %" in jour


# --------------------------------------------------------------------------- #
#  Le projet
# --------------------------------------------------------------------------- #
def test_a4_le_total_du_projet_est_la_table_plus_l_anterieur(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")
    garnir(base)  # 35 × 0,03 + 0,10 = 1,15 $

    page = soupe(client.get("/admin/couts"))
    projet = texte(page, "zone-cout-projet")

    # 1,15 + 0,709557 = 1,859557
    assert "Total du projet : 1,8596 $ sur 20 $." in projet
    assert "Dont 0,7096 $ avant le 6 octobre 2026, hors table" in projet
    assert "et 1,1500 $ dans la table (109 appels)" in projet
    assert "Premier jour de la table : 6 octobre 2026." in projet
    jauge = page.select_one("#jauge-projet")
    assert Decimal(jauge["max"]) == 20
    assert Decimal(jauge["value"]) == Decimal("1.859557")
    assert "9,3 %" in projet


def test_a4_l_anterieur_vient_de_la_configuration(doublure, reglages, base_app, base):
    app = demarrer(doublure, configure(reglages, base_app, cout_anterieur_usd=2.5))
    with Navigateur(app, client=CLIENT, raise_server_exceptions=False) as client:
        entrer(client, base, "admin_un")
        poser(base, (AUJOURD_HUI, "p-a", "strict", 1, "0.5"))
        projet = texte(soupe(client.get("/admin/couts")), "zone-cout-projet")

    assert "Total du projet : 3,0000 $ sur 20 $." in projet
    assert "Dont 2,5000 $ avant le 9 novembre 2026" in projet


def test_a4_table_vide_l_anterieur_seul_et_les_etats_vides_nommes(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")

    page = soupe(client.get("/admin/couts"))

    assert "Aujourd'hui (UTC) : 0,0000 $ sur 0,50 $ — plafond : ok." in texte(
        page, "zone-cout-jour"
    )
    projet = texte(page, "zone-cout-projet")
    assert "Total du projet : 0,7096 $ sur 20 $." in projet
    assert "avant la table des coûts" in projet
    assert "La table des coûts est encore vide." in projet
    assert "Aucun appel enregistré." in texte(page, "zone-cout-historique")
    assert page.select(".zone-cout-historique table") == []
    assert texte(page, "zone-cout-repartition").count("Aucun appel enregistré.") == 2


# --------------------------------------------------------------------------- #
#  L'historique et la répartition
# --------------------------------------------------------------------------- #
def test_a4_l_historique_porte_30_jours_et_pas_le_31e(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")
    garnir(base)

    page = soupe(client.get("/admin/couts"))
    jours = [t["datetime"] for t in page.select(".zone-cout-historique time")]

    assert len(jours) == 30
    assert jours[0] == AUJOURD_HUI.isoformat()
    assert jours[-1] == (AUJOURD_HUI - timedelta(days=29)).isoformat()
    assert (AUJOURD_HUI - timedelta(days=30)).isoformat() not in jours
    assert page.select_one(".zone-cout-historique caption").get_text(strip=True)
    assert all(th["scope"] == "col" for th in page.select("thead th"))


def test_a4_les_totaux_par_jour_profil_et_mode(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")
    garnir(base)

    page = soupe(client.get("/admin/couts"))
    aujourd_hui = page.select(".zone-cout-historique tbody")[0]
    lignes = [
        [c.get_text(strip=True) for c in tr.select("td")]
        for tr in aujourd_hui.select("tr")
    ]

    assert lignes == [
        ["p-a", "enrichi", "4", "0,1000 $"],
        ["p-a", "strict", "2", "0,0100 $"],
        ["p-b", "enrichi", "1", "0,0200 $"],
        ["Total du jour", "7", "0,1300 $"],
    ]
    repartition = texte(page, "zone-cout-repartition")
    assert "p-a : 74 appels, 0,4500 $" in repartition
    assert "p-b : 35 appels, 0,7000 $" in repartition
    assert "enrichi : 39 appels, 0,8000 $" in repartition
    assert "strict : 70 appels, 0,3500 $" in repartition


def test_a4_les_lectures_ne_portent_aucun_texte_de_question():
    """La garantie C20 : une ligne de l'historique n'a que jour, profil, mode, appels,
    coût — la table n'a pas d'autre colonne, la requête n'en lit pas d'autre."""
    assert {f.name for f in fields(couts.Ligne)} == {
        "jour",
        "profil",
        "mode",
        "appels",
        "cout_usd",
    }


# --------------------------------------------------------------------------- #
#  Sans base, et les rôles
# --------------------------------------------------------------------------- #
def test_a4_sans_base_la_page_est_un_503(doublure, reglages):
    app = demarrer(doublure, configure(reglages, None))
    with Navigateur(app, client=CLIENT, raise_server_exceptions=False) as client:
        r = client.get("/admin/couts")

    assert r.status_code == 503
    assert "Les comptes sont momentanément indisponibles" in r.text


@pytest.mark.parametrize("role", ["client", "libraire"])
def test_a4_un_autre_role_recoit_403(appli, base, role):
    _, client = appli
    entrer(client, base, f"{role}_un", role)

    assert client.get("/admin/couts").status_code == 403


def test_a4_un_visiteur_est_renvoye_vers_la_connexion(appli):
    _, client = appli

    r = client.get("/admin/couts", follow_redirects=False)

    assert r.headers["location"] == "/connexion?retour=/admin/couts"


def test_a4_l_onglet_couts_apparait(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")

    nav = soupe(client.get("/admin/couts")).select_one("nav.onglets")

    assert nav.select_one("a[aria-current=page]")["href"] == "/admin/couts"
