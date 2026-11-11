"""Les écrans des critiques signées (E4, lot 3, specs 02 et 03) sur une base jetable :
un test nommé par critère d'acceptation de L2, L3, L4, L5, A5 et de la partie V3
(préfixes `test_l2_` … `test_v3_`), les parcours d'intégration, les droits, la saisie,
le balisage des pages neuves.

L'application réelle, sous le rôle de test membre de `app_rw` ; la doublure de l'API et
du catalogue (le 02) en mémoire ; un hacheur aux paramètres réduits. Textes factices et
neutres, jamais une vraie critique.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import httpx
import psycopg
import pytest
from bs4 import BeautifulSoup
from conftest import HACHEUR_RAPIDE, Navigateur, jeton_du_cookie, journal_complet
from doublure_api import CLE_CATALOGUE_ATTENDUE, Doublure
from fastapi import FastAPI

from application import critiques
from application.app import creer_application
from application.config import DelaisCatalogue, Reglages
from application.critiques import CritiqueIntrouvable, CritiqueInvalide, PasLAuteur

CLE = "c" * 48
MDP = "mot de passe factice 1"
CLIENT = ("127.0.0.1", 50000)
TEXTE = (
    "Texte factice d'une critique de libraire, assez long pour tenir dans les bornes"
    " de l'application."
)
AUTRE = (
    "Un autre texte factice, modifié, assez long lui aussi pour tenir dans les bornes."
)
URL_1 = "https://catalogue.invalid/serie/101/volume/1"
URL_2 = "https://catalogue.invalid/serie/101/volume/2"
URL_AUTRE_SERIE = "https://catalogue.invalid/serie/102/volume/1"
DATE = re.compile(r"le (1er|\d{1,2}) [a-zéû]+ \d{4}")


def configure(reglages: Reglages, base_app: str, **critiques: Any) -> Reglages:
    return replace(
        reglages,
        base_dsn=base_app,
        cle_session=CLE,
        catalogue=replace(
            reglages.catalogue,
            cle=CLE_CATALOGUE_ATTENDUE,
            delais=DelaisCatalogue(connexion_s=0.5, reponse_s=0.5),
        ),
        critiques=replace(reglages.critiques, **critiques),
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


def autre_client(app: FastAPI, port: int = 50001) -> Navigateur:
    """Un second navigateur sur l'application déjà démarrée (sans son cycle de vie)."""
    return Navigateur(app, client=("127.0.0.1", port), raise_server_exceptions=False)


def creer_compte(
    dsn: str, pseudo: str, role: str = "libraire", actif: bool = True
) -> int:
    with psycopg.connect(dsn) as cx:
        (ident,) = cx.execute(
            "INSERT INTO application.comptes"
            " (pseudo, nom_affiche, role, hachage, actif)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (pseudo, f"Nom {pseudo}", role, HACHEUR_RAPIDE.hacher(MDP), actif),
        ).fetchone()
    return ident


def connecter(client: Navigateur, pseudo: str) -> None:
    r = client.post(
        "/connexion",
        data={"pseudo": pseudo, "mot_de_passe": MDP},
        follow_redirects=False,
    )
    assert r.status_code == 303, pseudo


def entrer(client: Navigateur, dsn: str, pseudo: str, role: str = "libraire") -> int:
    ident = creer_compte(dsn, pseudo, role)
    connecter(client, pseudo)
    return ident


def ecrire(client: Navigateur, series_id: int | str = 101, **champs: str):
    donnees = {
        "series_id": str(series_id),
        "volume_url": "",
        "texte": TEXTE,
        "note": "8",
        "label": "",
    } | champs
    return client.post(
        "/libraire/critiques/nouvelle", data=donnees, follow_redirects=False
    )


def publiee(client: Navigateur, dsn: str, **champs: str) -> int:
    r = ecrire(client, **champs)
    assert r.status_code == 303, r.text[:400]
    return derniere(dsn)


def derniere(dsn: str) -> int:
    with psycopg.connect(dsn) as cx:
        (ident,) = cx.execute("SELECT max(id) FROM application.critiques").fetchone()
    return ident


def lignes(
    dsn: str, requete: str, params: tuple[Any, ...] = ()
) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as cx:
        return cx.execute(requete, params).fetchall()


def etat_des_critiques(dsn: str) -> tuple[list[tuple[Any, ...]], int]:
    critiques = lignes(
        dsn,
        "SELECT id, compte_id, series_id, volume_url, texte, note, label, statut,"
        " modifie_le FROM application.critiques ORDER BY id",
    )
    (journal,) = lignes(dsn, "SELECT count(*) FROM application.journal_actions")[0]
    return critiques, journal


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def menu(reponse) -> list[str]:
    # E4, lot 7 : le menu = les deux navigations de l'en-tête (les pages, le compte).
    navs = soupe(reponse).select("header nav")
    assert len(navs) == 2
    return [e.get_text(strip=True) for nav in navs for e in nav.select("a, button")]


def reseau(page: BeautifulSoup):
    zone = page.select_one("section.zone-critiques-reseau")
    assert zone is not None
    return zone


def articles(page: BeautifulSoup):
    return reseau(page).select("ul > li > article")


def un_seul_focus(page: BeautifulSoup) -> str:
    focus = page.select("[autofocus]")
    assert len(focus) == 1, [str(e)[:60] for e in focus]
    return focus[0]["id"]


# --------------------------------------------------------------------------- #
#  L2 — rédiger une critique signée
# --------------------------------------------------------------------------- #
def test_l2_le_libraire_ecrit_depuis_la_fiche(client, base):
    entrer(client, base, "libraire_un")
    fiche = soupe(client.get("/series/101"))
    lien = reseau(fiche).select_one("a")

    assert lien.get_text() == "Écrire une critique"
    assert lien["href"] == "/libraire/critiques/nouvelle?series_id=101"
    page = soupe(client.get(lien["href"]))
    assert page.select_one("h1").get_text() == "Écrire une critique"
    assert "Série factice A" in page.select_one("#critique-serie").get_text()
    assert page.select_one("section.zone-formulaire-critique form") is not None
    tomes = [(o["value"], o.get_text()) for o in page.select("#volume_url option")]
    assert tomes == [
        ("", "La série entière"),
        (URL_1, "Tome 1 — Premier tome factice (2001)"),
        (URL_2, "Tome 1 — Premier tome factice (2010)"),
        ("https://catalogue.invalid/serie/101/volume/3", "Hors-série factice"),
    ]
    notes = page.select("#note option")
    assert [o["value"] for o in notes] == ["", *map(str, range(11))]
    assert not [o for o in notes if o.has_attr("selected") and o["value"]]
    assert page.select_one("#note").has_attr("required")
    labels = [o.get_text() for o in page.select("#label option")]
    assert labels == [
        "Aucun",
        "Coup de cœur",
        "Découverte du mois",
        "À offrir",
        "Pour débuter",
        "Valeur sûre",
    ]
    texte = page.select_one("textarea#texte")
    assert "texte-aide" in texte["aria-describedby"]
    assert (
        page.select_one("#texte-aide").get_text()
        == "Entre 50 et 2\u202f000 caractères."
    )
    assert page.select_one("script:not([src='/statique/attente.js'])") is None


