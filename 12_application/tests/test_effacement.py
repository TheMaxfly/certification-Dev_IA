"""Supprimer son compte (E4, lot 4, spec 03, CL5 : le droit à l'effacement) sur une
base jetable : la page par rôle, le mot de passe vérifié par le chemin de la connexion,
la suppression physique en une transaction (compte, listes, compteurs ; une ligne de
journal sans identité), la session fermée, le pseudo libre, les cas limites, le
balisage des pages neuves.

L'application réelle, sous le rôle de test membre de `app_rw` ; la doublure de l'API et
du catalogue en mémoire ; un hacheur aux paramètres réduits.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
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

from application import comptes
from application.app import creer_application
from application.base import Base
from application.comptes import CompteNonSupprimable, DejaSupprime
from application.config import Reglages
from application.metriques import Metriques
from application.sessions import NOM_COOKIE

CLE = "c" * 48
MDP = "mot de passe factice 1"
CLIENT = ("127.0.0.1", 50000)
PHRASE_RESEAU = (
    "Les comptes du réseau sont désactivés par l'administrateur ; ils ne se suppriment"
    " pas, leurs critiques signées doivent rester attribuables."
)


def configure(reglages: Reglages, base_app: str | None) -> Reglages:
    return replace(
        reglages,
        base_dsn=base_app,
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


@pytest.fixture
def client(appli) -> Navigateur:
    return appli[1]


def creer(dsn: str, pseudo: str, role: str = "client") -> int:
    with psycopg.connect(dsn) as cx:
        (ident,) = cx.execute(
            "INSERT INTO application.comptes (pseudo, nom_affiche, role, hachage)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (pseudo, f"Nom {pseudo}", role, HACHEUR_RAPIDE.hacher(MDP)),
        ).fetchone()
    return ident


def connecter(client: Navigateur, pseudo: str) -> None:
    r = client.post(
        "/connexion",
        data={"pseudo": pseudo, "mot_de_passe": MDP},
        follow_redirects=False,
    )
    assert r.status_code == 303, pseudo


def entrer(client: Navigateur, dsn: str, pseudo: str, role: str = "client") -> int:
    ident = creer(dsn, pseudo, role)
    connecter(client, pseudo)
    return ident


def supprimer(client: Navigateur, mot_de_passe: str = MDP, **champs: str):
    return client.post(
        "/compte/supprimer",
        data={"mot_de_passe": mot_de_passe, **champs},
        follow_redirects=False,
    )


def une(dsn: str, requete: str, params: tuple[Any, ...] = ()) -> Any:
    with psycopg.connect(dsn) as cx:
        return cx.execute(requete, params).fetchone()


def toutes(dsn: str, requete: str, params: tuple[Any, ...] = ()) -> list[Any]:
    with psycopg.connect(dsn) as cx:
        return cx.execute(requete, params).fetchall()


def garnir(dsn: str, ident: int) -> None:
    """Trois listes et un compteur de questions : ce que la cascade doit emporter."""
    with psycopg.connect(dsn) as cx:
        for serie, etat in ((101, "favori"), (102, "a_lire"), (103, "a_commander")):
            cx.execute(
                "INSERT INTO application.listes (compte_id, series_id, etat)"
                " VALUES (%s, %s, %s)",
                (ident, serie, etat),
            )
        cx.execute(
            "INSERT INTO application.questions_jour (compte_id, jour, questions)"
            " VALUES (%s, current_date, 1)",
            (ident,),
        )


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def menu(reponse) -> list[str]:
    nav = soupe(reponse).select_one("header nav")
    assert nav is not None
    return [e.get_text(strip=True) for e in nav.select("a, button")]


def session_du_cookie(client: Navigateur) -> dict[str, Any]:
    valeur = client.cookies.get(NOM_COOKIE)
    assert valeur
    return json.loads(base64.b64decode(valeur.split(".")[0]))


# --------------------------------------------------------------------------- #
#  La page de confirmation, par rôle
# --------------------------------------------------------------------------- #
def test_cl5_la_page_de_confirmation_dit_tout_et_exige_le_mot_de_passe(client, base):
    entrer(client, base, "client_un")
    r = client.get("/compte/supprimer")
    assert r.status_code == 200
    page = soupe(r)
    texte = page.select_one("main").get_text()
    assert page.select_one("h1").get_text() == "Supprimer mon compte"
    for attendu in (
        "votre compte, votre pseudo, l'empreinte de votre mot de passe, vos listes",
        "compteurs de questions",
        "Rien d'autre n'existe à votre nom",
        "irréversible",
    ):
        assert attendu in texte, attendu
    champ = page.select_one("main form input#mot_de_passe")
    assert champ["type"] == "password"
    assert champ["autocomplete"] == "current-password"
    assert page.select_one('label[for="mot_de_passe"]') is not None
    bouton = page.select_one("main form button[type=submit]")
    assert bouton.get_text() == "Supprimer définitivement mon compte"
    assert page.select_one('main a[href="/compte/listes"]').get_text() == "Annuler"
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("role", ["libraire", "admin"])
def test_cl5_un_compte_du_reseau_recoit_403_et_la_phrase(client, base, role):
    ident = entrer(client, base, f"{role}_un", role)
    for r in (client.get("/compte/supprimer"), supprimer(client)):
        assert r.status_code == 403
        assert PHRASE_RESEAU in soupe(r).get_text()
    assert (
        une(base, "SELECT count(*) FROM application.comptes WHERE id = %s", (ident,))[0]
        == 1
    )


def test_cl5_un_visiteur_est_renvoye_vers_la_connexion(client):
    r = client.get("/compte/supprimer", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/connexion?retour=/compte/supprimer"
    assert supprimer(client).status_code == 401


def test_cl5_le_lien_est_en_bas_de_mon_compte_pour_le_client_seul(appli, base):
    app, client = appli
    entrer(client, base, "client_un")
    page = soupe(client.get("/compte/mot-de-passe"))
    lien = page.select_one("p.zone-effacement a")
    assert lien["href"] == "/compte/supprimer"
    assert lien.get_text() == "Supprimer mon compte"
    assert "Supprimer mon compte" not in menu(client.get("/")), "pas dans le menu"
    autre = Navigateur(app, client=("127.0.0.1", 50001))
    entrer(autre, base, "libraire_un", "libraire")
    assert soupe(autre.get("/compte/mot-de-passe")).select_one(".zone-effacement") is (
        None
    )


# --------------------------------------------------------------------------- #
#  Le mot de passe, par le chemin de la connexion
# --------------------------------------------------------------------------- #
def test_cl5_un_mot_de_passe_faux_422_rien_supprime_et_l_echec_compte(client, base):
    ident = entrer(client, base, "client_un")
    garnir(base, ident)
    r = supprimer(client, "pas le bon mot de passe")
    assert r.status_code == 422
    page = soupe(r)
    assert (
        page.select_one("#mot_de_passe-erreur").get_text() == "Mot de passe incorrect."
    )
    assert page.select_one("#mot_de_passe")["aria-invalid"] == "true"
    assert page.select_one("[autofocus]")["id"] == "mot_de_passe"
    (echecs,) = une(
        base, "SELECT echecs_connexion FROM application.comptes WHERE id = %s", (ident,)
    )
    assert echecs == 1, "le compteur de la connexion"
    assert une(base, "SELECT count(*) FROM application.listes")[0] == 3
    assert (
        une(
            base,
            "SELECT count(*) FROM application.journal_actions WHERE"
            " action = 'compte_supprime'",
        )[0]
        == 0
    )


def test_cl5_le_verrou_de_la_connexion_vaut_ici(client, base, reglages):
    ident = entrer(client, base, "client_un")
    for _ in range(reglages.comptes.echecs_avant_verrou):
        assert supprimer(client, "faux faux faux").status_code == 422
    # Verrouillé : même le bon mot de passe est refusé, du même message.
    r = supprimer(client)
    assert r.status_code == 422
    assert "Mot de passe incorrect." in soupe(r).get_text()
    assert (
        une(base, "SELECT count(*) FROM application.comptes WHERE id = %s", (ident,))[0]
        == 1
    )


def test_cl5_sans_jeton_403_et_rien_supprime(client, base):
    ident = entrer(client, base, "client_un")
    r = supprimer(client, jeton="faux")
    assert r.status_code == 403
    assert (
        une(base, "SELECT count(*) FROM application.comptes WHERE id = %s", (ident,))[0]
        == 1
    )


# --------------------------------------------------------------------------- #
#  La suppression : physique, en une transaction, sans identité au journal
# --------------------------------------------------------------------------- #
def test_cl5_la_suppression_emporte_compte_listes_et_compteurs(client, base):
    ident = entrer(client, base, "client_un")
    garnir(base, ident)
    r = supprimer(client)

    assert r.status_code == 303
    assert r.headers["location"] == "/?etat=compte_supprime"
    assert (
        une(base, "SELECT count(*) FROM application.comptes WHERE id = %s", (ident,))[0]
        == 0
    )
    assert (
        une(
            base,
            "SELECT count(*) FROM application.listes WHERE compte_id = %s",
            (ident,),
        )[0]
        == 0
    )
    assert (
        une(
            base,
            "SELECT count(*) FROM application.questions_jour WHERE compte_id = %s",
            (ident,),
        )[0]
        == 0
    )
    assert toutes(
        base,
        "SELECT par_compte_id, objet_type, objet_id, details FROM"
        " application.journal_actions WHERE action = 'compte_supprime'",
    ) == [(None, "compte", ident, {})]


def test_cl5_apres_la_suppression_l_avis_et_le_menu_d_un_visiteur(client, base):
    entrer(client, base, "client_un")
    r = client.get(supprimer(client).headers["location"])
    page = soupe(r)
    assert (
        page.select_one("#avis").get_text(strip=True) == "Votre compte a été supprimé."
    )
    assert page.select_one("[autofocus]")["id"] == "avis"
    assert "Connexion" in menu(r) and "Mes listes" not in menu(r)


def test_cl5_un_cookie_copie_avant_ne_vaut_plus_rien(appli, base):
    app, client = appli
    entrer(client, base, "client_un")
    copie = Navigateur(app, client=("127.0.0.1", 50001))
    copie.cookies.set(NOM_COOKIE, client.cookies.get(NOM_COOKIE))
    assert "Mes listes" in menu(copie.get("/"))
    supprimer(client)
    r = copie.get("/compte/listes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/connexion")
    assert "Connexion" in menu(copie.get("/"))


def test_cl5_le_pseudo_redevient_libre(client, base):
    ancien = entrer(client, base, "client_un")
    supprimer(client)
    r = client.post(
        "/inscription",
        data={"pseudo": "client_un", "mot_de_passe": MDP, "confirmation": MDP},
        follow_redirects=False,
    )
    assert r.status_code == 303
    (neuf,) = une(base, "SELECT id FROM application.comptes WHERE pseudo = 'client_un'")
    assert neuf != ancien


def test_cl5_le_compteur_des_inscriptions_survit_a_la_suppression(client, base):
    r = client.post(
        "/inscription",
        data={"pseudo": "client_inscrit", "mot_de_passe": MDP, "confirmation": MDP},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(session_du_cookie(client)["inscriptions"]) == 1
    supprimer(client)
    session = session_du_cookie(client)
    assert "compte" not in session
    assert len(session["inscriptions"]) == 1, "un compte supprimé n'en rend pas un"


def test_cl5_la_metrique_et_le_journal_sans_pseudo_ni_mot_de_passe(
    client, base, caplog
):
    entrer(client, base, "client_secret_un")
    caplog.set_level(logging.INFO, logger="application")
    supprimer(client, "faux faux faux")
    supprimer(client)
    texte = journal_complet(caplog)
    assert "compte_supprime" in texte and "suppression_refusee" in texte
    assert "client_secret_un" not in texte
    assert MDP not in texte and "faux faux faux" not in texte
    assert "application_comptes_supprimes_total 1.0" in client.get("/metrics").text


def test_cl5_sans_base_la_page_est_un_503(doublure, reglages):
    with Navigateur(
        demarrer(doublure, configure(reglages, None)), client=CLIENT
    ) as client:
        r = client.get("/compte/supprimer")
    assert r.status_code == 503
    assert "Les comptes sont momentanément indisponibles" in r.text


def test_cl5_un_delete_a_zero_ligne_meme_sortie_journal_inchange(
    client, base, monkeypatch
):
    """Une autre session vient de supprimer le compte entre deux requêtes : la
    transaction est annulée (`DejaSupprime`), la sortie est la même."""
    entrer(client, base, "client_un")

    async def deja(_base: Base, _ident: int) -> None:
        raise DejaSupprime

    monkeypatch.setattr(comptes, "supprimer", deja)
    r = supprimer(client)
    assert r.headers["location"] == "/?etat=compte_supprime"
    assert "application_comptes_supprimes_total 0.0" in client.get("/metrics").text


def test_cl5_le_module_annule_tout_si_le_compte_n_existe_plus(base_app, base):
    avant = une(base, "SELECT count(*) FROM application.journal_actions")[0]
    with pytest.raises(DejaSupprime):
        asyncio.run(comptes.supprimer(Base(base_app, 3.0, Metriques()), 999_999))
    assert une(base, "SELECT count(*) FROM application.journal_actions")[0] == avant


def test_cl5_la_base_refuse_un_compte_qui_a_des_critiques(base_app, base):
    """Cas théorique (un client n'a pas de critique) tenu par la base : `RESTRICT`."""
    auteur = creer(base, "libraire_un", "libraire")
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO application.critiques (compte_id, series_id, texte, note)"
            " VALUES (%s, 101, %s, 8)",
            (auteur, "Texte factice d'une critique, assez long pour la contrainte."),
        )
    avant = une(base, "SELECT count(*) FROM application.journal_actions")[0]
    with pytest.raises(CompteNonSupprimable):
        asyncio.run(comptes.supprimer(Base(base_app, 3.0, Metriques()), auteur))
    assert une(base, "SELECT count(*) FROM application.journal_actions")[0] == avant
    assert (
        une(base, "SELECT count(*) FROM application.comptes WHERE id = %s", (auteur,))[
            0
        ]
        == 1
    )


