"""Le gel d'une version du jeu : à blanc intact, réel immuable, même empreinte."""

from __future__ import annotations

import json

import psycopg
import pytest

from evaluation.geler import GelRefuse, geler
from evaluation.jeu import COLONNES_SOURCE


def _jeu(tmp_path):
    jeu = tmp_path / "v1"
    jeu.mkdir()
    ligne = {
        "id": "Q001",
        "texte": "Vous avez Une ?",
        "mode": "reconnaissance",
        "famille": "F1",
        "issue_attendue": "au_catalogue",
        "series_attendues": "Une",
        "grade": "",
        "origine": "decembre:13",
        "note": "titre exact",
        "cle_confirmation": "",
    }
    with (jeu / "jeu_evaluation_recherche.csv").open("w", encoding="utf-8") as fh:
        fh.write(",".join(COLONNES_SOURCE) + "\n")
        fh.write(",".join(f'"{ligne[c]}"' for c in COLONNES_SOURCE) + "\n")
    (jeu / "mots_non_significatifs_v1.txt").write_text("# vide\n")
    (jeu / "controles_v1.json").write_text(
        json.dumps(
            {
                "recouvrement_f3": {
                    "seuil_frequence_critiques": 0.05,
                    "liste_mots_non_significatifs": "mots_non_significatifs_v1.txt",
                },
                "effectifs": {
                    "minimum_par_famille": 0,
                    "minimum_par_mode_hors_refus": 0,
                    "minimum_refus": 0,
                },
            }
        )
    )
    (jeu / "DECLARATIONS.md").write_text("Aucune question écrite après exécution.\n")
    return jeu


@pytest.fixture
def base_une(base):
    with psycopg.connect(base, autocommit=True) as cx:
        cx.execute(
            "INSERT INTO manga.ms_series_enriched (series_id, series_title) "
            "VALUES (1, 'Une')"
        )
    return base


def _jeux(dsn):
    with psycopg.connect(dsn) as cx:
        return cx.execute("SELECT version, empreinte FROM bench.eval_jeux").fetchall()


def test_a_blanc_ni_la_base_ni_le_dossier_ne_bougent(base_une, tmp_path):
    jeu = _jeu(tmp_path)
    avant = sorted(p.name for p in jeu.iterdir())
    r = geler(base_une, jeu, "v1", executer_gel=False)
    assert (r["questions"], r["attendus"]) == (1, 1)
    assert _jeux(base_une) == []
    assert sorted(p.name for p in jeu.iterdir()) == avant


def test_le_gel_reel_projette_et_garde_l_empreinte_de_l_essai(base_une, tmp_path):
    jeu = _jeu(tmp_path)
    essai = geler(base_une, jeu, "v1", executer_gel=False)
    reel = geler(base_une, jeu, "v1", executer_gel=True)
    assert reel["empreinte"] == essai["empreinte"]
    assert _jeux(base_une) == [("v1", reel["empreinte"])]
    assert {"questions.csv", "attendus.csv"} <= {p.name for p in jeu.iterdir()}
    with psycopg.connect(base_une) as cx:
        assert cx.execute(
            "SELECT origine, origine_query_id FROM bench.eval_questions"
        ).fetchone() == ("decembre", 13)
        assert cx.execute(
            "SELECT series_id, grade FROM bench.eval_attendus"
        ).fetchall() == [(1, 2)]


def test_une_version_gelee_ne_se_regele_pas(base_une, tmp_path):
    jeu = _jeu(tmp_path)
    geler(base_une, jeu, "v1", executer_gel=True)
    avant = {p.name: p.read_bytes() for p in jeu.iterdir()}
    with pytest.raises(GelRefuse, match="déjà gelée"):
        geler(base_une, jeu, "v1", executer_gel=True)
    assert {p.name: p.read_bytes() for p in jeu.iterdir()} == avant


def test_pas_de_gel_sans_declaration(base_une, tmp_path):
    jeu = _jeu(tmp_path)
    (jeu / "DECLARATIONS.md").unlink()
    with pytest.raises(GelRefuse, match="déclaration"):
        geler(base_une, jeu, "v1", executer_gel=False)
