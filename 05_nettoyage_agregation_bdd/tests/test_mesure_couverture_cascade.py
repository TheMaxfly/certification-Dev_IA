"""Contrats de la mesure unique du rapport de couverture."""

from __future__ import annotations

import json

import psycopg
import pytest

from identity.mesure_couverture_cascade import (
    ECHANTILLON,
    HISTORIQUE_SECTION_2,
    REQUETE,
    ErreurMesure,
    assembler_mesure,
    mesurer_bdd,
    mesurer_echantillon,
    serialiser,
    verifier_regle_arret,
)


def bdd_reference() -> dict[str, int]:
    return {
        "catalogue_total": 14_670,
        "identites_auto": 8_413,
        "needs_review": 952,
        "orphelines": 5_304,
        "rejected": 1,
        "exact": 111,
        "exact_author": 1_058,
        "exact_kitsu": 3_180,
        "exact_kitsu_author": 1_396,
        "kitsu_bridge_current": 1_688,
        "kitsu_bridge_initial": 1_689,
        "llm_review": 980,
        "trgm_auto": 0,
        "kitsu_propagation": 1_168,
        "human_review_rejected": 1,
        "human_review_rejected_series": [1_428],
        "kitsu_formes_total": 155_003,
        "llm_seau_adjacent": 67,
        "llm_auteur_pseudonyme": 272,
        "llm_autres": 641,
        "pivot_total": 8_214,
        "wiki_ja": 4_755,
        "wiki_en": 3_664,
        "wiki_fr": 1_803,
        "wiki_ja_seul": 1_664,
        "identites_qid": 3_136,
        "identites_kitsu": 7_028,
        "identites_mal": 7_978,
        "identites_anilist": 8_051,
        "genres_couvertes": 12_952,
        "sans_genre_source": 1_694,
        "undecidable": 53,
        "conflits_multi": 54,
        "fusibles": 6,
        "collisions_unicite": 56,
        "same_work_moyenne": 516,
        "graphies_ms_total": 28_274,
        "graphies_ms_romanisees": 28_272,
        "graphies_ms_japonaises": 2,
    }


FORMES_REFERENCE = {
    "series_decidees": 1_058,
    "series_concordantes": 749,
    "series_avec_latine": 749,
    "series_d0_seul": 21,
}


def test_requete_est_un_select_unique_sans_operation_de_structure_ou_ecriture():
    sql = REQUETE.read_text(encoding="utf-8")
    instructions = sql[sql.index("WITH") :].upper()
    assert instructions.count(";") == 1
    for mot in ("CREATE ", "ALTER ", "DROP ", "INSERT ", "UPDATE ", "DELETE "):
        assert mot not in instructions


def test_requete_complete_s_execute_sur_le_schema_migre(base):
    with psycopg.connect(base) as connexion, connexion.cursor() as cur:
        mesure = mesurer_bdd(cur)
    assert mesure["catalogue_total"] == 0
    assert mesure["identites_auto"] == 0
    assert mesure["collisions_unicite"] == 0


def test_une_propagation_reste_comptee_sous_sa_methode_d_identification(base):
    """La décision `kitsu_propagation` (018) devient courante sans identifier :
    la série reste comptée sous `exact_author`, et la propagation a sa ligne.
    La somme de contrôle de la section 2 ne bouge pas (le statut reste auto)."""
    with psycopg.connect(base, autocommit=True) as cx:
        cx.execute("INSERT INTO manga.ms_series_enriched (series_id) VALUES (1), (2)")
        for series_id in (1, 2):
            cx.execute(
                "INSERT INTO manga.match_decision "
                "(series_id, wikidata_qid, method, status) "
                "VALUES (%s, %s, 'exact_author', 'auto')",
                (series_id, f"Q{series_id}"),
            )
        (source,) = cx.execute(
            "SELECT decision_id FROM manga.match_decision WHERE series_id = 1"
        ).fetchone()
        cx.execute(
            "INSERT INTO manga.match_decision "
            "(series_id, wikidata_qid, method, status, details) "
            "VALUES (1, 'Q1', 'kitsu_propagation', 'auto', %s)",
            (f'{{"decision_source": {source}}}',),
        )
    with psycopg.connect(base) as connexion, connexion.cursor() as cur:
        mesure = mesurer_bdd(cur)
    assert mesure["exact_author"] == 2
    assert mesure["kitsu_propagation"] == 1
    assert mesure["kitsu_bridge_current"] == 0
    assert mesure["identites_auto"] == 2