def test_l2_la_critique_parait_a_l_instant_signee_et_horodatee(client, base):
    entrer(client, base, "libraire_un")
    r = ecrire(client, label="coup_de_coeur")

    assert r.status_code == 303
    assert r.headers["location"] == "/series/101?etat=critique_publiee"
    fiche = soupe(client.get(r.headers["location"]))
    (article,) = articles(fiche)
    assert article.select_one("h3").get_text() == "Nom libraire_un"
    entete = article.select_one("p small").get_text()
    assert DATE.search(entete) and "8/10" in entete
    assert article.select_one("span.badge-label").get_text() == "Coup de cœur"
    assert TEXTE in article.get_text()
    note = [p.get_text() for p in fiche.select("section.zone-note p")]
    assert note == [
        "Vos libraires : 8,0/10 (1 critique)",
        "Note des lecteurs : 8,7/10 (6 936 votes)",
    ]


def test_l2_un_tome_facultatif_son_libelle_est_copie(client, base):
    entrer(client, base, "libraire_un")
    publiee(client, base)
    publiee(client, base, volume_url=URL_1, note="6")

    assert lignes(
        base, "SELECT volume_url, volume_libelle FROM application.critiques ORDER BY id"
    ) == [(None, None), (URL_1, "Tome 1 — Premier tome factice (2001)")]
    fiche = soupe(client.get("/series/101"))
    recent, ancien = articles(fiche)
    assert "Tome 1" in recent.select_one("p small span").get_text()
    assert "(Premier tome factice (2001))" in recent.select_one("p small").get_text()
    assert ancien.select_one("p small span:not(.badge-label)") is None
    assert fiche.select("section.zone-note p")[0].get_text() == (
        "Vos libraires : 7,0/10 (2 critiques)"
    )


def test_l2_une_seule_critique_publiee_par_serie_et_tome_refus_nomme(client, base):
    entrer(client, base, "libraire_un")
    premiere = publiee(client, base)
    page = soupe(client.get("/libraire/critiques/nouvelle?series_id=101"))
    existante = page.select_one(
        f'main a[href="/libraire/critiques/{premiere}/modifier"]'
    )
    assert existante is not None, "le formulaire dit qu'il en existe une"
    avant = etat_des_critiques(base)

    r = ecrire(client, note="3")

    assert r.status_code == 422
    page = soupe(r)
    erreur = page.select_one("#volume_url-erreur")
    assert "Vous avez déjà une critique publiée" in erreur.get_text()
    assert erreur.select_one("a")["href"] == f"/libraire/critiques/{premiere}/modifier"
    assert page.select_one("#volume_url")["aria-invalid"] == "true"
    assert un_seul_focus(page) == "volume_url"
    assert etat_des_critiques(base) == avant


SAISIES = {
    "texte_court": ({"texte": "[x] court"}, "texte"),
    "texte_long": ({"texte": "x" * 2001}, "texte"),
    "texte_controle": ({"texte": TEXTE + "\x07"}, "texte"),
    "note_absente": ({"note": ""}, "note"),
    "note_11": ({"note": "11"}, "note"),
    "note_negative": ({"note": "-1"}, "note"),
    "label_inconnu": ({"label": "inconnu"}, "label"),
    "tome_d_une_autre_serie": ({"volume_url": URL_AUTRE_SERIE}, "volume_url"),
}


@pytest.mark.parametrize("cas", SAISIES)
def test_l2_une_saisie_refusee_est_dite_au_champ_sans_perdre_le_texte(
    client, base, cas
):
    """Mutation E4R-06 (le tome d'une autre série accepté par la page)."""
    champs, fautif = SAISIES[cas]
    entrer(client, base, "libraire_un")
    avant = etat_des_critiques(base)

    r = ecrire(client, **champs)

    assert r.status_code == 422, cas
    page = soupe(r)
    assert page.select_one(f"#{fautif}")["aria-invalid"] == "true", cas
    assert page.select_one(f"#{fautif}-erreur").get_text().strip(), cas
    assert un_seul_focus(page) == fautif, cas
    saisi = champs.get("texte", TEXTE).replace("\x07", "")
    assert saisi[:40] in page.select_one("textarea#texte").get_text(), "texte perdu"
    assert etat_des_critiques(base) == avant, cas


def test_l2_les_valeurs_saisies_sont_rendues_au_formulaire(client, base):
    entrer(client, base, "libraire_un")
    r = ecrire(client, texte="court", volume_url=URL_2, note="7", label="a_offrir")

    page = soupe(r)
    assert page.select_one("#volume_url option[selected]")["value"] == URL_2
    assert page.select_one("#note option[selected]")["value"] == "7"
    assert page.select_one("#label option[selected]")["value"] == "a_offrir"
    assert page.select_one("textarea#texte").get_text() == "court"


@pytest.mark.parametrize("valeur", ["", "abc", "1" * 19, "１０１"])
def test_l2_une_serie_absente_ou_hors_forme_est_introuvable(client, base, valeur):
    entrer(client, base, "libraire_un")
    assert (
        client.get(f"/libraire/critiques/nouvelle?series_id={valeur}").status_code
        == 404
    )
    assert ecrire(client, series_id=valeur).status_code == 404


def test_l2_une_serie_inconnue_du_catalogue_est_nommee(client, base, doublure):
    entrer(client, base, "libraire_un")
    doublure.choisir(catalogue="serie_inconnue")

    for r in (client.get("/libraire/critiques/nouvelle?series_id=101"), ecrire(client)):
        assert r.status_code == 404
        assert "Cette série n'existe pas dans le catalogue." in soupe(r).get_text()
    assert etat_des_critiques(base)[0] == []


