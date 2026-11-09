"""Les listes du client (E4, lot 4, spec 02) sur une base jetable : un test nommé par
critère de CL2, CL3 et CL4 (préfixes `test_cl2_` … `test_cl4_`), les droits par rôle,
la revalidation dans le catalogue, la borne, les métriques, le balisage des pages
neuves.

L'application réelle, sous le rôle de test membre de `app_rw` ; la doublure de l'API et
du catalogue (le 02) en mémoire — ses séries connues : 101, 102, 103, 1000 ; un hacheur
aux paramètres réduits.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from bs4 import BeautifulSoup
from conftest import HACHEUR_RAPIDE, Navigateur
from doublure_api import CLE_CATALOGUE_ATTENDUE, Doublure
from fastapi import FastAPI

from application import listes
from application.app import creer_application
from application.base import BaseIndisponible
from application.config import DelaisCatalogue, Reglages

CLE = "c" * 48
MDP = "mot de passe factice 1"
CLIENT = ("127.0.0.1", 50000)
QUESTION = "Un manga factice pour débuter ?"


def configure(reglages: Reglages, base_app: str | None, **delais: float) -> Reglages:
    return replace(
        reglages,
        base_dsn=base_app,
        cle_session=CLE,
        catalogue=replace(
            reglages.catalogue,
            cle=CLE_CATALOGUE_ATTENDUE,
            delais=DelaisCatalogue(
                connexion_s=delais.get("connexion_s", 0.5),
                reponse_s=delais.get("reponse_s", 0.5),
            ),
        ),
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


@pytest.fixture
def client(appli) -> Navigateur:
    return appli[1]


def autre(app: FastAPI, port: int) -> Navigateur:
    return Navigateur(app, client=("127.0.0.1", port), raise_server_exceptions=False)


def entrer(client: Navigateur, dsn: str, pseudo: str, role: str = "client") -> int:
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


def geste(client: Navigateur, serie: int | str, etat: str, **champs: str):
    return client.post(
        f"/listes/{serie}", data={"etat": etat, **champs}, follow_redirects=False
    )


def lignes(dsn: str, compte: int | None = None) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as cx:
        if compte is None:
            return cx.execute(
                "SELECT compte_id, series_id, etat FROM application.listes"
                " ORDER BY compte_id, series_id"
            ).fetchall()
        return cx.execute(
            "SELECT series_id, etat FROM application.listes WHERE compte_id = %s"
            " ORDER BY series_id",
            (compte,),
        ).fetchall()


def poser_en_base(dsn: str, compte: int, *entrees: tuple[int, str]) -> None:
    with psycopg.connect(dsn) as cx:
        for i, (serie, etat) in enumerate(entrees):
            cx.execute(
                "INSERT INTO application.listes (compte_id, series_id, etat, ajoute_le)"
                " VALUES (%s, %s, %s, now() - %s * interval '1 minute')",
                (compte, serie, etat, i),
            )


def journal(dsn: str) -> int:
    with psycopg.connect(dsn) as cx:
        (n,) = cx.execute("SELECT count(*) FROM application.journal_actions").fetchone()
    return n


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def zone(page: BeautifulSoup):
    return page.select_one("section.zone-listes")


def boutons(element) -> list[tuple[str, str, str]]:
    return [
        (b["value"], b.get_text(strip=True), b.get("aria-pressed", ""))
        for b in element.select("button[name=etat][aria-pressed]")
    ]


def menu(reponse) -> list[str]:
    nav = soupe(reponse).select_one("header nav")
    assert nav is not None
    return [e.get_text(strip=True) for e in nav.select("a, button")]


def avis(page: BeautifulSoup) -> str | None:
    encart = page.select_one("#avis")
    return None if encart is None else encart.get_text(strip=True)


# --------------------------------------------------------------------------- #
#  CL2 — depuis la fiche : trois états, un seul par série, en un geste
# --------------------------------------------------------------------------- #
def test_cl2_ajouter_changer_meme_etat_retirer_en_un_geste(client, base):
    moi = entrer(client, base, "client_un")

    r = geste(client, 101, "a_lire")
    assert r.status_code == 303
    assert r.headers["location"] == "/series/101?etat=ajoutee"
    assert lignes(base, moi) == [(101, "a_lire")]
    page = soupe(client.get(r.headers["location"]))
    assert avis(page) == "Série ajoutée à votre liste à lire."
    assert "Dans votre liste : à lire" in zone(page).get_text()
    assert boutons(zone(page)) == [
        ("favori", "Favori", "false"),
        ("a_lire", "À lire (actuelle)", "true"),
        ("a_commander", "À commander", "false"),
    ]
    assert zone(page).select_one('button[value="retirer"]').get_text() == (
        "Retirer de mes listes"
    )

    r = geste(client, 101, "favori")
    assert r.headers["location"] == "/series/101?etat=changee"
    assert avis(soupe(client.get(r.headers["location"]))) == (
        "Liste changée : la série est maintenant dans vos favoris."
    )
    r = geste(client, 101, "favori")
    assert r.headers["location"] == "/series/101?etat=inchangee"
    assert avis(soupe(client.get(r.headers["location"]))) == (
        "Déjà dans vos favoris : rien n'a changé."
    )
    assert lignes(base, moi) == [(101, "favori")], "un état par série"

    r = geste(client, 101, "retirer")
    assert r.headers["location"] == "/series/101?etat=retiree"
    assert lignes(base, moi) == []
    page = soupe(client.get(r.headers["location"]))
    assert avis(page) == "Série retirée de vos listes."
    assert "Cette série n'est dans aucune de vos listes." in zone(page).get_text()
    r = geste(client, 101, "retirer")
    assert r.headers["location"] == "/series/101?etat=absente"


def test_cl2_le_changement_remet_la_date_d_ajout(client, base):
    moi = entrer(client, base, "client_un")
    poser_en_base(base, moi, (101, "a_lire"))
    with psycopg.connect(base) as cx:
        cx.execute(
            "UPDATE application.listes SET ajoute_le = now() - interval '2 days'"
        )
    geste(client, 101, "a_commander")
    with psycopg.connect(base) as cx:
        (age,) = cx.execute(
            "SELECT now() - ajoute_le < interval '1 minute' FROM application.listes"
        ).fetchone()
    assert age is True


def test_cl2_un_client_ne_voit_ni_ne_modifie_les_listes_d_un_autre(appli, base):
    app, client = appli
    un = entrer(client, base, "client_un")
    geste(client, 101, "favori")
    deux_nav = autre(app, 50001)
    deux = entrer(deux_nav, base, "client_deux")

    fiche = soupe(deux_nav.get("/series/101"))
    assert "Cette série n'est dans aucune de vos listes." in zone(fiche).get_text()
    assert "Aucune série en favori" in deux_nav.get("/compte/listes").text
    assert geste(deux_nav, 101, "retirer").headers["location"].endswith("absente")
    geste(deux_nav, 101, "a_lire")

    assert lignes(base, un) == [(101, "favori")]
    assert lignes(base, deux) == [(101, "a_lire")]


@pytest.mark.parametrize("role", ["libraire", "admin"])
def test_cl2_un_libraire_et_un_admin_n_ont_ni_zone_ni_geste(client, base, role):
    entrer(client, base, f"{role}_un", role)

    page = soupe(client.get("/series/101"))
    assert zone(page) is None
    assert "Vos listes" not in page.select_one("main").get_text()
    assert geste(client, 101, "favori").status_code == 403
    assert client.get("/compte/listes").status_code == 403
    assert lignes(base) == []


def test_cl2_un_visiteur_voit_l_invitation_et_son_geste_mene_a_la_connexion(client):
    page = soupe(client.get("/series/101"))
    liens = [a["href"] for a in zone(page).select("a")]
    assert liens == ["/connexion?retour=/series/101", "/inscription?retour=/series/101"]
    assert zone(page).select_one("form, button") is None

    r = geste(client, 101, "favori")
    assert r.status_code == 303
    assert r.headers["location"] == "/connexion?retour=/series/101"
    r = geste(client, 101, "favori", retour="/compte/listes")
    assert r.headers["location"] == "/connexion?retour=/compte/listes"


def test_cl2_un_etat_hors_liste_est_un_422_et_rien_n_est_ecrit(client, base):
    entrer(client, base, "client_un")
    for etat in ("", "lu", "FAVORI", "favori "):
        r = geste(client, 101, etat)
        assert r.status_code == 422, etat
        assert "Cette liste n'existe pas" in soupe(r).get_text()
    assert lignes(base) == []


@pytest.mark.parametrize(
    "chemin", ["abc", "0", "-1", "2147483648", "99999999999", "1e3", "１０１"]
)
def test_cl2_un_identifiant_illisible_est_un_404(client, base, chemin):
    entrer(client, base, "client_un")
    assert geste(client, chemin, "favori").status_code == 404
    assert lignes(base) == []


def test_cl2_une_serie_inconnue_du_catalogue_est_un_404_rien_n_est_ecrit(client, base):
    entrer(client, base, "client_un")
    assert geste(client, 555, "favori").status_code == 404
    assert lignes(base) == []


def test_cl2_le_catalogue_muet_refuse_l_ajout_sans_rien_ecrire(client, base, doublure):
    entrer(client, base, "client_un")
    doublure.choisir(catalogue="indisponible")
    r = geste(client, 101, "favori")
    assert r.status_code == 303
    assert r.headers["location"] == "/series/101?etat=indisponible"
    assert lignes(base) == []


def test_cl2_sans_catalogue_configure_l_ajout_est_refuse(doublure, reglages, base_app):
    sans = replace(
        configure(reglages, base_app),
        catalogue=replace(reglages.catalogue, cle=None),
    )
    with Navigateur(demarrer(doublure, sans), client=CLIENT) as client:
        entrer(client, base_app, "client_un")
        r = geste(client, 101, "a_lire")
    assert r.headers["location"] == "/series/101?etat=indisponible"
    assert lignes(base_app) == []


def test_cl2_retirer_ne_demande_pas_le_catalogue(client, base, doublure):
    """Une série que le 02 ne connaît plus, ou un catalogue muet : la série sort quand
    même de la liste (décision 4 : seul un ajout est revalidé)."""
    moi = entrer(client, base, "client_un")
    poser_en_base(base, moi, (555, "favori"))
    doublure.choisir(catalogue="indisponible")
    r = geste(client, 555, "retirer", retour="/compte/listes")
    assert r.headers["location"] == "/compte/listes?etat=retiree"
    assert lignes(base, moi) == []


def test_cl2_la_borne_de_la_liste_est_tenue(client, base, reglages):
    maximum = reglages.comptes.listes_max_par_etat
    assert maximum == 50
    moi = entrer(client, base, "client_un")
    poser_en_base(base, moi, (103, "favori"))
    poser_en_base(base, moi, *((2000 + i, "favori") for i in range(maximum - 1)))
    poser_en_base(base, moi, (101, "a_lire"))

    r = geste(client, 102, "favori")
    assert r.headers["location"] == "/series/102?etat=pleine"
    page = soupe(client.get(r.headers["location"]))
    assert avis(page) == (
        "Cette liste est pleine (50 séries) : retirez-en une avant d'en ajouter une"
        " autre."
    )
    # Un changement vers la liste pleine : refusé ; vers une autre : accepté.
    assert geste(client, 101, "favori").headers["location"].endswith("pleine")
    assert (101, "a_lire") in lignes(base, moi)
    assert geste(client, 101, "a_commander").headers["location"].endswith("changee")
    # Une série déjà dans la liste pleine reste « inchangée », pas « pleine ».
    assert geste(client, 103, "favori").headers["location"].endswith("inchangee")
    assert len([e for e in lignes(base, moi) if e[1] == "favori"]) == maximum


def test_cl2_la_borne_vient_de_la_configuration(doublure, reglages, base_app):
    serree = configure(reglages, base_app)
    serree = replace(serree, comptes=replace(serree.comptes, listes_max_par_etat=1))
    with Navigateur(demarrer(doublure, serree), client=CLIENT) as client:
        entrer(client, base_app, "client_un")
        assert geste(client, 101, "favori").headers["location"].endswith("ajoutee")
        assert geste(client, 102, "favori").headers["location"].endswith("pleine")


@pytest.mark.parametrize(
    "retour",
    ["https://exemple.invalid/x", "//exemple.invalid/x", "/\\exemple.invalid", "x"],
)
def test_cl2_un_retour_externe_est_ramene_a_la_fiche(client, base, retour):
    entrer(client, base, "client_un")
    r = geste(client, 101, "favori", retour=retour)
    assert r.headers["location"] == "/series/101?etat=ajoutee"


def test_cl2_le_retour_garde_sa_requete_et_remplace_son_etat(client, base):
    entrer(client, base, "client_un")
    r = geste(client, 101, "favori", retour="/series/101?etat=ajoutee&x=1")
    assert r.headers["location"] == "/series/101?x=1&etat=ajoutee"


def test_cl2_sans_jeton_403_et_rien_n_est_ecrit(client, base):
    entrer(client, base, "client_un")
    r = client.post(
        "/listes/101",
        data={"etat": "favori", "jeton": "faux"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert lignes(base) == []


def test_cl2_la_fiche_selon_le_role(appli, base):
    app, client = appli
    moi = entrer(client, base, "client_un")
    sans = zone(soupe(client.get("/series/102")))
    assert "Cette série n'est dans aucune de vos listes." in sans.get_text()
    assert [b[2] for b in boutons(sans)] == ["false"] * 3
    assert sans.select_one('button[value="retirer"]') is None
    formulaires = sans.select("form")
    assert len(formulaires) == 1, "un formulaire par zone"
    assert formulaires[0]["action"] == "/listes/102"
    assert formulaires[0].select_one("input[name=retour]")["value"] == "/series/102"
    assert formulaires[0].select_one("input[name=jeton]")["value"]
    poser_en_base(base, moi, (102, "a_commander"))
    avec = zone(soupe(client.get("/series/102")))
    assert "Dans votre liste : à commander" in avec.get_text()
    assert ("a_commander", "À commander (actuelle)", "true") in boutons(avec)


def test_cl2_un_geste_n_ecrit_rien_au_journal_des_actions(client, base):
    entrer(client, base, "client_un")
    avant = journal(base)
    for etat in ("favori", "a_lire", "a_lire", "retirer", "retirer"):
        geste(client, 101, etat)
    assert journal(base) == avant


def test_cl2_le_menu_mes_listes_au_client_seul(appli, base):
    app, client = appli
    assert "Mes listes" not in menu(client.get("/"))
    entrer(client, base, "client_un")
    entrees = menu(client.get("/"))
    assert entrees.index("Mes listes") == entrees.index("Mon compte") - 1
    for port, role in ((50001, "libraire"), (50002, "admin")):
        nav = autre(app, port)
        entrer(nav, base, f"{role}_un", role)
        assert "Mes listes" not in menu(nav.get("/"))


def test_cl2_les_metriques_comptent_ajout_changement_retrait(client, base):
    def valeur(texte: str, action: str) -> float:
        motif = f'application_listes_total{{action="{action}"}} '
        ligne = next(x for x in texte.splitlines() if x.startswith(motif))
        return float(ligne.split()[-1])

    zero = client.get("/metrics").text
    assert [valeur(zero, a) for a in ("ajout", "changement", "retrait")] == [0, 0, 0]
    entrer(client, base, "client_un")
    for etat in ("favori", "favori", "a_lire", "retirer", "retirer"):
        geste(client, 101, etat)
    texte = client.get("/metrics").text
    assert [valeur(texte, a) for a in ("ajout", "changement", "retrait")] == [1, 1, 1]


# --------------------------------------------------------------------------- #
#  CL3 — la page « Mes listes »
# --------------------------------------------------------------------------- #
def sections(page: BeautifulSoup) -> dict[str, BeautifulSoup]:
    return {
        etat: page.select_one(f"section.zone-liste-{etat}") for etat in listes.ETATS
    }


def test_cl3_les_trois_listes_vides_sont_nommees(client, base):
    entrer(client, base, "client_un")
    r = client.get("/compte/listes")
    assert r.status_code == 200
    page = soupe(r)
    assert page.select_one("h1").get_text() == "Mes listes"
    parts = sections(page)
    assert [s.select_one("h2").get_text() for s in parts.values()] == [
        "Favoris (0)",
        "À lire (0)",
        "À commander (0)",
    ]
    assert "Aucune série à lire pour l'instant" in parts["a_lire"].get_text()
    assert page.select_one("main form") is None


def test_cl3_les_listes_garnies_titre_lien_date_et_retirer(client, base, doublure):
    moi = entrer(client, base, "client_un")
    poser_en_base(
        base, moi, (101, "favori"), (102, "a_commander"), (103, "a_commander")
    )
    page = soupe(client.get("/compte/listes"))
    parts = sections(page)

    assert parts["favori"].select_one("h2").get_text() == "Favoris (1)"
    assert "Aucune série à lire" in parts["a_lire"].get_text()
    commander = parts["a_commander"]
    assert commander.select_one("h2").get_text() == "À commander (2)"
    # La plus récente d'abord (102 posée la dernière minute, 103 une minute avant).
    assert [a.get_text() for a in commander.select("li > a")] == [
        "Série factice B",
        "Série factice C",
    ]
    assert [a["href"] for a in commander.select("li > a")] == [
        "/series/102",
        "/series/103",
    ]
    assert all(t.has_attr("datetime") for t in page.select("main time"))
    retirer = commander.select("form")
    assert [f["action"] for f in retirer] == ["/listes/102", "/listes/103"]
    assert {f.select_one("input[name=retour]")["value"] for f in retirer} == {
        "/compte/listes"
    }
    noms = [b["aria-label"] for b in page.select("main button")]
    assert noms[0] == "Retirer Série factice A de mes listes"
    assert len(noms) == len(set(noms)), "deux boutons au même nom"
    assert page.select_one(".zone-catalogue-indisponible, article h2") is None


def test_cl3_retirer_depuis_la_page_avec_l_avis(client, base):
    moi = entrer(client, base, "client_un")
    poser_en_base(base, moi, (101, "favori"), (102, "a_lire"))
    r = geste(client, 101, "retirer", retour="/compte/listes")
    assert r.headers["location"] == "/compte/listes?etat=retiree"
    page = soupe(client.get(r.headers["location"]))
    assert avis(page) == "Série retirée de vos listes."
    assert page.select_one("[autofocus]")["id"] == "avis"
    assert sections(page)["favori"].select_one("h2").get_text() == "Favoris (0)"
    assert lignes(base, moi) == [(102, "a_lire")]


def test_cl3_une_serie_que_le_catalogue_ne_connait_plus_reste_avec_son_lien(
    client, base
):
    moi = entrer(client, base, "client_un")
    poser_en_base(base, moi, (555, "a_lire"), (101, "a_lire"))
    page = soupe(client.get("/compte/listes"))
    liens = {a["href"]: a.get_text() for a in page.select("main li > a")}
    assert liens == {
        "/series/555": "Titre indisponible",
        "/series/101": "Série factice A",
    }
    assert page.select_one("main article") is None, "pas d'encart : le 02 répond"


def test_cl3_le_catalogue_muet_l_encart_et_titre_indisponible(client, base, doublure):
    moi = entrer(client, base, "client_un")
    poser_en_base(base, moi, (101, "favori"), (102, "a_commander"))
    doublure.choisir(catalogue="indisponible")
    r = client.get("/compte/listes")
    assert r.status_code == 200
    page = soupe(r)
    assert "Le catalogue ne répond pas ; vos listes sont intactes." in (
        page.select_one("main article").get_text()
    )
    assert [a.get_text() for a in page.select("main li > a")] == [
        "Titre indisponible",
        "Titre indisponible",
    ]


def test_cl3_les_titres_sont_demandes_en_parallele(doublure, reglages, base_app):
    lente = configure(reglages, base_app, reponse_s=3.0)
    with Navigateur(demarrer(doublure, lente), client=CLIENT) as client:
        moi = entrer(client, base_app, "client_un")
        poser_en_base(base_app, moi, (101, "favori"), (102, "a_lire"), (103, "a_lire"))
        doublure.choisir(catalogue="lent")  # une seconde par fiche
        debut = time.monotonic()
        r = client.get("/compte/listes")
        duree = time.monotonic() - debut
    assert r.status_code == 200
    assert "Série factice C" in r.text
    assert duree < 2.5, f"trois fiches en {duree:.1f} s : pas en parallèle"


def test_cl3_sans_base_la_page_est_un_503(doublure, reglages):
    with Navigateur(
        demarrer(doublure, configure(reglages, None)), client=CLIENT
    ) as client:
        r = client.get("/compte/listes")
    assert r.status_code == 503
    assert "Les comptes sont momentanément indisponibles" in r.text


def test_cl3_un_visiteur_est_renvoye_vers_la_connexion(client):
    r = client.get("/compte/listes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/connexion?retour=/compte/listes"


# --------------------------------------------------------------------------- #
#  CL4 — depuis une carte du conseiller
# --------------------------------------------------------------------------- #
def poser_question(client: Navigateur):
    return client.post("/question", data={"question": QUESTION, "historique": ""})


def test_cl4_sur_chaque_carte_le_geste_pour_un_client(client, base):
    moi = entrer(client, base, "client_un")
    poser_en_base(base, moi, (101, "favori"))
    r = poser_question(client)
    assert r.status_code == 200
    page = soupe(r)
    cartes = page.select("main article:has(> h3)")
    assert len(cartes) == 2
    zones = [c.select_one(".zone-carte-listes") for c in cartes]
    assert all(z is not None for z in zones)
    assert "Dans votre liste : favori" in zones[0].get_text()
    assert "Dans votre liste" not in zones[1].get_text()
    for z, serie in zip(zones, (101, 102), strict=True):
        formulaire = z.select_one("form")
        assert formulaire["action"] == f"/listes/{serie}"
        assert formulaire.select_one("input[name=retour]")["value"] == (
            f"/series/{serie}"
        )
        assert [b[0] for b in boutons(z)] == list(listes.ETATS)
        assert z.select_one('button[value="retirer"]') is None, "pas de retrait"
    assert {f["aria-label"] for f in page.select(".zone-carte-listes form")} == {
        "Mettre Série factice A dans une liste",
        "Mettre Série factice B dans une liste",
    }


def test_cl4_le_geste_d_une_carte_mene_a_la_fiche_avec_l_avis(client, base):
    entrer(client, base, "client_un")
    soupe_ = soupe(poser_question(client))
    retour = soupe_.select_one(".zone-carte-listes input[name=retour]")["value"]
    r = geste(client, 101, "a_lire", retour=retour)
    assert r.headers["location"] == "/series/101?etat=ajoutee"
    page = soupe(client.get(r.headers["location"]))
    assert avis(page) == "Série ajoutée à votre liste à lire."


@pytest.mark.parametrize("role", [None, "libraire", "admin"])
def test_cl4_aucun_formulaire_de_liste_dans_les_cartes_hors_client(client, base, role):
    if role is not None:
        entrer(client, base, f"{role}_un", role)
    r = poser_question(client)
    assert r.status_code == 200
    assert "Série factice A" in r.text, "les cartes sont là"
    assert 'action="/listes/' not in r.text
    assert "zone-carte-listes" not in r.text


def test_cl4_sans_base_un_client_est_un_visiteur_aucun_formulaire(doublure, reglages):
    with Navigateur(
        demarrer(doublure, configure(reglages, None)), client=CLIENT
    ) as client:
        r = poser_question(client)
    assert r.status_code == 200
    assert 'action="/listes/' not in r.text


# --------------------------------------------------------------------------- #
#  Le module de données
# --------------------------------------------------------------------------- #
def test_les_etats_sont_ceux_de_la_contrainte_de_la_027():
    migration = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "027_schema_application.sql"
    ).read_text(encoding="utf-8")
    assert "CHECK (etat IN ('favori', 'a_lire', 'a_commander'))" in migration
    assert listes.ETATS == ("favori", "a_lire", "a_commander")
    assert listes.serie_valide(1) and listes.serie_valide(2_147_483_647)
    assert not listes.serie_valide(0) and not listes.serie_valide(2_147_483_648)


def test_etats_de_lit_plusieurs_series_en_une_requete(appli, base):
    app, client = appli
    moi = entrer(client, base, "client_un")
    poser_en_base(base, moi, (101, "favori"), (103, "a_lire"))
    base_app = app.state.base
    etats = asyncio.run(listes.etats_de(base_app, moi, [101, 102, 103, 0]))
    assert etats == {101: "favori", 103: "a_lire"}
    assert asyncio.run(listes.etats_de(base_app, moi, [])) == {}
    assert asyncio.run(listes.compter(base_app, moi, "a_lire")) == 1


# --------------------------------------------------------------------------- #
#  Le balisage des pages neuves
# --------------------------------------------------------------------------- #
def pages_neuves(appli, base, doublure) -> dict[str, BeautifulSoup]:
    app, client = appli
    rendues = {"serie_listes_visiteur": client.get("/series/101")}
    moi = entrer(client, base, "client_un")
    rendues |= {
        "serie_listes_client": client.get("/series/102"),
        "mes_listes_vides": client.get("/compte/listes"),
    }
    poser_en_base(base, moi, (101, "favori"), (102, "a_lire"), (103, "a_commander"))
    rendues |= {
        "serie_listes_client_etat": client.get("/series/101?etat=ajoutee"),
        "mes_listes_garnies": client.get("/compte/listes?etat=retiree"),
        "reponse_cartes_client": poser_question(client),
    }
    doublure.choisir(catalogue="indisponible")
    rendues["mes_listes_catalogue_indisponible"] = client.get("/compte/listes")
    doublure.choisir(catalogue="nominal")
    return {nom: soupe(r) for nom, r in rendues.items()}


def test_balisage_titres_evitement_langue_mention_et_un_seul_focus(
    appli, base, doublure
):
    for nom, page in pages_neuves(appli, base, doublure).items():
        niveaux = [int(h.name[1]) for h in page.select("main h1, main h2, main h3")]
        assert niveaux and niveaux[0] == 1, nom
        assert all(b - a <= 1 for a, b in zip(niveaux, niveaux[1:], strict=False)), nom
        assert page.select_one('a[href="#contenu"]') is not None, nom
        assert page.select_one("html")["lang"] == "fr", nom
        assert "Vous parlez à une IA." in page.get_text(), nom
        assert page.select_one("script") is None, nom
        assert len(page.select("[autofocus]")) <= 1, nom


def test_balisage_chaque_formulaire_a_son_jeton_et_ses_boutons_nommes(
    appli, base, doublure
):
    pages = pages_neuves(appli, base, doublure)
    for nom, page in pages.items():
        for formulaire in page.select("main form[method=post]"):
            assert formulaire.select_one("input[name=jeton]") is not None, nom
            for bouton in formulaire.select("button"):
                assert bouton.get_text(strip=True) or bouton.get("aria-label"), nom
        for bouton in page.select("main button[aria-pressed]"):
            assert bouton["aria-pressed"] in ("true", "false"), nom
    fiche = pages["serie_listes_client_etat"]
    assert len(fiche.select("section.zone-listes form")) == 1
    pressions = fiche.select("section.zone-listes button[aria-pressed]")
    assert [b["aria-pressed"] for b in pressions] == ["true", "false", "false"]
    cartes = pages["reponse_cartes_client"].select(".zone-carte-listes")
    assert len(cartes) == 2
    assert all(len(c.select("form")) == 1 for c in cartes)


def test_balisage_les_zones_nommees_pour_l_habillage(appli, base, doublure):
    pages = pages_neuves(appli, base, doublure)
    for etat in listes.ETATS:
        assert pages["mes_listes_garnies"].select_one(f"section.zone-liste-{etat}")
    assert pages["serie_listes_visiteur"].select_one("section.zone-listes")
    assert pages["reponse_cartes_client"].select_one("div.zone-carte-listes")
    assert pages["mes_listes_catalogue_indisponible"].select_one("main article")


# --------------------------------------------------------------------------- #
#  Les cas limites
# --------------------------------------------------------------------------- #
def test_la_base_perdue_pour_la_zone_de_la_fiche_le_dit(client, base, monkeypatch):
    entrer(client, base, "client_un")

    async def perdue(*_: object) -> None:
        raise BaseIndisponible("interrompue")

    monkeypatch.setattr(listes, "etat_de", perdue)
    r = client.get("/series/101?etat=ajoutee")
    assert r.status_code == 200
    assert "Vos listes sont momentanément indisponibles." in zone(soupe(r)).get_text()
    assert zone(soupe(r)).select_one("form") is None
    assert avis(soupe(r)) is None


def test_la_base_perdue_pour_les_cartes_aucun_formulaire(client, base, monkeypatch):
    entrer(client, base, "client_un")

    async def perdue(*_: object) -> None:
        raise BaseIndisponible("interrompue")

    monkeypatch.setattr(listes, "etats_de", perdue)
    r = poser_question(client)
    assert r.status_code == 200
    assert "Série factice A" in r.text
    assert 'action="/listes/' not in r.text


def test_un_avis_qui_nomme_une_liste_sans_etat_courant_n_est_pas_dit(client, base):
    entrer(client, base, "client_un")
    for etat in ("ajoutee", "changee", "inchangee", "inconnu"):
        assert avis(soupe(client.get(f"/series/101?etat={etat}"))) is None, etat
    assert avis(soupe(client.get("/series/101?etat=absente"))) == (
        "Cette série n'était dans aucune de vos listes : rien n'a changé."
    )


def test_sans_catalogue_configure_mes_listes_garde_ses_entrees(
    doublure, reglages, base_app
):
    sans = replace(
        configure(reglages, base_app),
        catalogue=replace(reglages.catalogue, cle=None),
    )
    with Navigateur(demarrer(doublure, sans), client=CLIENT) as client:
        moi = entrer(client, base_app, "client_un")
        poser_en_base(base_app, moi, (101, "favori"))
        page = soupe(client.get("/compte/listes"))
    assert [a.get_text() for a in page.select("main li > a")] == ["Titre indisponible"]
    assert "vos listes sont intactes" in page.select_one("main article").get_text()


def test_un_corps_de_formulaire_illisible_est_une_demande_illisible(client, base):
    entrer(client, base, "client_un")
    r = client.post(
        "/listes/101",
        content=b"illisible",
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert r.status_code == 400
    assert lignes(base) == []