def test_un_arbitrage_valide_derive_reste_compte_sous_sa_source(base):
    """Un arbitrage humain d'entrée Kitsu (`human_review` / `validated`) est une
    décision dérivée : la série reste comptée sous `llm_review` / `auto`, et la
    somme de contrôle de la section 2 — qui ignore `validated` — tient."""
    with psycopg.connect(base, autocommit=True) as cx:
        cx.execute("INSERT INTO manga.ms_series_enriched (series_id) VALUES (1)")
        (source,) = cx.execute(
            "INSERT INTO manga.match_decision "
            "(series_id, wikidata_qid, method, status, details) "
            "VALUES (1, 'Q1', 'llm_review', 'auto', "
            '\'{"case": "promo_llm_same_haute"}\') RETURNING decision_id'
        ).fetchone()
        cx.execute(
            "INSERT INTO manga.match_decision "
            "(series_id, wikidata_qid, method, status, decided_by, details) "
            "VALUES (1, 'Q1', 'human_review', 'validated', 'human', %s)",
            (f'{{"decision_source": {source}, "kitsu_id": 10}}',),
        )
    with psycopg.connect(base) as connexion, connexion.cursor() as cur:
        mesure = mesurer_bdd(cur)
    assert mesure["identites_auto"] == 1
    assert mesure["llm_review"] == 1
    assert mesure["llm_autres"] == 1
    assert mesure["human_review_rejected"] == 0
    assert (
        mesure["identites_auto"]
        + mesure["needs_review"]
        + mesure["orphelines"]
        + mesure["rejected"]
        == mesure["catalogue_total"]
    )


def test_echantillon_versionne_confirme_100_decisions_sur_100():
    assert ECHANTILLON.is_file()
    assert mesurer_echantillon() == {
        "decisions_arbitrees": 100,
        "decisions_confirmees": 100,
        "precision_pct": 100.0,
        "strate_historique": 25,
        "strate_score_bas": 20,
        "strate_pont": 15,
        "strate_standard": 40,
    }


def test_assemblage_restitue_les_chiffres_et_leurs_ecarts():
    mesure = assembler_mesure(bdd_reference(), mesurer_echantillon(), FORMES_REFERENCE)
    assert mesure["section_2"]["somme_controle"] == 14_670
    assert all(
        ligne["ecart"] == 0 for ligne in mesure["section_2_comparaison"].values()
    )
    assert mesure["section_6"]["wiki_ja_pct"] == 57.9
    assert mesure["section_6"]["wiki_ja_seul"] == 1_664
    assert mesure["section_7"]["same_work_moyenne_non_promus"] == 516
    assert mesure["section_38_genres"]["series_couvertes_pct"] == 88.3
    assert mesure["section_38_genres"]["sans_genre_dans_les_sources_pct"] == 11.5
    assert mesure["section_44_formes_auteur"] == {
        "series_exact_author": 1_058,
        "series_concordantes": 749,
        "concordances_non_retrouvees": 309,
        "part_decisions_retrouvees_pct": 70.79,
        "series_avec_forme_latine": 749,
        "part_series_avec_forme_latine_pct": 100.0,
        "series_concordantes_nom_d0_seul": 21,
        "graphies_auteur_ms_total": 28_274,
        "graphies_auteur_ms_romanisees": 28_272,
        "graphies_auteur_ms_romanisees_pct": 99.993,
        "graphies_auteur_ms_japonaises": 2,
        "certification_2026_08_25": {
            "series_decidees": 1_058,
            "series_concordantes": 1_058,
            "series_avec_forme_latine": 1_058,
            "part_forme_latine_pct": 100.0,
            "series_concordantes_nom_d0_seul": 21,
        },
    }


def test_somme_de_controle_incorrecte_declenche_arret():
    section = dict(HISTORIQUE_SECTION_2)
    section["orphelines"] -= 1
    with pytest.raises(ErreurMesure, match="somme de contrôle"):
        verifier_regle_arret(section)


def test_ecart_superieur_a_un_pourcent_declenche_arret():
    section = dict(HISTORIQUE_SECTION_2)
    section["identites_automatiques"] -= 100
    section["orphelines"] += 100
    with pytest.raises(ErreurMesure, match="supérieur au seuil"):
        verifier_regle_arret(section)


def test_serialisation_est_deterministe_et_valide():
    mesure = assembler_mesure(bdd_reference(), mesurer_echantillon(), FORMES_REFERENCE)
    premier = serialiser(mesure)
    second = serialiser(mesure)
    assert premier == second
    assert json.loads(premier) == mesure