def test_cl5_le_refus_de_la_base_est_une_page_nommee(client, base, monkeypatch):
    ident = entrer(client, base, "client_un")

    async def refuse(_base: Base, _ident: int) -> None:
        raise CompteNonSupprimable

    monkeypatch.setattr(comptes, "supprimer", refuse)
    r = supprimer(client)
    assert r.status_code == 409
    assert "Ce compte ne peut pas être supprimé." in soupe(r).get_text()
    assert (
        une(base, "SELECT count(*) FROM application.comptes WHERE id = %s", (ident,))[0]
        == 1
    )


def test_cl5_a_propos_dit_ou_comment_quoi_et_le_delai(client):
    page = soupe(client.get("/a-propos"))
    section = page.select_one("section#effacement")
    texte = section.get_text()
    assert section.select_one("h2").get_text() == "Supprimer votre compte"
    for attendu in (
        "mot de passe",
        "immédiate",
        "sans délai de rétention",
        "redevient libre",
        "sans identité",
        "désactive",
    ):
        assert attendu in texte, attendu


# --------------------------------------------------------------------------- #
#  Le balisage des pages neuves
# --------------------------------------------------------------------------- #
def pages_neuves(appli, base) -> dict[str, BeautifulSoup]:
    app, client = appli
    entrer(client, base, "client_un")
    rendues = {
        "compte_supprimer": client.get("/compte/supprimer"),
        "compte_supprimer_refuse": supprimer(client, "faux faux faux"),
    }
    libraire = Navigateur(app, client=("127.0.0.1", 50001))
    entrer(libraire, base, "libraire_un", "libraire")
    rendues["compte_supprimer_interdit"] = libraire.get("/compte/supprimer")
    rendues["accueil_apres_suppression"] = client.get(
        supprimer(client).headers["location"]
    )
    return {nom: soupe(r) for nom, r in rendues.items()}


