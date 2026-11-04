"""L'écran question → réponse, la conversation dans la page, chaque cas dégradé."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import doublure_api as d
import pytest
from bs4 import BeautifulSoup
from conftest import journal_complet

QUESTION = "Un manga factice pour débuter ?"


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def poser(navigateur, question: str = QUESTION, **champs):
    donnees = {
        "question": question,
        "profil": "mistral-large-2512",
        "temperature": "0.0",
        "reformuler": "1",
        "historique": "",
        **champs,
    }
    return navigateur.post("/question", data=donnees)


def champ_cache(reponse) -> str:
    return soupe(reponse).select_one("input[name=historique]")["value"]


def echanges(n: int) -> str:
    tours = []
    for i in range(n):
        tours += [
            {"role": "libraire", "contenu": f"Question {i} ?"},
            {"role": "assistant", "contenu": f"Réponse {i} [S:101]."},
        ]
    return json.dumps(tours)


# --------------------------------------------------------------------------- #
#  La requête envoyée à l'API
# --------------------------------------------------------------------------- #
def test_la_requete_porte_les_seuls_champs_decides(navigateur, doublure):
    poser(navigateur)

    assert doublure.corps_recus == [
        {
            "question": QUESTION,
            "historique": [],
            "reformuler": True,
            "profil": "mistral-large-2512",
            "temperature": 0.0,
            "mode": "strict",
            "identite": "libraire-demo",
        }
    ]
    assert set(doublure.cles_recues) == {d.CLE_ATTENDUE}


def test_les_reglages_sont_transmis(navigateur, doublure):
    navigateur.post(
        "/question",
        data={"question": QUESTION, "profil": "gpt-6-luna", "temperature": "0.7"},
    )

    corps = doublure.corps_recus[0]
    assert corps["profil"] == "gpt-6-luna"
    assert corps["temperature"] == 0.7
    assert corps["reformuler"] is False


def test_sans_temperature_le_defaut_est_envoye(navigateur, doublure):
    navigateur.post("/question", data={"question": QUESTION, "profil": "gpt-6-luna"})

    assert doublure.corps_recus[0]["temperature"] == 0.0


def test_les_espaces_aux_bords_et_les_fins_de_ligne_sont_normalises(
    navigateur, doublure
):
    poser(navigateur, "  une\r\nquestion  ")

    assert doublure.corps_recus[0]["question"] == "une\nquestion"


def test_une_question_a_la_limite_avec_sauts_de_ligne_passe(navigateur, doublure):
    question = ("a\r\n" * 250).strip()

    r = poser(navigateur, question)

    assert r.status_code == 200
    assert len(doublure.corps_recus[0]["question"]) <= 500


@pytest.mark.parametrize("n", [0, 1, 4])
def test_l_historique_est_transmis_tel_quel(navigateur, doublure, n):
    poser(navigateur, historique=echanges(n))

    attendu = json.loads(echanges(n)) if n else []
    assert doublure.corps_recus[0]["historique"] == attendu


def test_un_seul_mode_impose_strict_meme_si_le_formulaire_dit_autre_chose(
    navigateur, doublure
):
    poser(navigateur, mode="enrichi")

    assert doublure.corps_recus[0]["mode"] == "strict"


def test_avec_deux_modes_le_choix_est_retenu_par_le_selecteur(navigateur, doublure):
    doublure.choisir(info="deux_modes", reponse="profil_inconnu")

    r = poser(navigateur, mode="strict")

    choisi = soupe(r).select_one("select#mode option[selected]")
    assert choisi["value"] == "strict"


# --------------------------------------------------------------------------- #
#  La réponse
# --------------------------------------------------------------------------- #
def test_la_reponse_ses_cartes_et_sa_ligne(navigateur):
    r = poser(navigateur)
    page = soupe(r)
    texte = page.get_text()

    assert r.status_code == 200
    assert page.select_one("section[aria-live=polite] h2").get_text() == "Réponse"
    assert QUESTION in texte
    assert [h3["id"] for h3 in page.select("h3")] == ["serie-101", "serie-102"]
    assert [h3.get_text() for h3 in page.select("h3")] == [
        "Série factice A",
        "Série factice B",
    ]
    assert "Passage factice n° 11." in texte
    assert "Passage factice n° 21." in texte and "Passage factice n° 22." in texte
    assert (
        "Les métadonnées viennent du catalogue ; une critique peut être ancienne."
        in texte
    )
    assert "au moins un volume y est paru, pas qu'il est en stock" in texte
    assert (
        "Profil mistral-large-2512 · mistral · modèle modele-de-mistral-large-2512"
        " · appel du 03/11/2026 18:39 UTC · sans reformulation"
        " · coût estimé 0,0002 $ · 1200 ms" in texte
    )


def test_un_passage_non_cite_n_apparait_jamais(navigateur):
    texte = soupe(poser(navigateur)).get_text()

    assert "Passage factice n° 12." not in texte, "non cité, dans une série citée"
    assert "Série factice C" not in texte, "une série non citée"
    assert "Passage factice n° 31." not in texte


def test_les_ancres_du_texte_visent_les_cartes(navigateur):
    page = soupe(poser(navigateur))
    zone = page.select_one("section[aria-live=polite]")
    liens = [a for a in zone.select("a") if a["href"].startswith("#")]

    assert [a["href"] for a in liens] == ["#serie-101", "#serie-102"]
    assert [a.get_text() for a in liens] == ["Série factice A", "Série factice B"]
    assert "[S:" not in zone.get_text()
    for a in liens:
        assert page.select_one(a["href"]) is not None


def test_le_texte_piege_est_echappe_et_les_marques_sans_carte_sont_masquees(
    navigateur, doublure
):
    doublure.choisir(reponse="texte_piege")

    r = poser(navigateur)
    page = soupe(r)
    zone = page.select_one("section[aria-live=polite]")

    assert "<script>" not in r.text
    assert "&lt;script&gt;" in r.text
    assert "[S:" not in zone.get_text()
    assert "une marque inconnue, une série non citée." in zone.get_text()
    assert not zone.select('a[href="#serie-999"]')
    assert not zone.select('a[href="#serie-103"]')
    # L'identifiant reste dans les détails techniques et dans le champ caché.
    assert "sans carte : 999, 103" in zone.select_one("details").get_text()
    historique = page.select_one("input[name=historique]")["value"]
    assert "[S:999]" in historique and "[S:103]" in historique


def test_une_reponse_dont_toutes_les_marques_ont_leur_carte_n_en_dit_rien(
    navigateur, doublure
):
    zone = soupe(poser(navigateur)).select_one("section[aria-live=polite]")

    assert "sans carte" not in zone.select_one("details").get_text()


def test_l_emphase_et_les_titres_ecrits_autrement(navigateur, doublure):
    doublure.choisir(reponse="titre_formes")

    zone = soupe(poser(navigateur)).select_one("section[aria-live=polite]")

    assert "*" not in zone.get_text()
    assert zone.select_one('a[href="#serie-101"] em').get_text() == "Série Factice a !"
    assert zone.select_one('a[href="#serie-102"]').get_text() == "série factice B"
    assert zone.select_one("strong:-soup-contains('un mot appuyé')") is not None


def test_le_champ_cache_garde_le_texte_brut(navigateur, doublure):
    doublure.choisir(reponse="titre_formes")

    tours = json.loads(champ_cache(poser(navigateur)))

    assert tours[1]["contenu"].startswith("*Série Factice a !* [S:101]")


@pytest.mark.parametrize(
    ("scenario", "motif"),
    [
        ("refus_aucun_passage", "Motif : aucun passage du catalogue ne répond."),
        ("refus_hors_catalogue", "Motif : ce titre n'est pas au catalogue."),
        ("refus_inconnu", "Motif : je ne sais pas répondre à partir du catalogue."),
    ],
)
def test_le_refus_est_une_reponse(navigateur, doublure, scenario, motif):
    doublure.choisir(reponse=scenario)

    r = poser(navigateur)
    page = soupe(r)

    assert r.status_code == 200
    assert page.select_one("section[aria-live=polite] h2").get_text() == (
        "Pas de réponse à partir du catalogue"
    )
    assert motif in page.get_text()
    assert not page.select("h3")
    assert not page.select("[role=alert]")
    assert json.loads(champ_cache(r))[1]["contenu"] == "Refus factice."


def test_le_refus_sans_appel_dit_qu_il_n_y_a_pas_eu_d_appel(navigateur, doublure):
    doublure.choisir(reponse="refus_aucun_passage")

    texte = soupe(poser(navigateur)).get_text()

    assert "aucun appel au modèle" in texte
    assert "110 ms" in texte


def test_la_question_reformulee_est_dite(navigateur, doublure):
    doublure.choisir(reponse="reformulee")

    texte = soupe(poser(navigateur)).get_text()

    assert "question reformulée : « Question factice reformulée ? »" in texte
    assert "reformulation :" in texte


def test_la_reformulation_ignoree_est_dite(navigateur, doublure):
    doublure.choisir(reponse="reformulation_ignoree")

    assert "reformulation ignorée : trop longue" in soupe(poser(navigateur)).get_text()


def test_un_cout_inconnu_est_dit(navigateur, doublure):
    doublure.choisir(reponse="cout_inconnu")

    texte = soupe(poser(navigateur)).get_text()

    assert "coût non estimé" in texte and "$" not in texte


def test_les_details_techniques_sont_replies(navigateur):
    page = soupe(poser(navigateur))
    details = page.select("section[aria-live=polite] details")

    assert len(details) == 1 and not details[0].has_attr("open")
    texte = details[0].get_text()
    assert (
        "réponse : 100 jetons en entrée, 50 en sortie, 0 de raisonnement ; 1000 ms"
        in texte
    )
    assert "Corpus v2 · encodage 3 · identifiant api-factice-0001" in texte


def test_un_refus_de_debit_rejoue_et_des_jetons_estimes_se_disent(navigateur, doublure):
    doublure.choisir(reponse="refus_debit_rejoue")

    texte = soupe(poser(navigateur)).get_text()

    assert "réponse : refusé pour débit, non facturé, rejoué une fois" in texte
    assert "(jetons estimés)" in texte


def test_une_carte_sans_passage_le_dit(navigateur, doublure):
    doublure.choisir(reponse="sans_fragment")

    assert "Aucun passage cité pour cette série." in soupe(poser(navigateur)).get_text()


def test_le_bloc_non_verifie_n_apparait_qu_avec_un_complement(navigateur, doublure):
    assert "Non vérifié" not in soupe(poser(navigateur)).get_text()

    doublure.choisir(reponse="complement")
    texte = soupe(poser(navigateur)).get_text()

    assert "Non vérifié — connaissances du modèle, sans source" in texte
    assert "Complément factice." in texte
    assert "Titre factice hors catalogue" in texte


def test_un_complement_sans_titres(navigateur, doublure, monkeypatch):
    monkeypatch.setitem(
        d.SCENARIOS_REPONSE,
        "complement",
        lambda: d.Rendu(200, d.reponse_nominale(complement="Seul.")),
    )
    doublure.choisir(reponse="complement")

    assert "Titres évoqués" not in soupe(poser(navigateur)).get_text()


def test_le_focus_va_au_titre_de_la_reponse_et_une_seule_fois(navigateur):
    page = soupe(poser(navigateur))

    assert [e.name for e in page.select("[autofocus]")] == ["h2"]
    assert page.select_one("h2[autofocus]")["tabindex"] == "-1"


# --------------------------------------------------------------------------- #
#  La conversation
# --------------------------------------------------------------------------- #
def test_le_nouvel_historique_part_dans_le_champ_cache_et_la_question_se_vide(
    navigateur,
):
    r = poser(navigateur)

    tours = json.loads(champ_cache(r))
    assert [t["role"] for t in tours] == ["libraire", "assistant"]
    assert tours[0]["contenu"] == QUESTION
    assert tours[1]["contenu"].startswith("Série factice A [S:101]")
    assert soupe(r).select_one("textarea#question").get_text() == ""


def test_les_reglages_restent_choisis_d_un_tour_a_l_autre(navigateur):
    r = navigateur.post(
        "/question",
        data={"question": QUESTION, "profil": "gpt-6-luna", "temperature": "0.3"},
    )
    page = soupe(r)

    assert page.select_one("select#profil option[selected]")["value"] == "gpt-6-luna"
    assert page.select_one("input[name=temperature][checked]")["value"] == "0.3"
    assert not page.select_one("#reformuler").has_attr("checked")


def test_les_tours_precedents_s_affichent_sans_leurs_marques(navigateur):
    page = soupe(poser(navigateur, historique=echanges(1)))
    tours = page.select("ol li")

    assert [li.get_text() for li in tours] == [
        "Libraire : Question 0 ?",
        "Assistant : Réponse 0.",
    ]


def test_la_premiere_visite_n_a_pas_de_conversation(navigateur):
    assert not soupe(navigateur.get("/")).select("ol")


def test_la_cinquieme_reponse_clot_la_conversation(navigateur):
    r = poser(navigateur, historique=echanges(4))
    page = soupe(r)

    assert r.status_code == 200
    assert "Conversation terminée (5 échanges)." in page.get_text()
    assert page.select_one("[role=alert] a[href='/']") is not None
    assert champ_cache(r) == ""
    assert page.select_one("h3") is not None, "la réponse reste affichée"


def test_la_quatrieme_reponse_ne_clot_pas(navigateur):
    r = poser(navigateur, historique=echanges(3))

    assert "Conversation terminée" not in soupe(r).get_text()
    assert len(json.loads(champ_cache(r))) == 8


def test_dix_tours_en_entree_rien_n_est_envoye(navigateur, doublure):
    r = poser(navigateur, historique=echanges(5))

    assert doublure.corps_recus == []
    assert "Conversation terminée (5 échanges)." in soupe(r).get_text()
    assert champ_cache(r) == ""


@pytest.mark.parametrize(
    "historique",
    [
        "{cassé",
        json.dumps([{"role": "assistant", "contenu": "x"}]),
        json.dumps(json.loads(echanges(5)) + [{"role": "libraire", "contenu": "x"}]),
        json.dumps(
            [
                {"role": "libraire", "contenu": "y" * 2000},
                {"role": "assistant", "contenu": "y" * 1999},
                {"role": "libraire", "contenu": "y"},
                {"role": "assistant", "contenu": "y"},
            ]
        ),
    ],
    ids=["json_casse", "premier_assistant", "onze_tours", "4001_caracteres"],
)
def test_un_historique_invalide_n_envoie_rien_et_le_dit(
    navigateur, doublure, historique
):
    r = poser(navigateur, historique=historique)
    page = soupe(r)

    assert r.status_code == 422
    assert doublure.corps_recus == []
    assert "Cette conversation n'est plus valide." in page.get_text()
    assert champ_cache(r) == ""
    assert page.select_one("textarea#question").get_text() == ""


def test_une_conversation_trop_longue_est_close_la_reponse_affichee(
    navigateur, doublure, monkeypatch
):
    """Le nouvel historique ne tiendrait plus : close, jamais coupée en silence."""
    longue = "Série factice A [S:101] " + "x" * 2100
    monkeypatch.setitem(
        d.SCENARIOS_REPONSE,
        "nominale",
        lambda: d.Rendu(200, d.reponse_nominale(reponse=longue)),
    )

    r = poser(navigateur)
    page = soupe(r)

    assert r.status_code == 200
    assert "Conversation terminée (longueur maximale atteinte)." in page.get_text()
    assert champ_cache(r) == ""
    assert page.select_one("h3") is not None


def test_un_cumul_de_plus_de_4000_caracteres_clot_aussi(navigateur):
    tours = [
        {"role": "libraire", "contenu": "y" * 1990},
        {"role": "assistant", "contenu": "y" * 1990},
    ]

    r = poser(navigateur, historique=json.dumps(tours))

    assert "longueur maximale atteinte" in soupe(r).get_text()
    assert champ_cache(r) == ""


# --------------------------------------------------------------------------- #
#  Erreurs de formulaire
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("question", "message"),
    [
        ("", "Posez une question."),
        ("   ", "Posez une question."),
        ("x" * 501, "La question dépasse 500 caractères (501 saisis)."),
    ],
    ids=["vide", "blanche", "501_caracteres"],
)
def test_une_question_fautive_est_dite_a_cote_du_champ(
    navigateur, doublure, question, message
):
    historique = echanges(1)

    r = poser(navigateur, question, historique=historique)
    page = soupe(r)
    champ = page.select_one("textarea#question")

    assert r.status_code == 422
    assert doublure.corps_recus == []
    assert page.select_one("#question-erreur").get_text() == message
    assert "question-erreur" in champ["aria-describedby"]
    assert champ["aria-invalid"] == "true" and champ.has_attr("autofocus")
    assert json.loads(champ_cache(r)) == json.loads(historique)


def test_une_question_longue_garde_sa_valeur(navigateur):
    page = soupe(poser(navigateur, "x" * 501))

    assert page.select_one("textarea#question").get_text() == "x" * 501


def test_sans_champ_de_profil_le_defaut_part_jamais_un_profil_exclu(
    navigateur, doublure
):
    doublure.choisir(info="large_4_actif")

    r = poser(navigateur, profil="")

    assert r.status_code == 200
    assert doublure.corps_recus[0]["profil"] == "mistral-large-2512"


def test_sans_profil_proposable_rien_ne_part(navigateur, doublure):
    doublure.choisir(info="aucun_profil")

    r = poser(navigateur, profil="")

    assert r.status_code == 422
    assert doublure.corps_recus == []


@pytest.mark.parametrize("profil", ["mistral-large-4", "profil-inconnu"])
def test_un_profil_hors_liste_est_refuse(navigateur, doublure, profil):
    r = poser(navigateur, profil=profil)
    page = soupe(r)

    assert r.status_code == 422
    assert doublure.corps_recus == [], "mistral-large-4 ne part jamais"
    assert (
        page.select_one("#profil-erreur").get_text() == "Ce profil n'est pas proposé."
    )
    assert page.select_one("select#profil")["aria-describedby"] == "profil-erreur"
    assert page.select_one("details").has_attr("open")


@pytest.mark.parametrize("temperature", ["0.9", "chaud", "nan"])
def test_une_temperature_hors_position_est_refusee(navigateur, doublure, temperature):
    r = poser(navigateur, temperature=temperature)
    page = soupe(r)

    assert r.status_code == 422
    assert doublure.corps_recus == []
    assert page.select_one("#temperature-erreur") is not None
    assert page.select_one("fieldset")["aria-describedby"] == "temperature-erreur"
    assert page.select_one("details").has_attr("open")


def test_un_fichier_a_la_place_d_un_champ_ne_compte_pas(navigateur, doublure):
    r = navigateur.post(
        "/question",
        files={"question": ("q.txt", b"contenu")},
        data={"profil": "mistral-large-2512"},
    )

    assert r.status_code == 422
    assert doublure.corps_recus == []


# --------------------------------------------------------------------------- #
#  Les cas dégradés : la matrice cas × encart × statut
# --------------------------------------------------------------------------- #
CAS = [
    ("debit_depasse", "Trop de demandes. Réessayez dans 12 secondes.", 429, "12"),
    ("debit_sans_entete", "Trop de demandes. Réessayez dans 60 secondes.", 429, "60"),
    (
        "fournisseur_refus_429",
        "Le modèle de langage est saturé. Réessayez dans 7 secondes.",
        503,
        "7",
    ),
    (
        "fournisseur_refus_429_sans_entete",
        "Le modèle de langage est saturé. Réessayez dans 2 secondes.",
        503,
        "2",
    ),
    (
        "fournisseur_injoignable",
        "Le modèle de langage ne répond pas (profil mistral-large-2512).",
        503,
        None,
    ),
    (
        "fournisseur_delai",
        "Le modèle de langage ne répond pas (profil mistral-large-2512).",
        503,
        None,
    ),
    (
        "fournisseur_refus_500",
        "Le modèle de langage ne répond pas (profil mistral-large-2512).",
        503,
        None,
    ),
    (
        "fournisseur_corps_inattendu",
        "Le modèle de langage ne répond pas (profil mistral-large-2512).",
        503,
        None,
    ),
    (
        "fournisseur_longueur",
        "Le modèle de langage n'a pas terminé sa réponse (profil mistral-large-2512).",
        503,
        None,
    ),
    (
        "sortie_schema",
        "refusée par nos contrôles (motif : réponse mal formée)",
        503,
        None,
    ),
    ("sortie_serie_inconnue", "(motif : série absente de la recherche)", 503, None),
    ("sortie_fragment_inconnu", "(motif : passage absent de la recherche)", 503, None),
    ("sortie_refus_incoherent", "(motif : refus incohérent)", 503, None),
    ("sortie_pseudonyme", "(motif : nom de personne)", 503, None),
    (
        "sortie_disponibilite",
        "(motif : disponibilité contredite par le catalogue)",
        503,
        None,
    ),
    ("sortie_demographie", "(motif : public contredit par le catalogue)", 503, None),
    ("service_embedding_indisponible", "La recherche est indisponible.", 503, None),
    ("service_embedding_incoherent", "La recherche est indisponible.", 503, None),
    ("base_indisponible", "La recherche est indisponible.", 503, None),
    ("encodage_en_service_change", "La recherche est indisponible.", 503, None),
    ("profil_indisponible", "Le profil gpt-6-sol n'est pas disponible.", 422, None),
    ("profil_inconnu", "Le profil gpt-6-sol n'est pas disponible.", 422, None),
    (
        "reglage_non_expose",
        "Le profil gpt-6-luna n'accepte pas le réglage temperature.",
        422,
        None,
    ),
    ("entree_invalide", "Erreur du service (identifiant api-factice-0002).", 422, None),
    (
        "question_trop_longue",
        "Erreur du service (identifiant api-factice-0002).",
        422,
        None,
    ),
    (
        "historique_invalide",
        "Erreur du service (identifiant api-factice-0002).",
        422,
        None,
    ),
    ("cle_invalide", "Erreur du service (identifiant api-factice-0002).", 502, None),
    (
        "corps_trop_volumineux",
        "Erreur du service (identifiant api-factice-0002).",
        502,
        None,
    ),
    ("erreur_interne", "Erreur du service (identifiant api-factice-0002).", 502, None),
    (
        "corps_hors_contrat",
        "Erreur du service (identifiant api-factice-entete).",
        502,
        None,
    ),
    (
        "reponse_hors_contrat",
        "Erreur du service (identifiant api-factice-entete).",
        502,
        None,
    ),
]


@pytest.mark.parametrize(
    ("scenario", "texte", "statut", "attente"), CAS, ids=[c[0] for c in CAS]
)
def test_chaque_cas_degrade_a_son_encart(
    navigateur, doublure, scenario, texte, statut, attente
):
    doublure.choisir(reponse=scenario)
    historique = echanges(1)

    r = poser(navigateur, historique=historique)
    page = soupe(r)
    encart = page.select_one("[role=alert]")

    assert r.status_code == statut
    assert encart is not None
    assert texte in encart.get_text()
    assert r.headers.get("Retry-After") == attente
    assert page.select_one("section[aria-live=polite] h3") is None
    # Le bouton reste actif, la question dans son champ, l'historique conservé.
    assert not page.select_one("button[type=submit]").has_attr("disabled")
    assert page.select_one("textarea#question").get_text() == QUESTION
    assert json.loads(champ_cache(r)) == json.loads(historique)
    # Le focus sur le titre de l'encart, décrit par son texte.
    titre = encart.select_one("h2")
    assert titre.has_attr("autofocus") and titre["aria-describedby"] == "encart-texte"
    assert [e.name for e in page.select("[autofocus]")] == ["h2"]
    # La conversation continue : pas de lien vers une nouvelle dans l'encart.
    assert encart.select_one("a") is None


def test_la_matrice_s_ecrit_depuis_les_pages_rendues(navigateur, doublure):
    """La matrice du rapport : produite ici, pas écrite à la main (`MATRICE_CAS`)."""
    lignes = [
        "| Scénario | Statut de la page | Retry-After | Encart |",
        "|---|---|---|---|",
    ]
    for scenario, *_ in CAS:
        doublure.choisir(reponse=scenario)
        r = poser(navigateur)
        encart = soupe(r).select_one("#encart-texte").get_text()
        attente = r.headers.get("Retry-After", "—")
        lignes.append(f"| `{scenario}` | {r.status_code} | {attente} | {encart} |")

    assert len(lignes) == len(CAS) + 2
    destination = os.environ.get("MATRICE_CAS")
    if destination:
        Path(destination).write_text("\n".join(lignes) + "\n", encoding="utf-8")


def test_la_matrice_des_cas_couvre_chaque_code_du_contrat():
    codes = d.codes_du_contrat("/v1/reponse", "post", 422) | d.codes_du_contrat(
        "/v1/reponse", "post", 503
    )
    scenarios = {c[0] for c in CAS}
    noms = {
        "fournisseur_refus": "fournisseur_refus_500",
        "sortie_modele_invalide": "sortie_schema",
    }

    assert {noms.get(code, code) for code in codes} <= scenarios


def test_un_clic_trop_tot_recoit_un_nouveau_refus_et_le_meme_encart(
    navigateur, doublure
):
    doublure.choisir(reponse="debit_depasse")

    premier = poser(navigateur)
    second = navigateur.post(
        "/question",
        data={
            "question": soupe(premier).select_one("textarea#question").get_text(),
            "profil": "mistral-large-2512",
            "historique": champ_cache(premier),
        },
    )

    assert len(doublure.corps_recus) == 2
    assert second.status_code == 429
    assert "Réessayez dans 12 secondes." in soupe(second).get_text()


def test_le_refus_d_un_profil_recharge_la_liste_et_revient_au_defaut(
    navigateur, doublure
):
    doublure.choisir(reponse="profil_indisponible")

    page = soupe(poser(navigateur, profil="gpt-6-sol"))

    assert page.select_one("select#profil option[selected]")["value"] == (
        "mistral-large-2512"
    )


def test_api_injoignable_a_la_question_l_encart_le_dit(demarrer, port_ferme):
    navigateur = demarrer(port_ferme, connexion_s=0.2)

    r = poser(navigateur, profil="")

    assert r.status_code == 503
    assert (
        "Le service de recommandation ne répond pas."
        in soupe(r).select_one("[role=alert]").get_text()
    )
    assert soupe(r).select_one("textarea#question").get_text() == QUESTION


def test_api_coupee_entre_la_liste_et_la_question(navigateur, doublure, monkeypatch):
    async def coupee(_demande):
        from application.client_api import ApiInjoignable

        raise ApiInjoignable

    monkeypatch.setattr(navigateur.app.state.vue.client, "reponse", coupee)

    r = poser(navigateur)

    assert r.status_code == 503
    assert "Vous pouvez réessayer." in soupe(r).get_text()


def test_le_delai_depasse_est_dit(doublure, doublure_sur_port, demarrer):
    navigateur = demarrer(doublure_sur_port, connexion_s=0.3, reponse_s=0.3, info_s=2.0)
    doublure.choisir(reponse="delai")

    r = poser(navigateur)

    assert r.status_code == 503
    assert "délai dépassé" in soupe(r).select_one("[role=alert]").get_text()
    assert 'application_api_erreurs_total{code="api_delai"} 1.0' in (
        navigateur.get("/metrics").text
    )


def test_une_cle_refusee_sans_liste_connue_le_dit_a_la_question(doublure, reglages):
    from conftest import application_sur
    from fastapi.testclient import TestClient

    doublure.choisir(info="cle_invalide")
    application, _ = application_sur(doublure, reglages)

    with TestClient(application, client=("127.0.0.1", 50000)) as navigateur:
        r = poser(navigateur, profil="")

    assert r.status_code == 503
    assert doublure.corps_recus == []
    assert "refuse l'accès" in soupe(r).select_one("[role=alert]").get_text()


# --------------------------------------------------------------------------- #
#  Journal et métriques
# --------------------------------------------------------------------------- #
def lignes_requetes(caplog) -> list[dict]:
    return [
        r.champs
        for r in caplog.records
        if r.getMessage() == "requete" and hasattr(r, "champs")
    ]


def test_le_journal_ne_porte_ni_la_question_ni_l_historique_ni_la_reponse(
    navigateur, caplog
):
    caplog.set_level(logging.DEBUG)

    poser(navigateur, historique=echanges(2))

    journal = journal_complet(caplog)
    assert QUESTION not in journal
    assert "Question 0" not in journal and "Réponse 0" not in journal
    assert "Passage factice" not in journal and "factice A" not in journal
    assert d.CLE_ATTENDUE not in journal
    ligne = [c for c in lignes_requetes(caplog) if c["route"] == "/question"][-1]
    assert ligne["nb_tours_historique"] == 4
    assert ligne["taille_historique"] == sum(
        len(t["contenu"]) for t in json.loads(echanges(2))
    )
    assert ligne["identifiant_api"] == "api-factice-entete"
    assert ligne["identifiant_requete"]
    assert ligne["identifiant_requete"] != ligne["identifiant_api"]


def test_le_journal_porte_le_code_et_l_identifiant_de_l_api_en_erreur(
    navigateur, doublure, caplog
):
    caplog.set_level(logging.DEBUG)
    doublure.choisir(reponse="fournisseur_refus_500")

    poser(navigateur)

    ligne = [c for c in lignes_requetes(caplog) if c["route"] == "/question"][-1]
    assert ligne["code"] == "fournisseur_refus"
    assert ligne["identifiant_api"] == "api-factice-0002"


@pytest.mark.parametrize(("chemin", "code"), [("{cassé", "historique_invalide")])
def test_le_journal_dit_un_historique_invalide(navigateur, caplog, chemin, code):
    caplog.set_level(logging.DEBUG)

    poser(navigateur, historique=chemin)

    assert [c for c in lignes_requetes(caplog) if c["route"] == "/question"][-1][
        "code"
    ] == code


def test_une_sequence_connue_se_lit_dans_les_metriques(navigateur, doublure):
    doublure.choisir(reponse="sortie_pseudonyme")
    poser(navigateur)
    doublure.choisir(reponse="debit_depasse")
    poser(navigateur)
    doublure.choisir(reponse="nominale")
    poser(navigateur)

    sortie = navigateur.get("/metrics").text

    assert 'application_api_erreurs_total{code="sortie_modele_invalide"} 1.0' in sortie
    assert 'application_api_erreurs_total{code="debit_depasse"} 1.0' in sortie
    assert (
        'application_api_appels_total{route_api="/v1/reponse",statut="200"} 1.0'
        in sortie
    )
    assert 'application_requetes_total{route="/question",statut="200"} 1.0' in sortie
    assert 'application_requetes_total{route="/question",statut="429"} 1.0' in sortie
    assert 'application_requetes_total{route="/question",statut="503"} 1.0' in sortie