@pytest.mark.parametrize(
    "scenario", ["indisponible", "cle_refusee", "corps_hors_contrat"]
)
def test_l2_un_catalogue_indisponible_est_un_503_nomme(
    client, base, doublure, scenario
):
    entrer(client, base, "libraire_un")
    doublure.choisir(catalogue=scenario)

    for r in (client.get("/libraire/critiques/nouvelle?series_id=101"), ecrire(client)):
        assert r.status_code == 503, scenario
        assert "Le catalogue ne répond pas" in r.text
    assert etat_des_critiques(base)[0] == []


def test_l2_sans_catalogue_configure_le_formulaire_est_un_503(
    doublure, reglages, base_app, base
):
    sans = replace(configure(reglages, base_app), catalogue=reglages.catalogue)
    with Navigateur(demarrer(doublure, sans), client=CLIENT) as client:
        ident = entrer(client, base, "libraire_un")
        lignes(
            base,
            "INSERT INTO application.critiques (compte_id, series_id, texte, note)"
            " VALUES (%s, 101, %s, 5) RETURNING id",
            (ident, TEXTE),
        )
        assert (
            client.get("/libraire/critiques/nouvelle?series_id=101").status_code == 503
        )
        mes = client.get("/libraire/critiques")

    assert mes.status_code == 200
    assert "titre indisponible" in soupe(mes).select_one("tbody tr").get_text()
    assert "Le catalogue ne répond pas" in soupe(mes).get_text()


def test_l2_le_journal_dit_l_action_sans_le_texte(client, base, caplog):
    entrer(client, base, "libraire_un")
    with caplog.at_level(logging.INFO, logger="application"):
        ident = publiee(client, base, label="valeur_sure")
        client.post(
            f"/libraire/critiques/{ident}/modifier",
            data={"texte": AUTRE, "note": "9", "label": ""},
        )
        client.post(f"/libraire/critiques/{ident}/retirer", data={})
    texte = journal_complet(caplog)

    evenements = [r.getMessage() for r in caplog.records]
    for nom in ("critique_publiee", "critique_modifiee", "critique_retiree"):
        assert nom in evenements, nom
    for fragment in (TEXTE[:30], AUTRE[:30]):
        assert fragment not in texte
    details = lignes(
        base,
        "SELECT action, details::text FROM application.journal_actions"
        " WHERE objet_type = 'critique' ORDER BY id",
    )
    assert [a for a, _ in details] == [
        "critique_publiee",
        "critique_modifiee",
        "critique_retiree",
    ]
    assert all(TEXTE[:30] not in d and AUTRE[:30] not in d for _, d in details)


def test_l2_la_metrique_compte_les_actions(client, base):
    entrer(client, base, "libraire_un")
    ident = publiee(client, base)
    client.post(f"/libraire/critiques/{ident}/retirer", data={})
    metriques = client.get("/metrics").text

    assert 'application_critiques_total{action="publiee"} 1.0' in metriques
    assert 'application_critiques_total{action="retiree"} 1.0' in metriques


# --------------------------------------------------------------------------- #
#  L3 — modifier ou retirer mes critiques
# --------------------------------------------------------------------------- #
def test_l3_modifier_le_formulaire_prerempli_serie_et_tome_figes(client, base):
    entrer(client, base, "libraire_un")
    ident = publiee(client, base, volume_url=URL_1, label="a_offrir", note="6")
    page = soupe(client.get(f"/libraire/critiques/{ident}/modifier"))

    assert page.select_one("h1").get_text() == "Modifier ma critique"
    assert page.select_one("#volume_url") is None, "le tome ne se change pas"
    assert (
        "Tome 1 — Premier tome factice (2001)"
        in page.select_one("main form").get_text()
    )
    assert page.select_one("textarea#texte").get_text() == TEXTE
    assert page.select_one("#note option[selected]")["value"] == "6"
    assert page.select_one("#label option[selected]")["value"] == "a_offrir"
    assert page.select_one("main form")["action"] == (
        f"/libraire/critiques/{ident}/modifier"
    )


def test_l3_modifier_pose_la_date_de_modification_et_la_moyenne_suit(client, base):
    entrer(client, base, "libraire_un")
    ident = publiee(client, base)
    r = client.post(
        f"/libraire/critiques/{ident}/modifier",
        data={"texte": AUTRE, "note": "9", "label": "valeur_sure", "series_id": "102"},
        follow_redirects=False,
    )

    assert r.headers["location"] == "/libraire/critiques?etat=modifiee"
    assert lignes(
        base,
        "SELECT series_id, texte, note, label, modifie_le > publie_le"
        " FROM application.critiques",
    ) == [(101, AUTRE, 9, "valeur_sure", True)]
    fiche = soupe(client.get("/series/101"))
    (article,) = articles(fiche)
    assert re.search(r"modifiée le (1er|\d{1,2}) ", article.get_text())
    assert (
        fiche.select("section.zone-note p")[0]
        .get_text()
        .startswith("Vos libraires : 9,0/10")
    )


def test_l3_une_modification_refusee_garde_la_critique(client, base):
    entrer(client, base, "libraire_un")
    ident = publiee(client, base)
    avant = etat_des_critiques(base)
    r = client.post(
        f"/libraire/critiques/{ident}/modifier",
        data={"texte": "court", "note": "9", "label": ""},
    )

    assert r.status_code == 422
    assert un_seul_focus(soupe(r)) == "texte"
    assert etat_des_critiques(base) == avant


