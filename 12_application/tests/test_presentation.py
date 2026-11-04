"""La mise en forme de la réponse : marques, titres, emphase, cartes, encarts."""

from __future__ import annotations

import doublure_api as d
import pytest

from application import presentation as p
from application.client_api import ErreurApi, Reponse
from application.presentation import Segment

TITRES = {101: "Série factice A", 102: "Série factice B"}


def lien(cible: int, *enfants: Segment) -> Segment:
    return Segment("lien", cible=cible, enfants=enfants)


def test_le_titre_exact_avant_la_marque_est_enveloppe():
    assert p.segments_de("Série factice A [S:101] convient.", TITRES) == [
        lien(101, Segment("texte", "Série factice A")),
        Segment("texte", " convient."),
    ]


def test_le_titre_en_emphase_et_en_autre_casse_est_enveloppe_tel_qu_ecrit():
    assert p.segments_de("Voici *Série Factice a !* [S:101].", TITRES) == [
        Segment("texte", "Voici "),
        lien(101, Segment("em", "Série Factice a !")),
        Segment("texte", "."),
    ]


def test_le_titre_apres_la_marque_est_enveloppe():
    assert p.segments_de("[S:102] « série factice B », bon.", TITRES) == [
        Segment("texte", " « "),
        lien(102, Segment("texte", "série factice B")),
        Segment("texte", " », bon."),
    ]


def test_le_titre_en_emphase_apres_la_marque_garde_son_emphase():
    assert p.segments_de("[S:102] *Série factice B* suit.", TITRES)[1] == lien(
        102, Segment("em", "Série factice B")
    )


def test_l_emphase_apres_la_marque_se_ferme_apres_la_ponctuation_du_titre():
    assert p.segments_de("[S:102] *Série factice B !* suit.", TITRES)[-2:] == [
        lien(102, Segment("em", "Série factice B !")),
        Segment("texte", " suit."),
    ]


def test_une_marque_redite_juste_apres_son_titre_ne_le_double_pas():
    segments = p.segments_de("[S:101] Série factice A [S:101] convient.", TITRES)

    assert [s.genre for s in segments].count("lien") == 1
    assert segments[-1] == Segment("texte", " convient.")


def test_sans_titre_autour_le_lien_porte_le_titre_du_catalogue():
    assert p.segments_de("Lisez [S:101] sans attendre.", TITRES) == [
        Segment("texte", "Lisez "),
        lien(101, Segment("texte", "Série factice A")),
        Segment("texte", " sans attendre."),
    ]


def test_une_marque_sans_carte_reste_du_texte():
    assert p.segments_de("Une marque [S:999].", TITRES) == [
        Segment("texte", "Une marque [S:999]."),
    ]


def test_deux_marques_de_suite_ne_mordent_pas_l_une_sur_l_autre():
    segments = p.segments_de(
        "Série factice A [S:101] et Série factice B [S:102].", TITRES
    )

    assert [s.cible for s in segments if s.genre == "lien"] == [101, 102]
    assert segments[1] == Segment("texte", " et ")


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("un *mot* ici", ["texte", "em", "texte"]),
        ("un **mot** ici", ["texte", "strong", "texte"]),
        ("*seul", ["texte"]),
        ("2 * 3 * 4", ["texte"]),
        ("4* et 5*", ["texte"]),
        ("", []),
    ],
)
def test_l_emphase(texte, attendu):
    assert [s.genre for s in p.avec_emphase(texte)] == attendu


def test_les_paragraphes_se_coupent_aux_lignes_vides():
    assert len(p.paragraphes_de("Un.\n\nDeux.\n \nTrois.\n", {})) == 3


def test_un_tour_precedent_ne_montre_pas_ses_marques():
    assert p.segments_d_un_tour("Série factice A [S:101] *bien*.") == [
        Segment("texte", "Série factice A "),
        Segment("em", "bien"),
        Segment("texte", "."),
    ]


def test_debut_et_fin_du_titre_absents():
    assert p.debut_du_titre("Rien ici ", p.plat("Série factice A")) is None
    assert p.debut_du_titre("", "") is None
    assert p.fin_du_titre(" rien ici", p.plat("Série factice A")) is None