def test_balisage_titres_evitement_langue_mention_et_focus(appli, base):
    for nom, page in pages_neuves(appli, base).items():
        niveaux = [int(h.name[1]) for h in page.select("main h1, main h2, main h3")]
        assert niveaux and niveaux[0] == 1, nom
        assert all(b - a <= 1 for a, b in zip(niveaux, niveaux[1:], strict=False)), nom
        assert page.select_one('a[href="#contenu"]') is not None, nom
        assert page.select_one("html")["lang"] == "fr", nom
        assert "Vous parlez à une IA." in page.get_text(), nom
        assert page.select_one("script:not([src='/statique/attente.js'])") is None, nom
        assert len(page.select("[autofocus]")) <= 1, nom
        for formulaire in page.select("main form[method=post]"):
            assert formulaire.select_one("input[name=jeton]") is not None, nom
        for champ in page.select("main input:not([type=hidden])"):
            assert page.select_one(f'label[for="{champ["id"]}"]') is not None, nom


def test_balisage_l_erreur_est_reliee_au_champ(appli, base):
    page = pages_neuves(appli, base)["compte_supprimer_refuse"]
    champ = page.select_one("#mot_de_passe")
    for ident in champ["aria-describedby"].split():
        assert page.select_one(f"#{ident}") is not None
    assert page.select_one("section.zone-effacement") is not None