def test_l3_retirer_confirmation_puis_post(client, base):
    entrer(client, base, "libraire_un")
    ident = publiee(client, base)
    page = soupe(client.get(f"/libraire/critiques/{ident}/retirer"))

    assert page.select_one("h1").get_text() == "Retirer ma critique"
    assert "Le retrait est définitif" in page.get_text()
    formulaire = page.select_one("main form")
    assert formulaire["method"] == "post"
    assert formulaire["action"] == f"/libraire/critiques/{ident}/retirer"
    assert formulaire.select_one("input[name=jeton]")["value"] == jeton_du_cookie(
        client
    )
    assert page.select_one('main a[href="/libraire/critiques"]').get_text() == "Annuler"
    assert lignes(base, "SELECT statut FROM application.critiques") == [("publiee",)]

    r = client.post(
        f"/libraire/critiques/{ident}/retirer", data={}, follow_redirects=False
    )

    assert r.headers["location"] == "/libraire/critiques?etat=retiree"
    assert lignes(base, "SELECT statut FROM application.critiques") == [("retiree",)]
    fiche = soupe(client.get("/series/101"))
    assert articles(fiche) == []
    assert "Aucune critique de libraire pour l'instant." in reseau(fiche).get_text()
    liste = soupe(client.get("/libraire/critiques"))
    assert "retirée" in liste.select_one("tbody tr").get_text()
    r = client.post(
        f"/libraire/critiques/{ident}/retirer", data={}, follow_redirects=False
    )
    assert r.headers["location"] == "/libraire/critiques?etat=deja_retiree"
    assert (
        client.get(
            f"/libraire/critiques/{ident}/retirer", follow_redirects=False
        ).headers["location"]
        == "/libraire/critiques?etat=deja_retiree"
    )


def test_l3_une_critique_retiree_ne_se_modifie_plus_page_nommee(client, base):
    entrer(client, base, "libraire_un")
    ident = publiee(client, base)
    client.post(f"/libraire/critiques/{ident}/retirer", data={})

    for r in (
        client.get(f"/libraire/critiques/{ident}/modifier"),
        client.post(
            f"/libraire/critiques/{ident}/modifier",
            data={"texte": AUTRE, "note": "9", "label": ""},
        ),
    ):
        assert r.status_code == 404
        assert "Cette critique est retirée" in r.text


def test_l3_apres_retrait_une_nouvelle_critique_est_acceptee(client, base):
    entrer(client, base, "libraire_un")
    ident = publiee(client, base)
    client.post(f"/libraire/critiques/{ident}/retirer", data={})

    assert ecrire(client, note="5").status_code == 303
    assert lignes(base, "SELECT statut FROM application.critiques ORDER BY id") == [
        ("retiree",),
        ("publiee",),
    ]


def test_l3_un_autre_libraire_recoit_403_et_la_base_est_inchangee(appli, base):
    app, client = appli
    entrer(client, base, "libraire_un")
    ident = publiee(client, base)
    autre = autre_client(app)
    entrer(autre, base, "libraire_deux")
    avant = etat_des_critiques(base)

    for r in (
        autre.get(f"/libraire/critiques/{ident}/modifier"),
        autre.post(
            f"/libraire/critiques/{ident}/modifier",
            data={"texte": AUTRE, "note": "1", "label": ""},
        ),
        autre.get(f"/libraire/critiques/{ident}/retirer"),
        autre.post(f"/libraire/critiques/{ident}/retirer", data={}),
    ):
        assert r.status_code == 403
        assert "Accès refusé" in r.text
    assert etat_des_critiques(base) == avant


@pytest.mark.parametrize("role", ["client", "admin"])
def test_l3_un_client_et_un_admin_n_ecrivent_pas(client, base, role):
    entrer(client, base, f"{role}_un", role)
    avant = etat_des_critiques(base)

    assert client.get("/libraire/critiques/nouvelle?series_id=101").status_code == 403
    assert ecrire(client).status_code == 403
    assert client.get("/libraire/critiques").status_code == 403
    assert etat_des_critiques(base) == avant