def test_date_lisible():
    assert p.date_lisible("2026-11-03T18:39:00+00:00") == "03/11/2026 18:39 UTC"
    assert p.date_lisible("pas une date") == "pas une date"


def corps_de(scenario: str) -> Reponse:
    return Reponse.model_validate(d.SCENARIOS_REPONSE[scenario]().corps)


def test_les_cartes_ne_portent_que_les_series_et_les_passages_cites(reglages):
    cartes = p.cartes_de(corps_de("nominale"), reglages.textes)

    assert [c.serie_id for c in cartes] == [101, 102]
    assert [x.texte for x in cartes[0].passages] == ["Passage factice n° 11."]
    assert [x.genre for x in cartes[1].passages] == [
        "résumé de série",
        "critique de tome",
    ]


def test_une_serie_citee_deux_fois_fait_une_seule_carte(reglages):
    corps = corps_de("nominale")
    double = corps.series_citees[0].model_copy(update={"fragment_ids": [12]})
    corps.series_citees = [corps.series_citees[0], double]

    cartes = p.cartes_de(corps, reglages.textes)

    assert [c.serie_id for c in cartes] == [101]
    assert {x.texte for x in cartes[0].passages} == {
        "Passage factice n° 11.",
        "Passage factice n° 12.",
    }


def test_une_serie_citee_absente_de_series_n_a_pas_de_carte(reglages):
    corps = corps_de("nominale")
    corps.series = corps.series[1:]

    assert [c.serie_id for c in p.cartes_de(corps, reglages.textes)] == [102]


def test_les_metadonnees_nulles_se_disent_inconnu(reglages):
    carte = p.cartes_de(corps_de("metadonnees_nulles"), reglages.textes)[0]

    assert [valeur for _, valeur in carte.metadonnees] == ["inconnu"] * 5


def test_les_tomes_se_lisent_au_moins_hors_du_champ(reglages):
    cartes = p.cartes_de(corps_de("nominale"), reglages.textes)

    assert dict(cartes[0].metadonnees)["Tomes"] == "12"
    assert dict(cartes[1].metadonnees)["Tomes"] == "au moins 3"
    assert dict(cartes[1].metadonnees)["Statut"] == "en cours"
    assert dict(cartes[1].metadonnees)["Disponibilité"] == "seulement au Japon"
    assert dict(cartes[0].metadonnees)["Auteurs"] == "Auteur factice"


@pytest.mark.parametrize(
    ("erreur", "texte", "statut", "attente"),
    [
        (
            ErreurApi(429, "debit_depasse", "m", {}, "i", 12),
            "Trop de demandes. Réessayez dans 12 secondes.",
            429,
            12,
        ),
        (
            ErreurApi(429, "debit_depasse", "m", {}, "i"),
            "Trop de demandes. Réessayez dans 60 secondes.",
            429,
            60,
        ),
        (
            ErreurApi(503, "fournisseur_refus", "m", {"statut": 429}, "i", 7),
            "Le modèle de langage est saturé. Réessayez dans 7 secondes.",
            503,
            7,
        ),
        (
            ErreurApi(503, "fournisseur_refus", "m", {"statut": 429}, "i"),
            "Le modèle de langage est saturé. Réessayez dans 2 secondes.",
            503,
            2,
        ),
    ],
)
def test_les_encarts_d_attente(reglages, erreur, texte, statut, attente):
    encart = p.encart_erreur(erreur, "profil-x", "page", reglages)

    assert (encart.texte, encart.statut, encart.retry_after) == (texte, statut, attente)


def test_un_motif_de_sortie_inconnu_ne_passe_pas_tel_quel(reglages):
    erreur = ErreurApi(503, "sortie_modele_invalide", "m", {"motif": "<nouveau>"}, "i")

    encart = p.encart_erreur(erreur, "profil-x", "page", reglages)

    assert "<nouveau>" not in encart.texte
    assert "motif : inconnu" in encart.texte


def test_sans_identifiant_de_l_api_l_encart_dit_celui_de_la_page(reglages):
    erreur = ErreurApi(502, "corps_inattendu", "m")

    encart = p.encart_erreur(erreur, "profil-x", "id-page", reglages)

    assert "id-page" in encart.texte and encart.statut == 502
