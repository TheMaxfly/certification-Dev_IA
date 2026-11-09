"""E4, lot 5, spec 01 — le profil IA actif (A2) et la barre d'onglets de
l'administration.

Base jetable, doublure de l'API : le scénario `administration` de `/v1/info` porte
quatre profils — deux proposés (`mistral-large-2512`, `gpt-6-luna`), un exclu par la
configuration (`mistral-large-4`), un indisponible (`gpt-6-sol`). La doublure garde
chaque demande reçue : le profil qui part avec une question s'y lit.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import httpx
import psycopg
import pytest
from bs4 import BeautifulSoup
from conftest import HACHEUR_RAPIDE, Navigateur, journal_complet
from doublure_api import CLE_CATALOGUE_ATTENDUE, Doublure
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from application import pages_admin
from application.app import creer_application
from application.config import Reglages

CLE = "c" * 48
MDP = "mot de passe factice 1"
CLIENT = ("127.0.0.1", 50000)
QUESTION = "Un manga factice pour débuter ?"
PROPOSES = ["mistral-large-2512", "gpt-6-luna"]


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
    doublure.choisir(info="administration")
    app = demarrer(doublure, configure(reglages, base_app))
    with Navigateur(app, client=CLIENT, raise_server_exceptions=False) as client:
        yield app, client


def autre(app: FastAPI, port: int) -> Navigateur:
    return Navigateur(app, client=("127.0.0.1", port), raise_server_exceptions=False)


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


def regler(client: Navigateur, profil: str, **champs: str):
    return client.post(
        "/admin/reglages", data={"profil": profil, **champs}, follow_redirects=False
    )


def reglages_en_base(dsn: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as cx:
        return cx.execute(
            "SELECT cle, valeur, modifie_par FROM application.reglages ORDER BY cle"
        ).fetchall()


def journal_reglages(dsn: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as cx:
        return cx.execute(
            "SELECT par_compte_id, action, objet_type, objet_id, details"
            " FROM application.journal_actions WHERE action = 'profil_actif_change'"
            " ORDER BY id"
        ).fetchall()


def radios(page: BeautifulSoup) -> list[tuple[str, bool]]:
    return [
        (r["value"], r.has_attr("checked"))
        for r in page.select(".zone-profil-actif input[type=radio]")
    ]


def poser(client: Navigateur) -> httpx.Response:
    return client.post(
        "/question",
        data={"question": QUESTION, "temperature": "0.0", "historique": ""},
    )


# --------------------------------------------------------------------------- #
#  A2 — la page
# --------------------------------------------------------------------------- #
def test_a2_la_page_liste_les_profils_proposes_indisponibles_a_part_exclus_comptes(
    appli, base
):
    _, client = appli
    entrer(client, base, "admin_un")
    r = client.get("/admin/reglages")
    page = soupe(r)

    assert r.status_code == 200
    assert radios(page) == [("mistral-large-2512", True), ("gpt-6-luna", False)]
    zone = page.select_one(".zone-profil-actif")
    texte = zone.get_text(" ", strip=True)
    # Le défaut de l'application, dit tel quel, coché ; fournisseur, hébergement,
    # statut.
    assert "(défaut de l'application)" in texte
    assert "Mistral (Union européenne)" in texte and "OpenAI" in texte
    assert "modèle modele-de-gpt-6-luna, stable" in texte
    assert "Aucun profil n'a été choisi" in texte
    # L'indisponible : à part, sans bouton ; l'exclu : compté, jamais nommé.
    indisponibles = page.select_one(".zone-profils-indisponibles")
    assert "gpt-6-sol" in indisponibles.get_text()
    assert indisponibles.select("input") == []
    assert "clé absente ou fournisseur injoignable" in indisponibles.get_text()
    assert "exclus par la configuration de l'application : 1" in texte
    assert "mistral-large-4" not in r.text
    assert len(page.select("main form")) == 1
    assert page.select_one("fieldset legend").get_text(strip=True)


def test_a2_appliquer_ecrit_le_reglage_le_journal_et_l_avis(appli, base):
    _, client = appli
    admin = entrer(client, base, "admin_un")

    r = regler(client, "gpt-6-luna")

    assert r.status_code == 303
    assert r.headers["location"] == "/admin/reglages?etat=applique"
    assert reglages_en_base(base) == [("profil_actif", "gpt-6-luna", admin)]
    assert journal_reglages(base) == [
        (admin, "profil_actif_change", "reglage", None, {"profil": "gpt-6-luna"})
    ]
    page = soupe(client.get(r.headers["location"]))
    assert page.select_one("#avis").get_text(strip=True) == "Profil actif : gpt-6-luna."
    assert radios(page) == [("mistral-large-2512", False), ("gpt-6-luna", True)]
    texte = page.select_one(".zone-profil-actif").get_text(" ", strip=True)
    assert "Choisi par Nom admin_un le" in texte and "UTC" in texte
    assert "(défaut de l'application)" not in texte


def test_a2_le_meme_profil_ne_change_rien_ni_n_ecrit_au_journal(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")
    regler(client, "gpt-6-luna")

    r = regler(client, "gpt-6-luna")

    assert r.headers["location"] == "/admin/reglages?etat=inchange"
    assert len(journal_reglages(base)) == 1
    avis = soupe(client.get(r.headers["location"])).select_one("#avis")
    assert avis.get_text(strip=True) == "Profil actif inchangé : gpt-6-luna."


def test_a2_un_second_changement_remplace_la_valeur_et_ajoute_une_ligne(appli, base):
    _, client = appli
    admin = entrer(client, base, "admin_un")
    regler(client, "gpt-6-luna")
    regler(client, "mistral-large-2512")

    assert reglages_en_base(base) == [("profil_actif", "mistral-large-2512", admin)]
    assert [ligne[4] for ligne in journal_reglages(base)] == [
        {"profil": "gpt-6-luna"},
        {"profil": "mistral-large-2512"},
    ]


@pytest.mark.parametrize(
    "profil",
    ["profil-inconnu", "gpt-6-sol", "mistral-large-4", ""],
    ids=["inconnu", "indisponible", "exclu", "vide"],
)
def test_a2_un_profil_hors_liste_est_un_422_et_rien_n_est_ecrit(appli, base, profil):
    _, client = appli
    entrer(client, base, "admin_un")

    r = regler(client, profil)

    assert r.status_code == 422
    page = soupe(r)
    assert page.select_one("#profil-erreur").get_text(strip=True) == (
        "Ce profil n'est pas proposé : rien n'a été enregistré."
    )
    assert page.select_one("fieldset")["aria-describedby"] == "profil-erreur"
    assert reglages_en_base(base) == [] and journal_reglages(base) == []


def test_a2_sans_jeton_403_et_rien_n_est_ecrit(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")

    r = regler(client, "gpt-6-luna", jeton="faux")

    assert r.status_code == 403
    assert reglages_en_base(base) == []


@pytest.mark.parametrize("role", ["client", "libraire"])
def test_a2_un_autre_role_recoit_403_et_rien_n_est_ecrit(appli, base, role):
    _, client = appli
    entrer(client, base, f"{role}_un", role)

    assert client.get("/admin/reglages").status_code == 403
    assert regler(client, "gpt-6-luna").status_code == 403
    assert reglages_en_base(base) == []


def test_a2_un_visiteur_est_renvoye_vers_la_connexion(appli, base):
    _, client = appli

    r = client.get("/admin/reglages", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/connexion?retour=/admin/reglages"
    assert regler(client, "gpt-6-luna").status_code == 401
    assert reglages_en_base(base) == []


def test_a2_sans_base_la_page_est_un_503(doublure, reglages):
    app = demarrer(doublure, configure(reglages, None))
    with Navigateur(app, client=CLIENT, raise_server_exceptions=False) as client:
        assert client.get("/admin/reglages").status_code == 503


def test_a2_api_injoignable_encart_le_reglage_seul_sans_formulaire(
    appli, base, doublure
):
    _, client = appli
    entrer(client, base, "admin_un")
    regler(client, "gpt-6-luna")
    doublure.choisir(info="erreur_interne")

    r = client.get("/admin/reglages")
    page = soupe(r)

    assert r.status_code == 503
    assert "API du modèle injoignable" in page.select_one("[role=alert]").get_text()
    assert page.select("main form") == []
    texte = page.select_one(".zone-profil-actif").get_text(" ", strip=True)
    assert "Réglage enregistré : gpt-6-luna." in texte
    assert "Choisi par Nom admin_un" in texte
    # Un POST, l'API muette : rien n'est validé, rien n'est écrit.
    assert regler(client, "mistral-large-2512").status_code == 503
    assert reglages_en_base(base)[0][1] == "gpt-6-luna"


def test_a2_api_injoignable_sans_reglage_le_dit(appli, base, doublure):
    _, client = appli
    entrer(client, base, "admin_un")
    doublure.choisir(info="erreur_interne")

    texte = soupe(client.get("/admin/reglages")).get_text(" ", strip=True)

    assert "Aucun réglage enregistré : le défaut de l'API s'applique." in texte


def test_a2_un_reglage_dont_l_auteur_a_ete_supprime_garde_sa_date(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")
    regler(client, "gpt-6-luna")
    with psycopg.connect(base) as cx:
        cx.execute("UPDATE application.reglages SET modifie_par = NULL")

    texte = soupe(client.get("/admin/reglages")).get_text(" ", strip=True)

    assert "par un compte supprimé depuis" in texte


def test_a2_aucun_profil_propose_la_page_le_dit_sans_formulaire(appli, base, doublure):
    _, client = appli
    entrer(client, base, "admin_un")
    doublure.choisir(info="aucun_profil")

    page = soupe(client.get("/admin/reglages"))

    assert page.select("main form") == []
    assert "Aucun profil de modèle de langage" in page.get_text()


# --------------------------------------------------------------------------- #
#  A2 — le profil actif appliqué à toute l'application (D12)
# --------------------------------------------------------------------------- #
def test_a2_le_profil_actif_part_avec_la_question_d_un_visiteur(appli, base, doublure):
    app, client = appli
    entrer(client, base, "admin_un")
    regler(client, "gpt-6-luna")
    visiteur = autre(app, 50001)

    assert poser(visiteur).status_code == 200

    assert doublure.corps_recus[-1]["profil"] == "gpt-6-luna"


def test_a2_le_profil_actif_part_avec_la_question_d_un_client(appli, base, doublure):
    app, client = appli
    entrer(client, base, "admin_un")
    regler(client, "gpt-6-luna")
    lecteur = autre(app, 50001)
    entrer(lecteur, base, "client_un", "client")

    assert poser(lecteur).status_code == 200

    assert doublure.corps_recus[-1]["profil"] == "gpt-6-luna"


def test_a2_sans_reglage_le_defaut_de_l_api_part(appli, doublure):
    _, client = appli

    assert poser(client).status_code == 200

    assert doublure.corps_recus[-1]["profil"] == "mistral-large-2512"


def test_a2_un_libraire_le_trouve_preselectionne_et_peut_en_choisir_un_autre(
    appli, base, doublure
):
    app, client = appli
    entrer(client, base, "admin_un")
    regler(client, "gpt-6-luna")
    libraire = autre(app, 50001)
    entrer(libraire, base, "libraire_un", "libraire")

    choisi = soupe(libraire.get("/")).select_one("#profil option[selected]")
    assert choisi["value"] == "gpt-6-luna"
    assert "(actif)" in choisi.get_text()

    r = libraire.post(
        "/question",
        data={
            "question": QUESTION,
            "profil": "mistral-large-2512",
            "temperature": "0.0",
            "historique": "",
        },
    )

    assert r.status_code == 200
    assert doublure.corps_recus[-1]["profil"] == "mistral-large-2512"
    assert reglages_en_base(base)[0][1] == "gpt-6-luna"
    assert len(journal_reglages(base)) == 1


def test_a2_l_etat_du_service_et_la_page_a_propos_nomment_le_profil_actif(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")
    regler(client, "gpt-6-luna")

    accueil = soupe(client.get("/")).get_text(" ", strip=True)
    a_propos = soupe(client.get("/a-propos")).get_text(" ", strip=True)

    assert "profil actif : gpt-6-luna (modèle modele-de-gpt-6-luna)" in accueil
    assert "gpt-6-luna (actif)" in a_propos
    assert "mistral-large-2512 (actif)" not in a_propos


def test_a2_un_profil_actif_devenu_indisponible_part_avec_le_defaut_et_l_admin_le_voit(
    appli, base, doublure, caplog
):
    caplog.set_level(logging.INFO, logger="application")
    app, client = appli
    entrer(client, base, "admin_un")
    regler(client, "gpt-6-luna")
    doublure.choisir(info="profil_indisponible")  # gpt-6-luna indisponible
    visiteur = autre(app, 50001)

    page_visiteur = poser(visiteur)

    assert page_visiteur.status_code == 200
    assert doublure.corps_recus[-1]["profil"] == "mistral-large-2512"
    assert soupe(page_visiteur).select(".zone-profil-actif-indisponible") == []
    assert "profil_actif_indisponible" in journal_complet(caplog)
    encart = soupe(client.get("/")).select_one(".zone-profil-actif-indisponible")
    assert "gpt-6-luna, est indisponible" in encart.get_text()
    assert "défaut de l'API (mistral-large-2512)" in encart.get_text()
    reglages_page = soupe(client.get("/admin/reglages"))
    assert reglages_page.select_one(".zone-profil-actif-indisponible") is not None
    # Le réglage est gardé : il revient quand le profil revient.
    assert reglages_en_base(base)[0][1] == "gpt-6-luna"
    doublure.choisir(info="administration")
    poser(visiteur)
    assert doublure.corps_recus[-1]["profil"] == "gpt-6-luna"


def test_a2_base_injoignable_pendant_une_question_le_defaut_part(
    doublure, reglages, caplog
):
    caplog.set_level(logging.INFO, logger="application")
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        port = prise.getsockname()[1]
    dsn = f"postgresql://personne@127.0.0.1:{port}/apimanga_essai"
    app = demarrer(doublure, configure(reglages, dsn))
    with Navigateur(app, client=CLIENT, raise_server_exceptions=False) as client:
        r = poser(client)

    assert r.status_code == 200
    assert doublure.corps_recus[-1]["profil"] == "mistral-large-2512"
    assert "profil_actif_non_lu" in journal_complet(caplog)


def test_a2_sans_base_configuree_rien_n_est_lu_ni_journalise(
    doublure, reglages, caplog
):
    caplog.set_level(logging.INFO, logger="application")
    app = demarrer(doublure, configure(reglages, None))
    with Navigateur(app, client=CLIENT, raise_server_exceptions=False) as client:
        assert poser(client).status_code == 200
        assert client.get("/sante").json()["profil_actif"] == "defaut"

    assert "profil_actif_non_lu" not in journal_complet(caplog)


def test_a2_sante_dit_le_profil_actif(appli, base, doublure):
    _, client = appli
    assert client.get("/sante").json()["profil_actif"] == "defaut"
    entrer(client, base, "admin_un")
    regler(client, "gpt-6-luna")

    assert client.get("/sante").json()["profil_actif"] == "gpt-6-luna"
    doublure.choisir(info="profil_indisponible")
    client.get("/")  # la liste relue : gpt-6-luna n'est plus proposé
    assert client.get("/sante").json()["profil_actif"] == "defaut"


def test_a2_la_metrique_du_profil_actif(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")
    regler(client, "gpt-6-luna")
    client.get("/")

    texte = client.get("/metrics").text

    assert 'application_profil_actif_info{profil="gpt-6-luna"} 1.0' in texte
    assert 'application_profil_actif_info{profil="mistral-large-2512"} 0.0' in texte


# --------------------------------------------------------------------------- #
#  A1 (suite) — la barre d'onglets et le menu
# --------------------------------------------------------------------------- #
def chemins_presents(app: FastAPI) -> list[str]:
    presents = pages_admin.chemins_en_get(app.routes)
    return [chemin for chemin, _ in pages_admin.ONGLETS if chemin in presents]


def test_a1_chaque_page_admin_porte_les_onglets_et_marque_la_sienne(appli, base):
    app, client = appli
    entrer(client, base, "admin_un")
    attendus = chemins_presents(app)
    assert attendus[:3] == ["/admin/comptes", "/admin/critiques", "/admin/reglages"]

    for chemin in attendus:
        page = soupe(client.get(chemin))
        nav = page.select_one("nav.onglets")
        assert nav["aria-label"] == "Administration", chemin
        assert [a["href"] for a in nav.select("a")] == attendus, chemin
        courants = nav.select("a[aria-current=page]")
        assert [a["href"] for a in courants] == [chemin], chemin


def test_a1_les_libelles_des_onglets(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")

    nav = soupe(client.get("/admin/reglages")).select_one("nav.onglets")
    libelles = [a.get_text(strip=True) for a in nav.select("a")]

    assert libelles[:3] == ["Comptes", "Modération", "Réglages"]


def test_a1_un_onglet_n_apparait_que_si_sa_route_existe():
    app = FastAPI()

    @app.get("/admin/comptes", response_class=HTMLResponse)
    async def comptes() -> str:
        return ""

    @app.post("/admin/cles")
    async def cles() -> str:
        return ""

    textes = {cle: cle for _, cle in pages_admin.ONGLETS}
    vus = pages_admin.onglets(app, "/admin/comptes", textes)

    assert [(o.chemin, o.courant) for o in vus] == [("/admin/comptes", True)]


def test_a1_hors_de_l_administration_aucun_onglet(appli, base):
    _, client = appli
    entrer(client, base, "admin_un")

    for chemin in ("/", "/a-propos", "/compte/mot-de-passe"):
        assert soupe(client.get(chemin)).select("nav.onglets") == [], chemin


def menu(reponse) -> list[str]:
    nav = soupe(reponse).select_one("header nav")
    return [e.get_text(strip=True) for e in nav.select("a, button")]


@pytest.mark.parametrize(
    ("role", "entrees"),
    [("admin", 1), ("libraire", 0), ("client", 0), (None, 0)],
)
def test_a1_une_seule_entree_administration_pour_l_admin(appli, base, role, entrees):
    _, client = appli
    if role is not None:
        entrer(client, base, f"{role}_un", role)

    lues = menu(client.get("/"))

    assert lues.count("Administration") == entrees
    assert "Comptes" not in lues and "Critiques" not in lues
    if entrees:
        lien = soupe(client.get("/")).select_one("header nav a[href='/admin/comptes']")
        assert lien.get_text(strip=True) == "Administration"