@pytest.mark.parametrize(
    "chemin",
    [
        "/libraire/critiques",
        "/libraire/critiques/nouvelle?series_id=101",
        "/libraire/critiques/1/modifier",
        "/libraire/critiques/1/retirer",
        "/admin/critiques",
        "/admin/critiques/1/retirer",
    ],
)
def test_l3_un_visiteur_est_renvoye_vers_la_connexion(client, chemin):
    r = client.get(chemin, follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"].startswith("/connexion?retour=/")


def test_l3_un_formulaire_sans_session_est_un_401(client):
    for chemin in (
        "/libraire/critiques/nouvelle",
        "/libraire/critiques/1/modifier",
        "/libraire/critiques/1/retirer",
        "/admin/critiques/1/retirer",
    ):
        assert client.post(chemin, data={}).status_code == 401, chemin


def test_l3_sans_jeton_403_et_la_base_est_inchangee(client, base):
    entrer(client, base, "libraire_un")
    ident = publiee(client, base)
    avant = etat_des_critiques(base)

    for chemin, donnees in (
        (
            "/libraire/critiques/nouvelle",
            {"series_id": "101", "texte": TEXTE, "note": "8"},
        ),
        (f"/libraire/critiques/{ident}/modifier", {"texte": AUTRE, "note": "1"}),
        (f"/libraire/critiques/{ident}/retirer", {}),
    ):
        for jeton in ("", "faux"):
            r = client.post(chemin, data={**donnees, "jeton": jeton})
            assert r.status_code == 403 and "Ce formulaire a expiré" in r.text, chemin
    assert etat_des_critiques(base) == avant


def test_l3_l_identifiant_est_lu_apres_le_jeton_et_le_role(appli, base):
    app, client = appli
    entrer(client, base, "client_un", "client")
    assert client.post("/libraire/critiques/abc/modifier", data={}).status_code == 403
    libraire = autre_client(app)
    entrer(libraire, base, "libraire_un")
    for chemin in (
        "/libraire/critiques/abc/modifier",
        "/libraire/critiques/1x/retirer",
    ):
        assert libraire.post(chemin, data={}).status_code == 404
        assert libraire.get(chemin).status_code == 404
    assert libraire.get("/libraire/critiques/999999/modifier").status_code == 404
    assert (
        libraire.post("/libraire/critiques/999999/retirer", data={}).status_code == 404
    )


# --------------------------------------------------------------------------- #
#  L4 — la liste de mes critiques ; L5 — le délai d'intégration
# --------------------------------------------------------------------------- #
def test_l4_mes_critiques_avec_statut_titre_tome_et_liens(appli, base):
    app, client = appli
    entrer(client, base, "libraire_un")
    serie = publiee(client, base, label="coup_de_coeur")
    tome = publiee(client, base, volume_url=URL_1, note="6")
    autre = publiee(client, base, series_id=102, note="4")
    client.post(f"/libraire/critiques/{autre}/retirer", data={})
    tiers = autre_client(app)
    entrer(tiers, base, "libraire_deux")
    publiee(tiers, base, series_id=103)

    page = soupe(client.get("/libraire/critiques"))
    rangs = page.select("tbody tr")
    cellules = [
        [td.get_text(" ", strip=True) for td in tr.select("td")][:5] for tr in rangs
    ]

    assert cellules == [
        ["Série factice B", "série entière", "4/10", "—", "retirée"],
        [
            "Série factice A",
            "Tome 1 — Premier tome factice (2001)",
            "6/10",
            "—",
            "publiée",
        ],
        ["Série factice A", "série entière", "8/10", "Coup de cœur", "publiée"],
    ]
    assert rangs[0].select_one('a[href="/series/102"]') is not None
    assert rangs[0].select("td")[-1].select("a") == []
    actions = [a["href"] for a in rangs[2].select("td")[-1].select("a")]
    assert actions == [
        f"/libraire/critiques/{serie}/modifier",
        f"/libraire/critiques/{serie}/retirer",
    ]
    assert tome != serie
    assert DATE.search(rangs[1].get_text())


def test_l4_le_filtre_par_statut(client, base):
    entrer(client, base, "libraire_un")
    publiee(client, base)
    retiree = publiee(client, base, series_id=102)
    client.post(f"/libraire/critiques/{retiree}/retirer", data={})

    retirees = soupe(client.get("/libraire/critiques?statut=retiree")).select(
        "tbody tr"
    )
    publiees = soupe(client.get("/libraire/critiques?statut=publiee")).select(
        "tbody tr"
    )
    toutes = soupe(client.get("/libraire/critiques?statut=autre")).select("tbody tr")
    assert len(retirees) == 1 and "retirée" in retirees[0].get_text()
    assert len(publiees) == 1 and "publiée" in publiees[0].get_text()
    assert len(toutes) == 2
    page = soupe(client.get("/libraire/critiques?statut=retiree"))
    assert page.select_one("#filtre-statut option[selected]")["value"] == "retiree"


def test_l4_la_liste_se_pagine(doublure, reglages, base_app, base):
    with Navigateur(
        demarrer(doublure, configure(reglages, base_app, par_page=2)), client=CLIENT
    ) as client:
        entrer(client, base, "libraire_un")
        for series_id in (101, 102, 103):
            publiee(client, base, series_id=str(series_id))
        une = soupe(client.get("/libraire/critiques"))
        deux = soupe(client.get("/libraire/critiques?page=2"))

    assert len(une.select("tbody tr")) == 2 and len(deux.select("tbody tr")) == 1
    assert "Page 1 sur 2" in une.select_one("main nav").get_text()
    assert une.select_one('main nav a[href="/libraire/critiques?page=2"]') is not None


def test_l4_l_etat_vide_est_nomme_et_mene_au_catalogue(client, base):
    entrer(client, base, "libraire_un")
    page = soupe(client.get("/libraire/critiques"))

    assert "Vous n'avez pas encore écrit de critique." in page.get_text()
    lien = page.select_one('main a[href="/catalogue"]')
    assert "première critique" in lien.get_text()
    assert page.select_one("table") is None


def test_l4_un_catalogue_indisponible_laisse_la_liste_avec_titre_indisponible(
    client, base, doublure
):
    entrer(client, base, "libraire_un")
    publiee(client, base)
    doublure.choisir(catalogue="indisponible")
    r = client.get("/libraire/critiques")

    assert r.status_code == 200
    page = soupe(r)
    assert "titre indisponible" in page.select_one("tbody tr").get_text()
    assert "Le catalogue ne répond pas" in page.get_text()


@pytest.mark.parametrize(
    ("etat", "texte"),
    [
        ("publiee", "Critique publiée."),
        ("modifiee", "Critique modifiée"),
        ("retiree", "Critique retirée"),
        ("deja_retiree", "déjà retirée"),
    ],
)
def test_l4_les_avis_prg_prennent_le_focus(client, base, etat, texte):
    entrer(client, base, "libraire_un")
    page = soupe(client.get(f"/libraire/critiques?etat={etat}"))

    assert texte in page.select_one("[role=status]").get_text()
    assert un_seul_focus(page) == "avis"


def test_l5_la_phrase_du_delai_apres_publication_et_sur_mes_critiques(client, base):
    entrer(client, base, "libraire_un")
    phrase = (
        "Votre critique est visible sur la fiche ; elle sera intégrée aux"
        " recommandations du conseiller à la prochaine mise à jour du corpus."
    )
    fiche = soupe(ecrire(client) and client.get("/series/101?etat=critique_publiee"))

    assert fiche.select_one("[role=status]").get_text() == phrase
    assert un_seul_focus(fiche) == "avis"
    # L'avis est au-dessus de la section du réseau.
    assert str(fiche).index('id="avis"') < str(fiche).index("zone-critiques-reseau")
    liste = soupe(client.get("/libraire/critiques"))
    assert liste.select("main p")[0].get_text() == phrase
    assert soupe(client.get("/series/101")).select_one("#avis") is None


def test_l4_les_pages_des_critiques_ne_vont_jamais_en_cache(client, base):
    entrer(client, base, "libraire_un")
    ident = publiee(client, base)
    for chemin in (
        "/libraire/critiques",
        "/libraire/critiques/nouvelle?series_id=101",
        f"/libraire/critiques/{ident}/modifier",
        f"/libraire/critiques/{ident}/retirer",
    ):
        assert client.get(chemin).headers["cache-control"] == "no-store", chemin


# --------------------------------------------------------------------------- #
#  V3 (suite) — la fiche : les critiques du réseau face à la note des lecteurs
# --------------------------------------------------------------------------- #
def test_v3_les_critiques_publiees_en_entier_et_en_paragraphes(client, base):
    entrer(client, base, "libraire_un")
    texte = (
        f"Premier paragraphe factice. {TEXTE}\r\nSa seconde ligne.\r\n\r\n"
        "Second paragraphe factice."
    )
    publiee(client, base, texte=texte, label="pour_debuter", note="10")
    (article,) = articles(soupe(client.get("/series/101")))

    paragraphes = article.select("p")[1:]
    assert (
        paragraphes[0].get_text()
        == f"Premier paragraphe factice. {TEXTE}Sa seconde ligne."
    )
    assert len(paragraphes[0].select("br")) == 1
    assert paragraphes[1].get_text() == "Second paragraphe factice."
    assert "10/10" in article.select_one("p small").get_text()
    assert "Pour débuter" in article.get_text()
    assert article.select_one("h3").get_text() == "Nom libraire_un"  # le nom affiché


def test_v3_le_texte_est_echappe_jamais_interprete(client, base):
    """Mutation E4R-07 (le texte marqué « sûr » dans le gabarit)."""
    entrer(client, base, "libraire_un")
    texte = (
        f"{TEXTE} <b>balisage</b> et <script>alert(1)</script>"
        " et <img src=x onerror=alert(1)>"
    )
    publiee(client, base, texte=texte)
    r = client.get("/series/101")
    (article,) = articles(soupe(r))

    assert article.select("b, script, img") == []
    assert "<b>balisage</b>" in article.get_text()
    assert "<script>alert(1)</script>" in article.get_text()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text


def test_v3_une_retiree_est_absente_et_la_moyenne_l_exclut(appli, base):
    app, client = appli
    entrer(client, base, "libraire_un")
    publiee(client, base, note="9")
    tome = publiee(client, base, volume_url=URL_1, note="6")
    deux = autre_client(app)
    entrer(deux, base, "libraire_deux")
    publiee(deux, base, note="4")
    retiree = publiee(deux, base, volume_url=URL_1, note="0")
    deux.post(f"/libraire/critiques/{retiree}/retirer", data={})

    fiche = soupe(client.get("/series/101"))
    assert len(articles(fiche)) == 3
    assert fiche.select("section.zone-note p")[0].get_text() == (
        "Vos libraires : 6,3/10 (3 critiques)"
    )
    assert tome


def test_v3_sans_critique_l_etat_vide_et_pas_encore_d_avis(client, base):
    entrer(client, base, "libraire_un")
    fiche = soupe(client.get("/series/101"))

    assert "Aucune critique de libraire pour l'instant." in reseau(fiche).get_text()
    assert [p.get_text() for p in fiche.select("section.zone-note p")] == [
        "Vos libraires : pas encore d'avis",
        "Note des lecteurs : 8,7/10 (6 936 votes)",
    ]


@pytest.mark.parametrize(
    ("qui", "ecrire_", "modifier_", "moderer"),
    [
        ("visiteur", False, False, False),
        ("client", False, False, False),
        ("auteur", True, True, False),
        ("tiers", True, False, False),
        ("admin", False, False, True),
    ],
)
def test_v3_les_liens_selon_le_role(appli, base, qui, ecrire_, modifier_, moderer):
    """Mutation E4R-05 (le lien d'écriture servi à un client)."""
    app, auteur = appli
    entrer(auteur, base, "libraire_auteur")
    ident = publiee(auteur, base)
    regard = auteur if qui == "auteur" else autre_client(app)
    if qui in ("client", "admin"):
        entrer(regard, base, f"{qui}_un", qui)
    elif qui == "tiers":
        entrer(regard, base, "libraire_tiers")
    zone = reseau(soupe(regard.get("/series/101")))
    liens = {a["href"] for a in zone.select("a")}

    assert ("/libraire/critiques/nouvelle?series_id=101" in liens) is ecrire_
    assert (f"/libraire/critiques/{ident}/modifier" in liens) is modifier_
    assert (f"/admin/critiques/{ident}/retirer" in liens) is moderer
    assert not any("/retirer" in lien and "/libraire/" in lien for lien in liens)
    assert len(articles(soupe(regard.get("/series/101")))) == 1


def test_v3_la_base_indisponible_laisse_la_fiche_avec_l_encart(doublure, reglages):
    sans_base = replace(
        reglages,
        catalogue=replace(reglages.catalogue, cle=CLE_CATALOGUE_ATTENDUE),
    )
    with Navigateur(demarrer(doublure, sans_base), client=CLIENT) as client:
        r = client.get("/series/101")

    assert r.status_code == 200
    page = soupe(r)
    assert (
        "Les critiques du réseau sont momentanément indisponibles."
        in reseau(page).get_text()
    )
    assert page.select_one("section.zone-titre dl") is not None
    assert [p.get_text() for p in page.select("section.zone-note p")] == [
        "Note des lecteurs : 8,7/10 (6 936 votes)"
    ]


def test_v3_les_critiques_collectees_ne_sont_toujours_pas_demandees(
    client, base, doublure
):
    entrer(client, base, "libraire_un")
    publiee(client, base)
    client.get("/series/101")
    client.get("/libraire/critiques")

    assert doublure.appels_catalogue.get("/series/{series_id}/reviews", 0) == 0


# --------------------------------------------------------------------------- #
#  A5 — la modération
# --------------------------------------------------------------------------- #
def test_a5_l_admin_voit_les_publiees_filtre_et_debut_echappe(appli, base):
    app, admin = appli
    un = autre_client(app)
    entrer(un, base, "libraire_un")
    long = f"<i>début</i> {TEXTE} {TEXTE}"
    publiee(un, base, texte=long, label="coup_de_coeur")
    retiree = publiee(un, base, series_id=102)
    un.post(f"/libraire/critiques/{retiree}/retirer", data={})
    deux = autre_client(app, 50002)
    deux_id = entrer(deux, base, "libraire_deux")
    publiee(deux, base, series_id=103)
    creer_compte(base, "libraire_inactif", actif=False)
    entrer(admin, base, "admin_un", "admin")

    r = admin.get("/admin/critiques")
    page = soupe(r)
    rangs = page.select("tbody tr")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert [tr.select("td")[0].get_text() for tr in rangs] == [
        "Nom libraire_deux",
        "Nom libraire_un",
    ]
    debut = rangs[1].select("td")[6].get_text()
    assert debut.startswith("<i>début</i>") and debut.endswith("…")
    assert len(debut) == 121 and rangs[1].select("i") == []
    assert rangs[1].select_one('a[href="/series/101"]').get_text() == "Série factice A"
    options = [o.get_text() for o in page.select("#filtre-libraire option")]
    assert options == [
        "Tous les libraires",
        "Nom libraire_deux",
        "Nom libraire_inactif (désactivé)",
        "Nom libraire_un",
    ]
    filtre = soupe(admin.get(f"/admin/critiques?libraire={deux_id}")).select("tbody tr")
    assert len(filtre) == 1 and "Nom libraire_deux" in filtre[0].get_text()
    vide = soupe(admin.get("/admin/critiques?libraire=999999"))
    assert "Aucune critique publiée." in vide.get_text()


def test_a5_la_moderation_se_pagine(doublure, reglages, base_app, base):
    app = demarrer(doublure, configure(reglages, base_app, par_page=1))
    with Navigateur(app, client=CLIENT) as libraire:
        entrer(libraire, base, "libraire_un")
        publiee(libraire, base)
        publiee(libraire, base, series_id="102")
        admin = autre_client(app)
        entrer(admin, base, "admin_un", "admin")
        une = soupe(admin.get("/admin/critiques"))
        deux = soupe(admin.get("/admin/critiques?page=2"))

    assert "Série factice B" in une.select_one("tbody").get_text()
    assert "Série factice A" in deux.select_one("tbody").get_text()
    assert "Page 1 sur 2" in une.select_one("main nav:not(.onglets)").get_text()


def test_a5_confirmation_en_entier_puis_retrait_au_nom_de_l_admin(appli, base):
    app, admin = appli
    un = autre_client(app)
    auteur = entrer(un, base, "libraire_un")
    ident = publiee(un, base, volume_url=URL_1, note="6")
    admin_id = entrer(admin, base, "admin_un", "admin")
    page = soupe(admin.get(f"/admin/critiques/{ident}/retirer"))

    assert page.select_one("h1").get_text() == "Retirer une critique (modération)"
    assert TEXTE in page.select_one("main article").get_text()
    assert page.select_one("main article h2").get_text() == "Nom libraire_un"
    assert "tracé au journal à votre nom" in page.get_text()
    assert "Série factice A" in page.select_one("main dl").get_text()

    r = admin.post(f"/admin/critiques/{ident}/retirer", data={}, follow_redirects=False)

    assert r.headers["location"] == "/admin/critiques?etat=retiree"
    assert lignes(
        base,
        "SELECT action, par_compte_id, details->>'par' FROM application.journal_actions"
        " WHERE action = 'critique_retiree'",
    ) == [("critique_retiree", admin_id, "admin")]
    assert articles(soupe(admin.get("/series/101"))) == []
    liste = soupe(un.get("/libraire/critiques"))
    assert "retirée" in liste.select_one("tbody tr").get_text()
    avis = soupe(admin.get("/admin/critiques?etat=retiree"))
    assert un_seul_focus(avis) == "avis"
    assert auteur != admin_id


def test_a5_une_critique_deja_retiree_un_avis_et_aucune_ligne(appli, base):
    app, admin = appli
    un = autre_client(app)
    entrer(un, base, "libraire_un")
    ident = publiee(un, base)
    un.post(f"/libraire/critiques/{ident}/retirer", data={})
    entrer(admin, base, "admin_un", "admin")
    avant = etat_des_critiques(base)

    r = admin.post(f"/admin/critiques/{ident}/retirer", data={}, follow_redirects=False)
    assert r.headers["location"] == "/admin/critiques?etat=deja_retiree"
    assert (
        admin.get(f"/admin/critiques/{ident}/retirer", follow_redirects=False).headers[
            "location"
        ]
        == "/admin/critiques?etat=deja_retiree"
    )
    assert etat_des_critiques(base) == avant
    assert admin.post("/admin/critiques/999999/retirer", data={}).status_code == 404
    assert admin.get("/admin/critiques/999999/retirer").status_code == 404


@pytest.mark.parametrize("role", ["libraire", "client"])
def test_a5_seul_l_admin_modere(appli, base, role):
    app, client = appli
    un = autre_client(app)
    entrer(un, base, "libraire_un")
    ident = publiee(un, base)
    entrer(client, base, f"{role}_deux", role)
    avant = etat_des_critiques(base)

    assert client.get("/admin/critiques").status_code == 403
    assert client.get(f"/admin/critiques/{ident}/retirer").status_code == 403
    assert client.post(f"/admin/critiques/{ident}/retirer", data={}).status_code == 403
    assert etat_des_critiques(base) == avant


def test_a5_la_moderation_sans_jeton_est_refusee(appli, base):
    app, admin = appli
    un = autre_client(app)
    entrer(un, base, "libraire_un")
    ident = publiee(un, base)
    entrer(admin, base, "admin_un", "admin")
    avant = etat_des_critiques(base)

    for jeton in ("", "faux"):
        r = admin.post(f"/admin/critiques/{ident}/retirer", data={"jeton": jeton})
        assert r.status_code == 403 and "Ce formulaire a expiré" in r.text
    assert etat_des_critiques(base) == avant


def test_a5_l_admin_ne_modifie_aucune_critique(appli, base):
    app, admin = appli
    un = autre_client(app)
    entrer(un, base, "libraire_un")
    ident = publiee(un, base)
    entrer(admin, base, "admin_un", "admin")
    avant = etat_des_critiques(base)

    assert admin.get(f"/libraire/critiques/{ident}/modifier").status_code == 403
    r = admin.post(
        f"/libraire/critiques/{ident}/modifier",
        data={"texte": AUTRE, "note": "1", "label": ""},
    )
    assert r.status_code == 403
    assert admin.post(f"/admin/critiques/{ident}/modifier", data={}).status_code in (
        404,
        405,
    )
    assert etat_des_critiques(base) == avant


@pytest.mark.parametrize(
    ("role", "attendus", "absents"),
    [
        ("libraire", {"Mes critiques"}, {"Administration"}),
        # E4, lot 5 : la modération est un onglet de « Administration ».
        ("admin", {"Administration"}, {"Mes critiques", "Critiques"}),
        ("client", set(), {"Mes critiques", "Administration"}),
    ],
)
def test_a5_le_menu_par_role(client, base, role, attendus, absents):
    entrer(client, base, f"{role}_un", role)
    entrees = set(menu(client.get("/")))

    assert attendus <= entrees
    assert not absents & entrees


# --------------------------------------------------------------------------- #
#  Le balisage des pages neuves
# --------------------------------------------------------------------------- #
def pages_neuves(appli, base, doublure) -> dict[str, BeautifulSoup]:
    app, client = appli
    entrer(client, base, "libraire_un")
    rendues = {"critiques_vide": client.get("/libraire/critiques")}
    ident = publiee(client, base, label="coup_de_coeur")
    publiee(client, base, volume_url=URL_1)
    rendues |= {
        "critiques_liste": client.get("/libraire/critiques?etat=modifiee"),
        "critique_nouvelle": client.get("/libraire/critiques/nouvelle?series_id=101"),
        "critique_nouvelle_refusee": ecrire(
            client, series_id=102, texte="court", note=""
        ),
        "critique_modifier": client.get(f"/libraire/critiques/{ident}/modifier"),
        "critique_retirer": client.get(f"/libraire/critiques/{ident}/retirer"),
        "serie_avec_critiques": client.get("/series/101?etat=critique_publiee"),
    }
    tiers = autre_client(app)
    entrer(tiers, base, "libraire_deux")
    rendues["critique_acces_refuse"] = tiers.get(
        f"/libraire/critiques/{ident}/modifier"
    )
    admin = autre_client(app, 50002)
    entrer(admin, base, "admin_un", "admin")
    rendues |= {
        "admin_critiques": admin.get("/admin/critiques"),
        "admin_critiques_vide": admin.get("/admin/critiques?libraire=999999"),
        "admin_critique_retirer": admin.get(f"/admin/critiques/{ident}/retirer"),
    }
    doublure.choisir(catalogue="indisponible")
    rendues["critiques_catalogue_indisponible"] = client.get("/libraire/critiques")
    doublure.choisir(catalogue="nominal")
    return {nom: soupe(r) for nom, r in rendues.items()}


def test_balisage_titres_dans_l_ordre_evitement_langue_et_mention(
    appli, base, doublure
):
    for nom, page in pages_neuves(appli, base, doublure).items():
        niveaux = [int(h.name[1]) for h in page.select("main h1, main h2, main h3")]
        assert niveaux and niveaux[0] == 1, nom
        assert all(b - a <= 1 for a, b in zip(niveaux, niveaux[1:], strict=False)), nom
        assert page.select_one('a[href="#contenu"]') is not None, nom
        assert page.select_one("html")["lang"] == "fr", nom
        assert "Vous parlez à une IA." in page.get_text(), nom
        assert page.select_one("script:not([src='/statique/attente.js'])") is None, nom
        assert len(page.select("[autofocus]")) <= 1, nom


def test_balisage_chaque_champ_a_son_etiquette_et_ses_erreurs_reliees(
    appli, base, doublure
):
    pages = pages_neuves(appli, base, doublure)
    for nom, page in pages.items():
        for champ in page.select(
            "main input:not([type=hidden]), main select, main textarea"
        ):
            assert page.select_one(f'label[for="{champ["id"]}"]') is not None, (
                nom,
                champ,
            )
        for formulaire in page.select("main form[method=post]"):
            assert formulaire.select_one("input[name=jeton]") is not None, nom
    fautifs = pages["critique_nouvelle_refusee"].select("main [aria-invalid=true]")
    assert {f["id"] for f in fautifs} == {"texte", "note"}
    for champ in fautifs:
        for ident in champ["aria-describedby"].split():
            assert (
                pages["critique_nouvelle_refusee"].select_one(f"#{ident}") is not None
            )


def test_balisage_les_zones_nommees_pour_l_habillage(appli, base, doublure):
    pages = pages_neuves(appli, base, doublure)

    assert pages["critique_nouvelle"].select_one("section.zone-formulaire-critique")
    assert pages["critique_modifier"].select_one("section.zone-formulaire-critique")
    fiche = pages["serie_avec_critiques"]
    assert len(fiche.select("section.zone-critiques-reseau ul > li > article")) == 2
    assert fiche.select("span.badge-label")[0].get_text() == "Coup de cœur"
    assert pages["critique_acces_refuse"].select_one("h1").get_text() == "Accès refusé"
    noms = [
        a.get("aria-label")
        for a in pages["critiques_liste"].select("tbody a[aria-label]")
    ]
    assert len(noms) == len(set(noms)), "deux liens au même nom"


# --------------------------------------------------------------------------- #
#  Les courses entre deux onglets, les corps illisibles
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("erreur", "statut", "texte"),
    [
        (CritiqueIntrouvable(), 404, "Cette critique est retirée"),
        (PasLAuteur(), 403, "Accès refusé"),
        (CritiqueInvalide("critique", "base"), 422, "pas pu être enregistrée"),
    ],
    ids=["retiree_entre_temps", "plus_l_auteur", "refus_de_la_base"],
)
def test_l3_une_course_a_la_modification_est_nommee(
    client, base, monkeypatch, erreur, statut, texte
):
    """La critique lue par la page, puis changée avant l'écriture (un autre onglet) :
    l'erreur du module est rendue nommée, jamais en 500."""
    entrer(client, base, "libraire_un")
    ident = publiee(client, base)

    async def course(*_: object, **__: object) -> None:
        raise erreur

    monkeypatch.setattr(critiques, "modifier", course)
    r = client.post(
        f"/libraire/critiques/{ident}/modifier",
        data={"texte": AUTRE, "note": "9", "label": ""},
    )

    assert r.status_code == statut
    assert texte in soupe(r).get_text()


