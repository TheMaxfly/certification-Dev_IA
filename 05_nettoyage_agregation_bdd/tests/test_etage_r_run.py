"""Étage R, pilote de run — tests À SEC de la logique pure (ZÉRO réseau, ZÉRO base).

On teste ce qui doit être juste AVANT tout appel payant : signal auteur, écart
d'année, assemblage du dossier (la vérité ne fuit pas au juge), notation et règle
de poursuite, comparaison inter-modèles.
"""

from __future__ import annotations

from identity import etage_r_run as rr


def test_signal_auteur():
    assert rr.calc_signal_auteur({"miura"}, {"miura", "autre"}) == "concordant"
    assert rr.calc_signal_auteur({"miura"}, {"toriyama"}) == "discordant"
    assert rr.calc_signal_auteur(set(), {"miura"}) == "incomparable"
    assert rr.calc_signal_auteur({"miura"}, None) == "incomparable"


def test_ecart_annee():
    assert rr.calc_ecart_annee(1989, 1989) == 0
    assert rr.calc_ecart_annee(2005, 2003) == 2
    assert rr.calc_ecart_annee(None, 2000) is None
    assert rr.calc_ecart_annee(2000, None) is None


def _cas(**s):
    base = {
        "series_id": 1,
        "titre": "Berserk",
        "auteurs": "Kentaro Miura",
        "annee": 1989,
        "qid": "Q1",
        "kitsu_id": "10",
        "attendu": "same_work",
        "fabrication": "identite_sure",
    }
    base.update(s)
    return base


def test_l_assemblage_ne_fait_pas_fuiter_la_verite():
    """MUTATION : si `attendu` entrait dans le texte, le juge lirait la réponse
    et la mesure ne vaudrait rien."""
    serie = {
        "titre": "Berserk",
        "auteurs": "Kentaro Miura",
        "annee": 1989,
        "formes": "Berserk | ベルセルク",
        "synopsis": "Un guerrier...",
        "auteurs_norm": {"kentaro miura"},
    }
    candidat = {
        "type": "qid",
        "id": "Q1",
        "label": "Berserk",
        "annee": 1989,
        "contexte": "manga",
        "formes": "Berserk",
        "auteurs": "Kentaro Miura",
        "auteurs_norm": {"kentaro miura"},
    }
    dossier = rr.assembler_dossier(_cas(), serie, candidat)
    texte = rr.texte_dossier(dossier)

    assert "Berserk" in texte
    assert dossier["candidats"][0]["signal_auteur"] == "concordant"
    assert dossier["candidats"][0]["ecart_annee"] == 0
    # La vérité ne doit apparaître NULLE PART dans ce que lira le juge.
    assert "same_work" not in texte
    assert "attendu" not in texte


def test_notation_et_poursuite_nominale():
    """40 corrects, 15 corrects, 5 undecidable → exactitude hors undecidable
    parfaite, aucun faux confiant → poursuite OUI."""
    res = (
        [{"attendu": "same_work", "verdict": "same_work", "confiance": "haute"}] * 28
        + [
            {
                "attendu": "different_work",
                "verdict": "different_work",
                "confiance": "moyenne",
            }
        ]
        * 27
        + [{"attendu": "same_work", "verdict": "undecidable", "confiance": "moyenne"}]
        * 5
    )
    m = rr.noter(res)
    assert m["undecidable"] == 5
    assert m["juges"] == 55
    assert m["exactitude_hors_undecidable"] == 1.0
    assert m["faux_same_work_haute"] == 0
    assert m["poursuite"] is True


def test_un_seul_faux_same_work_confiant_disqualifie():
    """Le cœur de la règle : un juge qui affirme fort une fausse identité est
    refusé, même si son score global reste très haut."""
    res = (
        [{"attendu": "same_work", "verdict": "same_work", "confiance": "haute"}] * 59
        + [
            {"attendu": "different_work", "verdict": "same_work", "confiance": "haute"}
        ]  # LE faux same_work confiant
    )
    m = rr.noter(res)
    assert m["exactitude_hors_undecidable"] > 0.95  # le score global reste haut
    assert m["faux_same_work_haute"] == 1
    assert m["poursuite"] is False  # et pourtant : refusé


def test_poursuite_refusee_sous_le_seuil_d_exactitude():
    res = [
        {"attendu": "same_work", "verdict": "different_work", "confiance": "moyenne"}
    ] * 5 + [
        {"attendu": "same_work", "verdict": "same_work", "confiance": "moyenne"}
    ] * 55
    m = rr.noter(res)
    assert m["exactitude_hors_undecidable"] < 0.95
    assert m["faux_same_work_haute"] == 0
    assert m["poursuite"] is False


def test_comparaison_compte_les_desaccords_sur_le_verdict_seul():
    """MUTATION : comparer les dicts entiers (avec justification) gonflerait les
    désaccords — deux modèles motivent rarement pareil. Seul le verdict compte."""
    par_modele = {
        "A": {
            "c1": {
                "verdict": {
                    "verdict": "same_work",
                    "confiance": "haute",
                    "justification": "auteur concordant",
                }
            },
            "c2": {
                "verdict": {
                    "verdict": "different_work",
                    "confiance": "moyenne",
                    "justification": "titres proches, auteurs distincts",
                }
            },
        },
        "B": {
            "c1": {
                "verdict": {
                    "verdict": "same_work",
                    "confiance": "moyenne",
                    "justification": "phrase totalement différente",
                }
            },
            "c2": {
                "verdict": {
                    "verdict": "same_work",
                    "confiance": "moyenne",
                    "justification": "autre phrase",
                }
            },
        },
    }
    comp = rr.comparer(par_modele)
    assert comp["communs"] == 2
    assert comp["desaccords"] == ["c2"]  # c1 = même verdict malgré justif. ≠


def test_resolution_des_noms_de_modele():
    assert rr.resoudre_modele("luna") == "gpt-5.6-luna"
    assert rr.resoudre_modele("terra") == "gpt-5.6-terra"
    assert rr.resoudre_modele("gpt-5.6-sol") == "gpt-5.6-sol"  # id complet accepté
