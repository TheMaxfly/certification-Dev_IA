"""Le mode démonstration : éteint, la production ne change pas ; allumé, la page montre
ce que l'API a déjà rendu, et rien n'est envoyé de plus."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import doublure_api as d
import pytest
from bs4 import BeautifulSoup, Tag
from conftest import ENVIRONNEMENT, application_sur
from ecrans import echanges
from fastapi.testclient import TestClient

from application.config import FICHIER_REGLAGES, ConfigurationInvalide, charger

QUESTION = "Un manga factice pour débuter ?"
PHRASE = (
    "Le score est la similarité entre la question et le meilleur passage d'une série "
    "(cosinus, 0 à 1). Il dit à quel point un texte ressemble à la question, pas si la "
    "réponse est vraie : la recherche propose, le modèle choisit parmi ces séries, et "
    "il ne peut en citer aucune autre en carte."
)
INFOBULLE = "similarité entre la question et le passage (0 à 1)"
#: Chaque score de la réponse nominale, tel que l'écran les écrirait.
SCORES = ("0,83", "0,76", "0,64", "0,52", "0,40", "0,47", "0,61")
#: Les passages que le modèle n'a pas cités, y compris dans une série citée.
NON_CITES = (12, 31, 41, 51)
#: Les séries de la recherche que le modèle n'a pas citées.
TITRES_NON_CITES = ("Série factice C", "Série factice D", "Série factice E")


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def plat(element: Tag) -> str:
    return " ".join(element.get_text().split())


def poser(navigateur, question: str = QUESTION, *, coche: bool = False, **champs):
    donnees = {
        "question": question,
        "profil": "mistral-large-2512",
        "temperature": "0.0",
        "reformuler": "1",
        "historique": "",
        **champs,
    }
    if coche:
        donnees["mode_demonstration"] = "1"
    return navigateur.post("/question", data=donnees)


def case_cochee(page: BeautifulSoup) -> bool:
    return page.select_one("#mode_demonstration").has_attr("checked")


def lignes_du_classement(page: BeautifulSoup) -> list[list[str]]:
    return [
        [plat(cellule) for cellule in ligne.select("td")]
        for ligne in page.select("#trace ~ table tbody tr")
    ]


def citee(serie_id: int, titre: str, rang: int, fragments: list[int]) -> dict:
    return {
        "serie_id": serie_id,
        "titre": titre,
        "rang": rang,
        "fragment_ids": fragments,
    }


def rendre_sur(monkeypatch, **champs) -> None:
    """Le scénario nominal, certains champs remplacés ; la doublure les valide."""
    monkeypatch.setitem(
        d.SCENARIOS_REPONSE,
        "nominale",
        lambda: d.Rendu(200, d.reponse_nominale(**champs)),
    )


def valeurs(page: BeautifulSoup) -> dict[str, str]:
    """Les définitions de la section : leur nom → leur valeur."""
    return {
        plat(terme): plat(terme.find_next_sibling("dd"))
        for terme in page.select("#trace ~ dl dt")
    }


# --------------------------------------------------------------------------- #
#  Décoché : la production
# --------------------------------------------------------------------------- #
def test_le_defaut_est_la_production(navigateur):
    page = soupe(navigateur.get("/"))

    assert not case_cochee(page)
    assert charger(ENVIRONNEMENT).mode_demonstration_defaut is False


def test_la_case_a_son_texte_et_vit_dans_les_reglages(navigateur):
    page = soupe(navigateur.get("/"))
    case = page.select_one("#mode_demonstration")

    assert case["type"] == "checkbox"
    assert case.find_parent("details").summary.get_text() == "Réglages"
    assert plat(page.select_one('label[for="mode_demonstration"]')) == (
        "Mode démonstration : afficher les scores et la trace de la recherche"
    )


def test_la_case_part_avec_le_formulaire_de_la_question(navigateur):
    """Les autres tests postent le champ à la main : ceci est ce qu'un navigateur
    enverrait."""
    case = soupe(navigateur.get("/")).select_one("#mode_demonstration")
    formulaire = case.find_parent("form")

    assert case["name"] == "mode_demonstration"
    assert case["value"] == "1"
    assert formulaire["action"] == "/question"
    assert formulaire["method"] == "post"


def test_decoche_le_html_ne_porte_ni_score_ni_rang_ni_non_cite_ni_section(navigateur):
    r = poser(navigateur)
    html = r.text
    page = soupe(r)

    assert r.status_code == 200
    # Le seul « score » de la page : l'étiquette de la case.
    assert html.count("score") == 1
    assert "score" in plat(page.select_one('label[for="mode_demonstration"]'))
    sans_case = html.replace("afficher les scores et la trace de la recherche", "")
    assert "score" not in sans_case.lower()
    assert "trace de la recherche" not in sans_case.lower()
    assert "Trace de la recherche" not in html
    assert not re.search(r"\brang\b", html, re.IGNORECASE)
    assert page.select_one("#trace") is None
    assert not page.select("section[aria-live=polite] table")
    assert not page.select("abbr")
    assert INFOBULLE not in html
    assert PHRASE not in plat(page)
    for valeur in SCORES:
        assert valeur not in html
    for fragment in NON_CITES:
        assert f"Passage factice n° {fragment}." not in html
    for titre in TITRES_NON_CITES:
        assert titre not in html
    # Les cartes de la 04, et les détails techniques tels quels.
    assert [h3["id"] for h3 in page.select("h3")] == ["serie-101", "serie-102"]
    assert page.select_one("details details") is None
    assert "Détails techniques" in html
    assert "Corpus v2 · encodage 3 · identifiant api-factice-0001" in html


def test_decoche_un_refus_ne_porte_ni_score_ni_section(navigateur, doublure):
    doublure.choisir(reponse="refus_inconnu")

    html = poser(navigateur).text

    assert html.count("score") == 1
    assert "Trace de la recherche" not in html
    assert "Série factice C" not in html
    assert "Passage factice n° 31." not in html


# --------------------------------------------------------------------------- #
#  Aucun effet amont
# --------------------------------------------------------------------------- #
def test_la_requete_est_identique_coche_ou_non(navigateur, doublure):
    poser(navigateur)
    poser(navigateur, coche=True)

    sans, avec = doublure.corps_recus
    assert sans == avec
    assert set(avec) == {
        "question",
        "historique",
        "reformuler",
        "profil",
        "temperature",
        "mode",
        "identite",
    }


def test_le_reglage_n_est_pas_envoye_a_l_api_qui_le_refuserait(navigateur, doublure):
    """Le contrat refuse un champ inconnu : la doublure noterait un écart, et le 422
    empêcherait la réponse."""
    r = poser(navigateur, coche=True)

    assert r.status_code == 200
    assert "mode_demonstration" not in doublure.corps_recus[0]
    assert not doublure.ecarts
    proprietes = d.CONTRAT["components"]["schemas"]["DemandeReponse"]["properties"]
    assert "mode_demonstration" not in proprietes


# --------------------------------------------------------------------------- #
#  Coché : ce que l'API rend
# --------------------------------------------------------------------------- #
def test_coche_chaque_carte_citee_porte_son_rang_et_son_score(navigateur):
    page = soupe(poser(navigateur, coche=True))
    carte_a = page.select_one("#serie-101").find_parent("article")
    carte_b = page.select_one("#serie-102").find_parent("article")

    assert "rang 1 — score 0,83" in plat(carte_a)
    assert "rang 2 — score 0,76" in plat(carte_b)
    for carte in (carte_a, carte_b):
        assert {a["title"] for a in carte.select("abbr")} == {INFOBULLE}


def test_coche_chaque_passage_cite_porte_son_score_et_son_identifiant(navigateur):
    page = soupe(poser(navigateur, coche=True))
    carte_a = page.select_one("#serie-101").find_parent("article")
    carte_b = page.select_one("#serie-102").find_parent("article")

    assert [plat(b.footer) for b in carte_a.select("blockquote")] == [
        "critique de tome — extrait (fragment n° 11) — score 0,83",
    ]
    assert [plat(b.footer) for b in carte_b.select("blockquote")] == [
        "résumé de série — extrait (fragment n° 21) — score 0,76",
        "critique de tome — extrait (fragment n° 22) — score 0,61",
    ]


def test_coche_la_section_vient_apres_les_cartes(navigateur):
    page = soupe(poser(navigateur, coche=True))
    titre = page.select_one("h2#trace")

    assert titre.get_text() == "Trace de la recherche"
    assert page.select("section[aria-live=polite] h2")[-1] is titre
    assert page.select_one("#serie-102").find_next("h2") is titre


def test_coche_la_phrase_fixe_est_en_tete_et_ecrite_une_fois(navigateur):
    page = soupe(poser(navigateur, coche=True))
    phrase = page.select_one("h2#trace").find_next_sibling("p")

    assert plat(phrase) == PHRASE
    assert phrase.select_one("strong").get_text() == "pas"
    assert plat(page).count(PHRASE) == 1


def test_coche_le_classement_a_ses_n_lignes_dans_l_ordre_du_rang(navigateur):
    page = soupe(poser(navigateur, coche=True))

    assert lignes_du_classement(page) == [
        ["1", "Série factice A", "0,83", "citée", "2"],
        ["2", "Série factice B", "0,76", "citée", "2"],
        ["3", "Série factice C", "0,64", "non citée", "1"],
        ["4", "Série factice D", "0,52", "non citée", "1"],
        ["5", "Série factice E", "0,40", "non citée", "1"],
    ]


def test_coche_le_classement_suit_le_rang_pas_l_ordre_recu(navigateur, doublure):
    doublure.choisir(reponse="series_dans_le_desordre")

    lignes = lignes_du_classement(soupe(poser(navigateur, coche=True)))

    assert [ligne[0] for ligne in lignes] == ["1", "2", "3", "4", "5"]


def test_coche_les_citees_ont_une_ancre_vers_leur_carte(navigateur):
    page = soupe(poser(navigateur, coche=True))
    lignes = page.select("#trace ~ table tbody tr")

    assert lignes[0].select_one("a")["href"] == "#serie-101"
    assert lignes[1].select_one("a")["href"] == "#serie-102"
    for ligne in lignes[2:]:
        assert ligne.select_one("a") is None
    assert page.select_one("#serie-101") is not None


def test_coche_les_non_cites_sont_replies_par_serie_avec_leur_texte(navigateur):
    page = soupe(poser(navigateur, coche=True))
    groupes = page.select("#trace ~ details")

    assert [plat(g.summary) for g in groupes] == [
        "Série factice A : passages non cités (1)",
        "Série factice C : passages non cités (1)",
        "Série factice D : passages non cités (1)",
        "Série factice E : passages non cités (1)",
    ]
    for groupe in groupes:
        assert not groupe.has_attr("open")
    a, c = groupes[0], groupes[1]
    assert plat(a.select_one("blockquote p")) == "Passage factice n° 12."
    assert plat(a.select_one("blockquote footer")) == (
        "synopsis — extrait (fragment n° 12) — score 0,47"
    )
    assert plat(c.select_one("blockquote p")) == "Passage factice n° 31."
    assert plat(c.select_one("blockquote footer")) == (
        "critique de tome — extrait (fragment n° 31) — score 0,64"
    )
    # Un passage cité n'y est pas : il est sur sa carte.
    assert "Passage factice n° 11." not in plat(a)


def test_coche_tous_les_passages_cites_rien_a_replier(
    navigateur, doublure, monkeypatch
):
    monkeypatch.setitem(
        d.SCENARIOS_REPONSE,
        "nominale",
        lambda: d.Rendu(
            200,
            d.reponse_nominale(
                series=[d.serie(101, "Série factice A", [d.passage(11)], 1)],
                series_citees=[
                    {
                        "serie_id": 101,
                        "titre": "Série factice A",
                        "rang": 1,
                        "fragment_ids": [11],
                    }
                ],
            ),
        ),
    )

    page = soupe(poser(navigateur, coche=True))

    assert not page.select("#trace ~ details")
    assert "Aucun passage non cité." in plat(page)


def test_coche_une_serie_citee_sans_passage_reste_citee(navigateur, monkeypatch):
    rendre_sur(
        monkeypatch,
        reponse="Série factice A [S:101] convient.",
        series_citees=[citee(101, "Série factice A", 1, [])],
        series=[
            d.serie(101, "Série factice A", [], 1, 0.83),
            d.serie(103, "Série factice C", [d.passage(31, score=0.64)], 2, 0.64),
        ],
    )

    page = soupe(poser(navigateur, coche=True))

    assert lignes_du_classement(page) == [
        ["1", "Série factice A", "0,83", "citée", "0"],
        ["2", "Série factice C", "0,64", "non citée", "1"],
    ]
    assert page.select_one("#trace ~ table a")["href"] == "#serie-101"
    assert "Aucun passage cité pour cette série." in plat(page)


def test_coche_une_serie_citee_sans_fragment_montre_ses_passages_non_cites(
    navigateur, doublure
):
    doublure.choisir(reponse="sans_fragment")

    page = soupe(poser(navigateur, coche=True))

    assert lignes_du_classement(page)[0][3] == "citée"
    assert plat(page.select("#trace ~ details")[0].summary) == (
        "Série factice A : passages non cités (2)"
    )


def test_coche_une_serie_citee_deux_fois_reunit_ses_fragments(navigateur, monkeypatch):
    rendre_sur(
        monkeypatch,
        reponse="Série factice A [S:101] convient.",
        series_citees=[
            citee(101, "Série factice A", 1, [11]),
            citee(101, "Série factice A", 1, [12]),
        ],
        series=[
            d.serie(
                101,
                "Série factice A",
                [d.passage(11, score=0.83), d.passage(12, score=0.47)],
                1,
                0.83,
            ),
        ],
    )

    page = soupe(poser(navigateur, coche=True))
    carte = page.select_one("#serie-101").find_parent("article")

    assert lignes_du_classement(page) == [
        ["1", "Série factice A", "0,83", "citée", "2"],
    ]
    assert [plat(b.p) for b in carte.select("blockquote")] == [
        "Passage factice n° 11.",
        "Passage factice n° 12.",
    ]
    assert not page.select("#trace ~ details")


def test_coche_une_serie_sans_identifiant_est_non_citee_et_sans_ancre(
    navigateur, monkeypatch
):
    sans_identifiant = d.serie(
        107, "Œuvre factice K", [d.passage(71, "synopsis_kitsu", 0.33)], 3, 0.33
    )
    sans_identifiant.update(serie_id=None, entite="kitsu:7", kitsu_id=7)
    corps = d.reponse_nominale()
    rendre_sur(monkeypatch, series=[*corps["series"][:2], sans_identifiant])

    page = soupe(poser(navigateur, coche=True))

    lignes = page.select("#trace ~ table tbody tr")
    assert [plat(td) for td in lignes[2].select("td")] == [
        "3",
        "Œuvre factice K",
        "0,33",
        "non citée",
        "1",
    ]
    assert lignes[2].select_one("a") is None
    assert plat(page.select("#trace ~ details")[-1].summary) == (
        "Œuvre factice K : passages non cités (1)"
    )
    assert "Passage factice n° 71." in plat(page)


def test_coche_les_reglages_de_la_recherche_et_le_modele_sont_ceux_rendus(navigateur):
    lues = valeurs(soupe(poser(navigateur, coche=True)))

    assert lues == {
        "Question d'origine": QUESTION,
        "Question reformulée": "sans reformulation",
        "Partie dans la recherche": "la question d'origine",
        "nombre_series": "5",
        "passages_par_serie": "2",
        "catalogue_seul": "oui",
        "Modèle d'embedding": "modele-embedding-factice",
        "Révision": "00000000",
        "corpus_id": "v2",
        "encodage_id": "3",
        "Profil": "mistral-large-2512",
        "Fournisseur": "mistral",
        "Modèle": "modele-de-mistral-large-2512",
        "Température": "0",
        "Longueur": "400",
        "Coût estimé": "0,000160 $",
        "Latence de la recherche": "100 ms",
        "Latence de la reformulation": "—",
        "Latence du modèle": "1000 ms",
        "Latence totale": "1200 ms",
        "Identifiant de requête": "api-factice-0001",
    }


def test_coche_les_jetons_sont_donnes_par_appel(navigateur, doublure):
    doublure.choisir(reponse="reformulee")

    page = soupe(poser(navigateur, coche=True))
    appels = [plat(li) for li in page.select("#trace ~ ul li")]

    assert appels == [
        "reformulation : 100 jetons en entrée, 50 en sortie, 0 de raisonnement ;"
        " 1000 ms",
        "réponse : 100 jetons en entrée, 50 en sortie, 0 de raisonnement ; 1000 ms",
    ]


def test_coche_le_bloc_details_techniques_est_absorbe(navigateur, doublure):
    doublure.choisir(reponse="texte_piege")

    page = soupe(poser(navigateur, coche=True))

    assert "Détails techniques" not in page.get_text()
    # Ce qu'il disait reste dit, dans la section.
    assert "Marques de séries sans carte : 999, 103" in plat(page)


@pytest.mark.parametrize(
    "scenario, reformulee, partie",
    [
        (
            "reformulee",
            "Question factice reformulée ?",
            "la question reformulée",
        ),
        (
            "reformulation_ignoree",
            "reformulation ignorée : trop longue",
            "la question d'origine",
        ),
    ],
)
def test_coche_la_question_dit_laquelle_a_cherche(
    navigateur, doublure, scenario, reformulee, partie
):
    doublure.choisir(reponse=scenario)

    lues = valeurs(soupe(poser(navigateur, coche=True)))

    assert lues["Question d'origine"] == QUESTION
    assert lues["Question reformulée"] == reformulee
    assert lues["Partie dans la recherche"] == partie


def test_coche_catalogue_seul_a_faux_se_dit(navigateur, doublure, monkeypatch):
    monkeypatch.setitem(
        d.SCENARIOS_REPONSE,
        "nominale",
        lambda: d.Rendu(
            200,
            d.reponse_nominale(
                contexte=d.contexte(
                    reglages={
                        "nombre_series": 5,
                        "passages_par_serie": 2,
                        "catalogue_seul": False,
                    }
                )
            ),
        ),
    )

    assert valeurs(soupe(poser(navigateur, coche=True)))["catalogue_seul"] == "non"


# --------------------------------------------------------------------------- #
#  Portage par le formulaire
# --------------------------------------------------------------------------- #
def test_coche_le_reglage_est_conserve_au_tour_suivant(navigateur, doublure):
    premier = poser(navigateur, coche=True)
    assert case_cochee(soupe(premier))
    historique = soupe(premier).select_one("input[name=historique]")["value"]

    second = poser(navigateur, "Et ensuite ?", coche=True, historique=historique)

    assert case_cochee(soupe(second))
    assert soupe(second).select_one("h2#trace") is not None
    assert len(doublure.corps_recus[1]["historique"]) == 2


def test_nouvelle_conversation_remet_le_reglage_au_defaut(navigateur):
    assert case_cochee(soupe(poser(navigateur, coche=True)))

    page = soupe(navigateur.get("/"))

    assert not case_cochee(page)
    assert page.select_one("#trace") is None


def test_decocher_au_tour_suivant_retire_la_section(navigateur):
    premier = poser(navigateur, coche=True)
    historique = soupe(premier).select_one("input[name=historique]")["value"]

    second = soupe(poser(navigateur, "Et ensuite ?", historique=historique))

    assert not case_cochee(second)
    assert second.select_one("#trace") is None


@pytest.mark.parametrize(
    "historique", ["{cassé", echanges(5)], ids=["invalide", "pleine"]
)
def test_une_conversation_finie_d_avance_revient_au_defaut(
    navigateur, doublure, historique
):
    """Rien n'est envoyé, et la suite est une nouvelle conversation : comme les autres
    réglages, la case revient à son défaut."""
    page = soupe(poser(navigateur, coche=True, historique=historique))

    assert not case_cochee(page)
    assert page.select_one("#trace") is None
    assert doublure.corps_recus == []


def test_un_cas_degrade_garde_la_case_sans_inventer_de_section(navigateur, doublure):
    doublure.choisir(reponse="base_indisponible")

    r = poser(navigateur, coche=True)
    page = soupe(r)

    assert r.status_code == 503
    assert case_cochee(page)
    assert page.select_one("#trace") is None


def test_une_erreur_de_champ_garde_la_case(navigateur, doublure):
    r = poser(navigateur, "", coche=True)

    assert r.status_code == 422
    assert case_cochee(soupe(r))
    assert doublure.corps_recus == []


def test_le_defaut_vient_de_la_configuration_et_le_libraire_garde_la_main(
    doublure, reglages
):
    allume = replace(reglages, mode_demonstration_defaut=True)
    application, _ = application_sur(doublure, allume)

    with TestClient(application, client=("127.0.0.1", 50000)) as client:
        assert case_cochee(soupe(client.get("/")))
        page = soupe(poser(client))

    assert not case_cochee(page)
    assert page.select_one("#trace") is None


def test_un_defaut_qui_n_est_pas_un_booleen_empeche_de_demarrer(tmp_path: Path):
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    assert texte.count("mode_demonstration_defaut = false") == 1
    fichier = tmp_path / "reglages.toml"
    fichier.write_text(
        texte.replace(
            "mode_demonstration_defaut = false", 'mode_demonstration_defaut = "non"'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationInvalide) as refus:
        charger(ENVIRONNEMENT, fichier)

    assert "reglages.mode_demonstration_defaut" in str(refus.value)


# --------------------------------------------------------------------------- #
#  Cohérent avec le refus
# --------------------------------------------------------------------------- #
def test_refus_aucun_passage_coche_classement_vide_et_mention(navigateur, doublure):
    doublure.choisir(reponse="refus_aucun_passage")

    page = soupe(poser(navigateur, coche=True))

    assert page.select_one("h2#trace") is not None
    assert not page.select("#trace ~ table")
    assert "aucune série retrouvée" in plat(page).lower()
    assert "Aucun passage non cité." in plat(page)
    assert "aucun appel au modèle" in [plat(li) for li in page.select("#trace ~ ul li")]


def test_refus_avec_des_series_le_classement_est_affiche_aucune_citee(
    navigateur, doublure
):
    doublure.choisir(reponse="refus_inconnu")

    page = soupe(poser(navigateur, coche=True))

    lignes = lignes_du_classement(page)
    assert [ligne[3] for ligne in lignes] == ["non citée"]
    assert not page.select("#trace ~ table a")
    assert "aucune série retrouvée" not in plat(page).lower()
    assert "Passage factice n° 31." in plat(page)


# --------------------------------------------------------------------------- #
#  Robustesse, sécurité
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scenario", ["scores_nuls", "scores_absents"])
def test_des_scores_nuls_ou_absents_se_lisent_tiret(navigateur, doublure, scenario):
    doublure.choisir(reponse=scenario)

    r = poser(navigateur, coche=True)
    page = soupe(r)

    assert r.status_code == 200
    assert page.select_one("[role=alert]") is None
    carte = page.select_one("#serie-101").find_parent("article")
    assert "rang 1 — score —" in plat(carte)
    assert plat(carte.select_one("blockquote footer")).endswith("— score —")
    assert [ligne[2] for ligne in lignes_du_classement(page)] == ["—"] * 5
    groupes = page.select("#trace ~ details")
    assert plat(groupes[0].select_one("blockquote footer")).endswith("— score —")


@pytest.mark.parametrize("scenario", ["scores_nuls", "scores_absents"])
def test_decoche_des_scores_nuls_ou_absents_ne_changent_rien(
    navigateur, doublure, scenario
):
    doublure.choisir(reponse=scenario)

    r = poser(navigateur)

    assert r.status_code == 200
    assert soupe(r).select_one("#trace") is None
    assert [h3["id"] for h3 in soupe(r).select("h3")] == ["serie-101", "serie-102"]


def test_un_contexte_sans_les_champs_de_la_trace_se_lit_tiret(navigateur, doublure):
    doublure.choisir(reponse="contexte_minimal")

    r = poser(navigateur, coche=True)
    page = soupe(r)
    lues = valeurs(page)

    assert r.status_code == 200
    for nom in (
        "nombre_series",
        "passages_par_serie",
        "catalogue_seul",
        "Modèle d'embedding",
        "Révision",
        "Température",
        "Longueur",
        "Coût estimé",
        "Latence de la recherche",
        "Latence du modèle",
    ):
        assert lues[nom] == "—", nom
    assert lues["Latence totale"] == "1200 ms"
    assert [ligne[0] for ligne in lignes_du_classement(page)] == ["—"] * 5


def test_la_production_ne_depend_pas_des_champs_de_la_trace(navigateur, doublure):
    doublure.choisir(reponse="contexte_minimal")

    r = poser(navigateur)

    assert r.status_code == 200
    assert [h3["id"] for h3 in soupe(r).select("h3")] == ["serie-101", "serie-102"]


def test_un_extrait_ou_un_titre_non_cite_pieges_sont_echappes(navigateur, doublure):
    doublure.choisir(reponse="non_citee_piegee")

    r = poser(navigateur, coche=True)
    page = soupe(r)

    assert not page.select("script")
    assert not page.select("#trace ~ details b, #trace ~ table b")
    assert "&lt;script&gt;alert(2)&lt;/script&gt; Extrait piégé." in r.text
    assert "&lt;b&gt;Titre piégé&lt;/b&gt;" in r.text


def test_decoche_un_extrait_non_cite_piege_n_apparait_pas(navigateur, doublure):
    doublure.choisir(reponse="non_citee_piegee")

    html = poser(navigateur).text

    assert "Extrait piégé" not in html
    assert "Titre piégé" not in html
