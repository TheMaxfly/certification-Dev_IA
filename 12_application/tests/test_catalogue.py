"""Le catalogue dans l'application (E4, lot 2a, spec 02) : le client de l'API data du
02, la recherche (`/catalogue`) et la fiche de série (`/series/{series_id}`), contre la
doublure du 02 — chaque réponse validée contre son contrat 0.5.0.

Un test nommé par critère d'acceptation des stories V2 et V3 retenus dans ce lot
(préfixes `test_v2_`, `test_v3_`), puis le client, les cas dégradés, la configuration,
le journal et les métriques. Noms et textes factices.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup
from conftest import ENVIRONNEMENT, HACHEUR_RAPIDE, Navigateur, journal_complet
from doublure_api import CLE_CATALOGUE_ATTENDUE, Doublure, Rendu, fiche_catalogue
from fastapi import FastAPI

from application.app import creer_application
from application.client_api import Metadonnees
from application.config import (
    ConfigurationInvalide,
    DelaisCatalogue,
    Reglages,
    charger,
)
from application.metriques import Metriques
from application.pages_catalogue import DECALAGE_MAX
from application.presentation import metadonnees_de

CONTRAT_02 = json.loads(
    (Path(__file__).resolve().parents[2] / "02_api_manga" / "openapi.json").read_text(
        encoding="utf-8"
    )
)
CLIENT = ("127.0.0.1", 50000)


def configure(reglages: Reglages, **delais: float) -> Reglages:
    """Le catalogue configuré : la clé attendue par la doublure, des délais courts."""
    return replace(
        reglages,
        catalogue=replace(
            reglages.catalogue,
            cle=CLE_CATALOGUE_ATTENDUE,
            delais=DelaisCatalogue(**({"connexion_s": 0.5, "reponse_s": 0.2} | delais)),
        ),
    )


@pytest.fixture
def appli(
    doublure: Doublure, reglages: Reglages
) -> Iterator[tuple[FastAPI, Navigateur]]:
    app = creer_application(
        configure(reglages),
        transport=httpx.ASGITransport(app=doublure.app),
        hacheur=HACHEUR_RAPIDE,
    )
    with Navigateur(app, client=CLIENT) as client:
        yield app, client


@pytest.fixture
def client(appli: tuple[FastAPI, Navigateur]) -> Navigateur:
    return appli[1]


def soupe(reponse: httpx.Response) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def resultats(reponse: httpx.Response) -> list[str]:
    return [a.get_text() for a in soupe(reponse).select("main ol li > a")]


# --------------------------------------------------------------------------- #
#  V2 — rechercher un titre ou un auteur
# --------------------------------------------------------------------------- #
def test_v2_la_recherche_porte_sur_titres_autres_titres_et_auteurs(client, doublure):
    r = client.get("/catalogue?q=factice")
    lignes = soupe(r).select("main ol li")

    assert r.status_code == 200
    assert resultats(r) == ["Série factice A", "Série factice B", "Série factice C"]
    assert "trouvé par" not in lignes[0].get_text()
    assert "trouvé par : Titre factice second" in lignes[1].get_text()
    assert "trouvé par : auteur" in lignes[2].get_text()
    assert [a["href"] for a in soupe(r).select("main ol li > a")] == [
        "/series/101",
        "/series/102",
        "/series/103",
    ]


def test_v2_la_saisie_part_au_catalogue_telle_quelle_bornes_retirees(client, doublure):
    client.get("/catalogue?q=%20%20Sh%C3%B4nen%20")

    assert doublure.recherches == [("Shônen", 20, 0)]


def test_v2_le_classement_est_celui_du_catalogue_et_la_ligne_dit_l_essentiel(client):
    lignes = soupe(client.get("/catalogue?q=factice")).select("main ol li small")

    assert lignes[0].get_text() == (
        "Manga · 2001 · Autrice FACTICE · note des lecteurs 8,2/10 · édité en France"
    )


@pytest.mark.parametrize(
    ("page", "attendue", "decalage"),
    [("1", 1, 0), ("2", 2, 20), ("3", 3, 40), ("abc", 1, 0), ("0", 1, 0)],
)
def test_v2_la_pagination_se_parcourt(client, doublure, page, attendue, decalage):
    doublure.choisir(catalogue="plusieurs_pages")
    r = client.get(f"/catalogue?q=factice&page={page}")
    nav = soupe(r).select_one("main nav")
    liens = {a.get_text(): a["href"] for a in nav.select("a")}

    assert nav.select_one("[aria-current=page]").get_text() == f"Page {attendue} sur 3"
    assert doublure.recherches[-1] == ("factice", 20, decalage)
    assert ("Page précédente" in liens) is (attendue > 1)
    assert ("Page suivante" in liens) is (attendue < 3)
    if attendue < 3:
        assert liens["Page suivante"] == f"/catalogue?q=factice&page={attendue + 1}"


def test_v2_une_page_hors_bornes_est_ramenee_a_la_derniere(client, doublure):
    doublure.choisir(catalogue="plusieurs_pages")
    r = client.get("/catalogue?q=factice&page=40")

    assert "Page 3 sur 3" in soupe(r).select_one("main nav").get_text()
    assert [d for _, _, d in doublure.recherches] == [780, 40]
    assert len(resultats(r)) == 5


def test_v2_une_seule_page_n_a_pas_de_pagination(client, doublure):
    doublure.choisir(catalogue="une_page")
    r = client.get("/catalogue?q=factice")

    assert len(resultats(r)) == 20 and soupe(r).select_one("main nav") is None


@pytest.mark.parametrize(
    "q", ["a", "%20a%20", "x" * 101, "%20%20%20"], ids=["1", "1 entouré", "101", "vide"]
)
def test_v2_une_saisie_hors_bornes_est_dite_au_champ_sans_appel(client, doublure, q):
    r = client.get(f"/catalogue?q={q}")
    page = soupe(r)
    champ = page.select_one("#q")

    assert r.status_code == 422 and r.headers["content-type"].startswith("text/html")
    assert "de 2 à 100 caractères" in page.select_one("#q-erreur").get_text()
    assert champ["aria-invalid"] == "true" and "autofocus" in champ.attrs
    assert "q-erreur" in champ["aria-describedby"].split()
    assert doublure.recherches == []


def test_v2_aucun_resultat_propose_le_conseiller(client, doublure):
    doublure.choisir(catalogue="vide")
    r = client.get("/catalogue?q=zz")
    texte = soupe(r).select_one("main").get_text()

    assert r.status_code == 200
    assert "Aucune série ne correspond à « zz »." in texte
    assert soupe(r).select_one('main a[href="/"]').get_text() == (
        "Décrivez ce que vous cherchez au conseiller"
    )
    assert "La recherche ne corrige pas encore les fautes de frappe." in texte


def test_v2_sans_saisie_la_page_aide_sans_appeler_le_catalogue(client, doublure):
    r = client.get("/catalogue")
    page = soupe(r)

    assert r.status_code == 200
    assert page.select_one('form[role=search][method=get] label[for="q"]') is not None
    assert "majuscules et accents indifférents" in page.select_one("#q-aide").get_text()
    assert doublure.recherches == [] and page.select_one("main ol") is None


def test_v2_les_bornes_de_l_application_sont_celles_du_contrat_du_02(reglages):
    parametres = {
        p["name"]: p["schema"]
        for p in CONTRAT_02["paths"]["/series"]["get"]["parameters"]
    }
    regles = reglages.catalogue

    assert (regles.q_min, regles.q_max) == (
        parametres["q"]["minLength"],
        parametres["q"]["maxLength"],
    )
    assert regles.par_page <= parametres["limit"]["maximum"]
    assert DECALAGE_MAX == parametres["offset"]["maximum"]


# --------------------------------------------------------------------------- #
#  V3 — consulter la page d'une série
# --------------------------------------------------------------------------- #
def paires(dl) -> list[tuple[str, str]]:
    return [
        (dt.get_text(), dd.get_text())
        for dt, dd in zip(dl.select("dt"), dl.select("dd"), strict=True)
    ]


def test_v3_la_ligne_de_titre_est_celle_d_une_carte_du_conseiller(client):
    """Une fiche et une carte aux mêmes métadonnées (celles de la doublure) rendent la
    même ligne : la fiche passe par `metadonnees_de`, comme les cartes."""
    fiche = soupe(client.get("/series/101"))
    carte = soupe(
        client.post(
            "/question",
            data={
                "question": "Un manga factice ?",
                "profil": "mistral-large-2512",
                "temperature": "0.0",
                "reformuler": "1",
                "historique": "",
            },
        )
    ).select_one("main article dl")

    assert paires(fiche.select_one("section.zone-titre dl")) == paires(carte)


def test_v3_la_ligne_de_titre_porte_les_valeurs_de_la_fiche(client, reglages):
    fiche = soupe(client.get("/series/101"))
    attendu = metadonnees_de(
        Metadonnees(
            disponibilite="edite_en_france",
            nb_volumes=12,
            nb_volumes_source="champ",
            statut="termine",
            demographie="Seinen",
            auteurs="Auteur factice",
        ),
        reglages.textes,
    )

    assert paires(fiche.select_one(".zone-titre dl")) == list(attendu)
    assert "Type : Manga · Année (première parution) : 2001" in (
        fiche.select_one(".zone-titre").get_text()
    )


def test_v3_la_note_des_lecteurs_sur_10_et_ses_votes(client, doublure):
    note = (
        soupe(client.get("/series/101")).select_one(".zone-note").get_text(strip=True)
    )
    doublure.choisir(catalogue="serie_sans_note")
    sans = (
        soupe(client.get("/series/101")).select_one(".zone-note").get_text(strip=True)
    )

    assert note == "Note des lecteurs : 8,7/10 (6 936 votes)"
    assert sans == "Pas encore de note des lecteurs."


def test_v3_les_zones_du_reseau_et_des_listes_sont_nommees(client):
    page = soupe(client.get("/series/101"))
    reseau = page.select_one("section.zone-critiques-reseau")
    listes = page.select_one("section.zone-listes")

    assert reseau.select_one("h2").get_text() == "Les libraires du réseau"
    assert "Aucune critique de libraire pour l'instant." in reseau.get_text()
    assert "Vos listes : bientôt disponible." in listes.get_text()
    for zone in ("zone-titre", "zone-note", "zone-synopsis"):
        assert page.select_one(f"section.{zone}") is not None, zone


def test_v3_le_synopsis_les_genres_et_le_lien_externe(client):
    page = soupe(client.get("/series/101"))
    lien = page.select_one('main a[href^="https://"]')

    assert page.select_one("h1").get_text() == "Série factice A"
    assert "Résumé factice de la série." in page.select_one(".zone-synopsis").get_text()
    assert [li.get_text() for li in page.select(".zone-synopsis li")] == [
        "Aventure",
        "Comédie",
    ]
    assert lien["href"] == "https://catalogue.invalid/serie/101"
    assert "noopener" in lien["rel"] and "(site externe)" in lien.get_text()


def test_v3_une_adresse_qui_n_est_pas_http_n_est_pas_un_lien(client, monkeypatch):
    import doublure_api

    monkeypatch.setattr(
        doublure_api,
        "fiche_selon",
        lambda nom, sid: Rendu(200, fiche_catalogue(sid, "X", url="javascript:x()")),
    )
    page = soupe(client.get("/series/101"))

    assert "javascript" not in str(page.select_one("main"))
    assert page.select_one('main a[rel~="noopener"]') is None


def test_v3_les_critiques_collectees_ne_sont_jamais_demandees(client, doublure):
    client.get("/catalogue?q=factice")
    for ident in (101, 102, 103):
        client.get(f"/series/{ident}")

    assert doublure.appels_catalogue.get("/series/{series_id}/reviews") is None
    assert doublure.appels_catalogue["/series/{series_id}"] == 3


@pytest.mark.parametrize(
    "chemin",
    ["abc", "-1", "1e3", "9" * 30, "١٢"],
    ids=["lettres", "négatif", "exposant", "long", "arabe"],
)
def test_v3_un_identifiant_hors_forme_est_introuvable_sans_appel(
    client, doublure, chemin
):
    r = client.get(f"/series/{chemin}")

    assert r.status_code == 404 and "Page introuvable" in r.text
    assert doublure.appels_catalogue.get("/series/{series_id}") is None


def test_v3_une_serie_inconnue_est_la_page_introuvable(client, doublure):
    doublure.choisir(catalogue="serie_inconnue")
    r = client.get("/series/101")

    assert r.status_code == 404
    assert soupe(r).select_one("h1").get_text() == "Page introuvable"
    assert soupe(r).select_one('header nav a[href="/catalogue"]') is not None


# --------------------------------------------------------------------------- #
#  Les cas dégradés : un encart nommé, 503 ; le conseiller continue
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "scenario", ["indisponible", "cle_refusee", "lent", "corps_hors_contrat"]
)
@pytest.mark.parametrize("chemin", ["/catalogue?q=factice", "/series/101"])
def test_un_catalogue_qui_ne_repond_pas_est_un_encart_503(
    client, doublure, scenario, chemin
):
    doublure.choisir(catalogue=scenario)
    r = client.get(chemin)
    encart = soupe(r).select_one("main [role=alert]")

    assert r.status_code == 503
    assert "Le catalogue ne répond pas ; le conseiller reste accessible." in (
        encart.get_text()
    )
    assert client.get("/").status_code == 200, "le conseiller répond"


def test_un_catalogue_non_configure_le_dit_sans_bloquer_le_reste(navigateur):
    for chemin in ("/catalogue", "/catalogue?q=factice", "/series/101"):
        r = navigateur.get(chemin)
        assert r.status_code == 503, chemin
        assert "Le catalogue ne répond pas" in r.text, chemin
    assert navigateur.get("/").status_code == 200
    assert navigateur.get("/sante").json()["catalogue"] == "non_configure"


def test_la_cle_refusee_se_journalise_sans_la_cle(client, doublure, caplog):
    doublure.choisir(catalogue="cle_refusee")
    with caplog.at_level(logging.INFO, logger="application"):
        client.get("/catalogue?q=factice")

    assert "catalogue_cle_refusee" in [r.getMessage() for r in caplog.records]
    assert CLE_CATALOGUE_ATTENDUE not in journal_complet(caplog)


# --------------------------------------------------------------------------- #
#  Le client : la clé sur chaque appel, la sonde sans clé, ni secret ni saisie au
#  journal, `/sante` et les métriques
# --------------------------------------------------------------------------- #
def test_chaque_appel_porte_la_cle_du_catalogue(client, doublure):
    client.get("/catalogue?q=factice")
    client.get("/series/101")

    assert doublure.cles_catalogue == [CLE_CATALOGUE_ATTENDUE] * 2


def test_ni_la_cle_ni_la_saisie_au_journal(client, doublure, caplog):
    with caplog.at_level(logging.INFO, logger="application"):
        client.get("/catalogue?q=saisie-tres-particuliere")
        client.get("/series/101")
    texte = journal_complet(caplog)

    assert "catalogue_recherche" in [r.getMessage() for r in caplog.records]
    assert CLE_CATALOGUE_ATTENDUE not in texte
    assert "saisie-tres-particuliere" not in texte


def test_sante_porte_l_etat_du_catalogue(client, doublure, reglages, port_ferme):
    assert client.get("/sante").json()["catalogue"] == "ok"
    assert doublure.appels_catalogue["/live"] >= 1
    ferme = creer_application(
        replace(
            configure(reglages),
            catalogue=replace(configure(reglages).catalogue, adresse=port_ferme),
        ),
        hacheur=HACHEUR_RAPIDE,
    )
    with Navigateur(ferme, client=CLIENT) as autre:
        assert autre.get("/sante").json()["catalogue"] == "indisponible"


def test_les_metriques_comptent_les_appels_et_posent_l_etat(client, doublure):
    client.get("/catalogue?q=factice")
    client.get("/series/101")
    doublure.choisir(catalogue="serie_inconnue")
    client.get("/series/101")
    doublure.choisir(catalogue="cle_refusee")
    client.get("/series/101")
    sortie = client.get("/metrics").text

    for ligne in (
        'application_catalogue_appels_total{route_catalogue="series",statut="ok"} 1.0',
        'application_catalogue_appels_total{route_catalogue="serie",statut="ok"} 1.0',
        'application_catalogue_appels_total{route_catalogue="serie",statut="introuvable"}'
        " 1.0",
        'application_catalogue_appels_total{route_catalogue="serie",statut="cle"} 1.0',
        'application_catalogue_etat{etat="cle"} 1.0',
        'application_catalogue_etat{etat="ok"} 0.0',
    ):
        assert ligne in sortie, ligne


def test_le_repr_du_client_ne_montre_pas_la_cle():
    from application.client_catalogue import ClientCatalogue

    client = ClientCatalogue(
        "http://127.0.0.1:8000",
        CLE_CATALOGUE_ATTENDUE,
        DelaisCatalogue(1.0, 1.0),
        Metriques(),
    )
    assert repr(client) == "ClientCatalogue(adresse='http://127.0.0.1:8000')"
    asyncio.run(client.fermer())


def test_les_pages_du_catalogue_ne_sont_pas_hors_cache(client):
    for chemin in ("/catalogue", "/catalogue?q=factice", "/series/101"):
        assert client.get(chemin).headers.get("cache-control") != "no-store", chemin


def test_le_menu_mene_au_catalogue_sur_chaque_page(client):
    for chemin in ("/", "/a-propos", "/catalogue", "/series/101"):
        page = soupe(client.get(chemin))
        liens = [a["href"] for a in page.select("header nav a")]
        assert liens[:2] == ["/", "/catalogue"], chemin


# --------------------------------------------------------------------------- #
#  La configuration
# --------------------------------------------------------------------------- #
def ecarts(**variables: str) -> list[str]:
    with pytest.raises(ConfigurationInvalide) as refus:
        charger({**ENVIRONNEMENT, **variables})
    return refus.value.ecarts


def test_en_developpement_sans_cle_le_catalogue_n_est_pas_configure():
    catalogue = charger(ENVIRONNEMENT).catalogue

    assert catalogue.cle is None and catalogue.adresse == "http://127.0.0.1:8000"
    assert "cle=" not in repr(catalogue)


@pytest.mark.parametrize(
    ("variables", "attendu"),
    [
        (
            {"CATALOGUE_ADRESSE": "http://catalogue:8000"},
            "APPLICATION_CLE_CATALOGUE : absente — obligatoire quand"
            " CATALOGUE_ADRESSE est posée",
        ),
        (
            {"APPLICATION_CLE_CATALOGUE": "clé-hors-ascii"},
            "APPLICATION_CLE_CATALOGUE : caractères hors ASCII — clé illisible",
        ),
        (
            {"APPLICATION_CLE_CATALOGUE": "k" * 40, "CATALOGUE_ADRESSE": "ftp://x"},
            "adresse du catalogue : attendu http(s)://hôte[:port]",
        ),
    ],
    ids=["adresse sans clé", "hors ASCII", "adresse"],
)
def test_une_configuration_fautive_du_catalogue_empeche_de_demarrer(variables, attendu):
    assert attendu in ecarts(**variables)


@pytest.mark.parametrize("environnement", ["preproduction", "production"])
def test_hors_developpement_la_cle_du_catalogue_est_obligatoire(environnement):
    base = {"preproduction": "apimanga_preprod", "production": "apimanga"}[
        environnement
    ]
    assert (
        f"APPLICATION_CLE_CATALOGUE : absente — obligatoire en {environnement}"
        in ecarts(
            APPLICATION_ENVIRONNEMENT=environnement,
            APPLICATION_BASE_DSN=f"postgresql://app_conseiller@localhost:5432/{base}",
            APPLICATION_CLE_SESSION="s" * 40,
        )
    )


def test_l_adresse_et_la_cle_de_l_environnement_l_emportent():
    catalogue = charger(
        {
            **ENVIRONNEMENT,
            "CATALOGUE_ADRESSE": "http://catalogue:8000",
            "APPLICATION_CLE_CATALOGUE": "k" * 40,
        }
    ).catalogue

    assert (catalogue.adresse, catalogue.cle) == ("http://catalogue:8000", "k" * 40)


@pytest.mark.parametrize(
    ("remplacement", "attendu"),
    [
        (
            ("par_page = 20", "par_page = 51"),
            "reglages.toml : [catalogue.par_page] 50 au plus (la borne de l'API data)",
        ),
        (
            ("q_min = 2", "q_min = 101"),
            "reglages.toml : [catalogue] q_min au plus q_max",
        ),
    ],
    ids=["plus de 50 par page", "bornes inversées"],
)
def test_des_bornes_du_catalogue_fautives_empechent_de_demarrer(
    tmp_path, remplacement, attendu
):
    from application.config import FICHIER_REGLAGES

    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    assert texte.count(remplacement[0]) == 1
    fichier = tmp_path / "reglages.toml"
    fichier.write_text(texte.replace(*remplacement), encoding="utf-8")
    with pytest.raises(ConfigurationInvalide) as refus:
        charger(ENVIRONNEMENT, fichier)

    assert attendu in refus.value.ecarts


@pytest.mark.parametrize("chemin", ["/catalogue?q=factice", "/series/101"])
def test_un_statut_hors_contrat_du_catalogue_est_un_encart(client, monkeypatch, chemin):
    """Ni 200 ni 404 (ici un 422 du catalogue, conforme à son contrat) : une réponse
    que l'application ne sait pas lire, donc l'encart — jamais une page fausse."""
    import doublure_api

    refus = Rendu(
        422,
        {"detail": [{"loc": ["query", "q"], "msg": "refus", "type": "value_error"}]},
    )
    monkeypatch.setattr(doublure_api, "recherche_catalogue", lambda *_: refus)
    monkeypatch.setattr(doublure_api, "fiche_selon", lambda *_: refus)
    r = client.get(chemin)

    assert r.status_code == 503 and "Le catalogue ne répond pas" in r.text