@pytest.mark.parametrize(
    ("erreur", "champ"),
    [
        (CritiqueInvalide("volume_url", "tome"), "volume_url"),
        (CritiqueInvalide("critique", "base"), "texte"),
    ],
    ids=["tome", "base"],
)
def test_l2_un_refus_du_module_a_la_publication_est_dit_au_champ(
    client, base, monkeypatch, erreur, champ
):
    entrer(client, base, "libraire_un")

    async def refus(*_: object, **__: object) -> int:
        raise erreur

    monkeypatch.setattr(critiques, "publier", refus)
    r = ecrire(client)

    assert r.status_code == 422
    assert soupe(r).select_one(f"#{champ}")["aria-invalid"] == "true"


def test_l2_un_tome_sans_numero_se_montre_entier(client, base):
    entrer(client, base, "libraire_un")
    publiee(client, base, volume_url="https://catalogue.invalid/serie/101/volume/3")
    (article,) = articles(soupe(client.get("/series/101")))

    assert "Hors-série factice" in article.select_one("p small span").get_text()


def test_un_corps_de_formulaire_illisible_est_une_demande_illisible(client, base):
    entrer(client, base, "libraire_un")
    r = client.post(
        "/libraire/critiques/nouvelle",
        content=b"illisible",
        headers={"content-type": "multipart/form-data; boundary=x"},
    )

    assert r.status_code == 400
