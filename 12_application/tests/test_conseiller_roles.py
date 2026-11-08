"""L'écran du conseiller selon le rôle (E4, lot 2b, spec 01) : un test nommé par critère
d'acceptation de V1, A7, L6 et de la fin de V3 (préfixes `test_v1_`, `test_a7_`,
`test_l6_`, `test_v3_`).

Le public — visiteur, client — n'a ni réglages, ni case du mode démonstration, ni liste
des modes, ni bloc du complément : rien de cela n'est dans le HTML qu'il reçoit, et un
champ forgé dans son `POST` est ignoré (la demande part avec les défauts, en strict).
Le libraire a les réglages et la liste ; l'administrateur, en plus, la case. Le pont :
la fiche mène au conseiller, la question pré-remplie du titre exact lu dans le 02 ; une
réponse qui refuse ou ne cite aucune série mène au catalogue.

Comptes sur une base jetable ; la doublure de l'API et du catalogue en mémoire.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from bs4 import BeautifulSoup
from conftest import Navigateur

QUESTION = "Un manga factice pour débuter ?"
#: Les champs que le public n'a pas, et que son HTML ne porte jamais.
CHAMPS_RESERVES = ("profil", "temperature", "mode", "mode_demonstration", "reformuler")
#: Ce qu'un `POST` forgé par le public essaie d'imposer.
FORGES = {
    "profil": "gpt-6-luna",
    "temperature": "0.7",
    "mode": "enrichi",
    "mode_demonstration": "1",
}
#: La demande que le public envoie, quoi qu'il forge : les défauts, en strict.
DEFAUTS = {
    "profil": "mistral-large-2512",
    "temperature": 0.0,
    "reformuler": True,
    "mode": "strict",
}


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def poser(client: Navigateur, question: str = QUESTION, **champs: str):
    return client.post(
        "/question", data={"question": question, "historique": "", **champs}
    )


def sans_reglages(html: str) -> list[str]:
    """Ce que le HTML porte des champs réservés (rien, pour le public)."""
    return [nom for nom in CHAMPS_RESERVES if f'name="{nom}"' in html]


def envoye(doublure, *cles: str) -> dict[str, object]:
    dernier = doublure.corps_recus[-1]
    return {cle: dernier[cle] for cle in cles}


# --------------------------------------------------------------------------- #
#  V1 : le public, sans réglages, en strict
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("role", [None, "client"], ids=["visiteur", "client"])
def test_v1_le_html_public_ne_porte_ni_reglages_ni_case_ni_liste(
    connecter, doublure, role
):
    doublure.choisir(info="deux_modes")
    client = connecter(role)

    html = client.get("/").text
    page = BeautifulSoup(html, "html.parser")

    assert sans_reglages(html) == []
    assert page.select_one("details") is None, "pas de bloc « Réglages »"
    assert "Réglages" not in page.get_text()
    assert page.select_one("textarea#question") is not None


@pytest.mark.parametrize("role", [None, "client"], ids=["visiteur", "client"])
def test_v1_la_mention_ia_reste_et_la_phrase_d_aide_publique_est_dite(connecter, role):
    page = soupe(connecter(role).get("/"))

    assert "Vous parlez à une IA." in page.header.get_text()
    assert (
        "Le conseiller répond à partir des critiques des libraires du réseau."
        in page.main.get_text()
    )


@pytest.mark.parametrize("role", [None, "client"], ids=["visiteur", "client"])
def test_v1_un_post_force_par_le_public_part_avec_les_defauts_en_strict(
    connecter, doublure, role
):
    doublure.choisir(info="deux_modes")
    client = connecter(role)

    r = poser(client, **FORGES)
    page = soupe(r)

    assert r.status_code == 200, "ignoré, pas refusé : le champ n'existe pas pour lui"
    assert envoye(doublure, *DEFAUTS) == DEFAUTS
    assert page.select_one("#trace") is None, "mode démonstration ignoré"
    assert sans_reglages(r.text) == []


def test_v1_une_temperature_ou_un_profil_faux_forges_par_le_public_ne_coutent_rien(
    connecter, doublure
):
    r = poser(connecter(None), profil="profil-inconnu", temperature="chaud")

    assert r.status_code == 200
    assert envoye(doublure, "profil", "temperature") == {
        "profil": "mistral-large-2512",
        "temperature": 0.0,
    }


def test_v1_la_reformulation_du_public_est_le_defaut_de_l_api(connecter, doublure):
    """Sans case, le public prend le défaut publié (vrai) — même sans le champ."""
    poser(connecter(None))

    assert doublure.corps_recus[-1]["reformuler"] is True


@pytest.mark.parametrize("role", [None, "client"], ids=["visiteur", "client"])
def test_v1_le_bloc_du_complement_n_est_jamais_servi_au_public(
    connecter, doublure, role
):
    """Même si l'API en rendait un (le public est en strict, elle n'en rend pas)."""
    doublure.choisir(reponse="enrichi_avec_etiquette")

    r = poser(connecter(role))
    texte = soupe(r).get_text()

    assert r.status_code == 200
    for absent in (
        "Complément factice.",
        "Étiquette factice de l'API",
        "Non vérifié",
        "Titre factice hors catalogue",
    ):
        assert absent not in texte, absent


# --------------------------------------------------------------------------- #
#  L6 : le libraire, ses réglages et la liste des modes
# --------------------------------------------------------------------------- #
def test_l6_le_libraire_a_les_reglages_et_la_liste_sans_la_case(libraire, doublure):
    doublure.choisir(info="deux_modes")

    page = soupe(libraire.get("/"))

    assert page.select_one("details summary").get_text() == "Réglages"
    assert page.select_one("select#profil") is not None
    assert page.select("input[name=temperature]")
    assert page.select_one("#reformuler") is not None
    modes = page.select("select#mode option")
    assert [o["value"] for o in modes] == ["strict", "enrichi"]
    assert page.select_one("select#mode option[selected]")["value"] == "strict"
    assert page.select_one("#mode_demonstration") is None
    assert 'name="mode_demonstration"' not in str(page)


def test_l6_un_seul_mode_publie_pas_de_liste(libraire):
    page = soupe(libraire.get("/"))

    assert page.select_one("select#profil") is not None
    assert page.select_one("select#mode") is None


def test_l6_le_mode_enrichi_choisi_par_le_libraire_part(libraire, doublure):
    doublure.choisir(info="deux_modes")

    poser(libraire, mode="enrichi")

    assert doublure.corps_recus[-1]["mode"] == "enrichi"


def test_l6_un_mode_hors_liste_cede_au_defaut(libraire, doublure):
    poser(libraire, mode="enrichi")  # un seul mode publié : strict

    assert doublure.corps_recus[-1]["mode"] == "strict"


def test_l6_le_libraire_ne_peut_pas_allumer_le_mode_demonstration(libraire):
    page = soupe(poser(libraire, mode_demonstration="1"))

    assert page.select_one("#trace") is None
    assert page.select_one("#mode_demonstration") is None


def test_l6_le_complement_a_l_etiquette_fournie_par_l_api(libraire, doublure):
    doublure.choisir(reponse="enrichi_avec_etiquette")

    page = soupe(poser(libraire))
    bloc = next(
        a for a in page.select("article") if "Complément factice." in a.get_text()
    )

    assert bloc.select_one("strong").get_text() == "Étiquette factice de l'API"
    assert "Titre factice hors catalogue" in bloc.get_text()


def test_l6_sans_etiquette_de_l_api_le_texte_du_12(libraire, doublure):
    doublure.choisir(reponse="enrichi_sans_etiquette")

    page = soupe(poser(libraire))
    bloc = next(
        a for a in page.select("article") if "Complément factice." in a.get_text()
    )

    assert bloc.select_one("strong").get_text() == (
        "Non vérifié — connaissances du modèle, sans source"
    )


# --------------------------------------------------------------------------- #
#  A7 : la case du mode démonstration, à l'administrateur seul
# --------------------------------------------------------------------------- #
def test_a7_l_admin_a_la_case_les_reglages_et_la_liste(admin, doublure):
    doublure.choisir(info="deux_modes")

    page = soupe(admin.get("/"))

    assert page.select_one("#mode_demonstration") is not None
    assert page.select_one("select#profil") is not None
    assert page.select_one("select#mode") is not None


def test_a7_la_case_cochee_par_l_admin_montre_la_trace(admin):
    page = soupe(poser(admin, mode_demonstration="1"))

    assert page.select_one("#trace") is not None


@pytest.mark.parametrize("role", [None, "client", "libraire"])
def test_a7_le_html_des_autres_roles_ne_porte_aucune_trace_de_la_case(connecter, role):
    html = connecter(role).get("/").text

    assert "mode_demonstration" not in html
    assert "Mode démonstration" not in html


# --------------------------------------------------------------------------- #
#  V3 (fin) : le pont fiche → conseiller
# --------------------------------------------------------------------------- #
def test_v3_la_fiche_mene_au_conseiller(connecter):
    page = soupe(connecter(None).get("/series/101"))

    lien = page.select_one(".zone-pont-conseiller a")
    assert lien["href"] == "/?serie=101"
    assert lien.get_text() == "Demander au conseiller"


def test_v3_la_question_est_pre_remplie_du_titre_exact_du_catalogue(
    connecter, doublure
):
    r = connecter(None).get("/?serie=101")

    assert r.status_code == 200
    assert soupe(r).select_one("textarea#question").get_text() == (
        "Que pensent les libraires de Série factice A ?"
    )
    assert doublure.appels_catalogue.get("/series/{series_id}") == 1


def test_v3_le_titre_vient_du_catalogue_jamais_de_l_adresse(connecter):
    r = connecter(None).get("/?serie=101&titre=<b>piege</b>&question=piege")
    page = soupe(r)

    assert "piege" not in page.select_one("textarea#question").get_text()
    assert "<b>piege" not in r.text


@pytest.mark.parametrize(
    "valeur", ["abc", "", "-1", "1" * 19, "١٢", "101x"], ids=lambda v: repr(v)
)
def test_v3_un_identifiant_illisible_laisse_la_page_vide_sans_appel(
    connecter, doublure, valeur
):
    r = connecter(None).get("/", params={"serie": valeur})

    assert r.status_code == 200
    assert soupe(r).select_one("textarea#question").get_text() == ""
    assert "/series/{series_id}" not in doublure.appels_catalogue


@pytest.mark.parametrize(
    "scenario", ["serie_inconnue", "indisponible", "cle_refusee", "corps_hors_contrat"]
)
def test_v3_serie_inconnue_ou_catalogue_en_panne_la_page_sans_pre_remplissage(
    connecter, doublure, scenario
):
    doublure.choisir(catalogue=scenario)

    r = connecter(None).get("/?serie=999999999")

    assert r.status_code == 200
    assert soupe(r).select_one("textarea#question").get_text() == ""


def test_v3_sans_catalogue_configure_la_page_sans_pre_remplissage(navigateur):
    r = navigateur.get("/?serie=101")

    assert r.status_code == 200
    assert soupe(r).select_one("textarea#question").get_text() == ""


# --------------------------------------------------------------------------- #
#  V1 : le pont conseiller → catalogue
# --------------------------------------------------------------------------- #
def lien_catalogue(reponse) -> str | None:
    lien = soupe(reponse).select_one(".zone-pont-catalogue a")
    if lien is None:
        return None
    assert lien.get_text() == "Chercher ce titre dans le catalogue"
    return lien["href"]


@pytest.mark.parametrize("scenario", ["refus", "refus_aucun_passage", "sans_carte"])
def test_v1_un_refus_ou_une_reponse_sans_serie_mene_au_catalogue(
    connecter, doublure, scenario
):
    doublure.choisir(reponse=scenario)

    cible = lien_catalogue(poser(connecter(None), "Naruto"))

    assert cible is not None
    assert urlsplit(cible).path == "/catalogue"
    assert parse_qs(urlsplit(cible).query) == {"q": ["Naruto"]}


def test_v1_avec_des_cartes_pas_de_lien_vers_le_catalogue(connecter):
    assert lien_catalogue(poser(connecter(None))) is None


def test_v1_la_question_est_coupee_a_100_caracteres_sans_espaces_aux_bords(
    connecter, doublure
):
    doublure.choisir(reponse="refus")
    question = "  " + "a" * 99 + " " + "b" * 50  # la 100e est l'espace

    cible = lien_catalogue(poser(connecter(None), question))

    assert parse_qs(urlsplit(cible).query)["q"] == ["a" * 99]


def test_v1_une_question_trop_courte_pour_le_catalogue_pas_de_lien(connecter, doublure):
    doublure.choisir(reponse="refus")

    assert lien_catalogue(poser(connecter(None), "a")) is None
