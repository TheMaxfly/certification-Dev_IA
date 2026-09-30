"""Contrôles du §8, et le marqueur « synopsis anglais seulement » (2026-09-30)."""

from __future__ import annotations

import json
from collections import Counter

import psycopg
import pytest

from evaluation.atteignabilite import synopsis_seul
from evaluation.confirmer import Bilan
from evaluation.controles import controler, mots_significatifs
from evaluation.jeu import (
    COLONNES_ATTENDUS,
    COLONNES_QUESTIONS,
    COLONNES_SOURCE,
    Question,
)


def test_un_mot_frequent_ou_liste_ne_compte_pas():
    frequence = Counter({"fin": 40, "polar": 1, "genre": 1})
    assert mots_significatifs(
        "Un polar, genre sans fin : l'enquête", {"genre"}, frequence, 100, 0.05
    ) == ["un", "polar", "sans", "enquete"]


def test_le_seuil_est_inclusif():
    """Porté par exactement 5 % des critiques : encore significatif."""
    assert mots_significatifs("pile", set(), Counter({"pile": 5}), 100, 0.05) == [
        "pile"
    ]
    assert mots_significatifs("pile", set(), Counter({"pile": 6}), 100, 0.05) == []


def test_le_marqueur_n_est_jamais_une_colonne_du_jeu():
    """Calculé à la mesure depuis le corpus, jamais écrit dans le CSV."""
    for colonnes in (COLONNES_SOURCE, COLONNES_QUESTIONS, COLONNES_ATTENDUS):
        assert not any("synopsis" in c or "atteign" in c for c in colonnes)


def _corpus(dsn):
    """Série 1 : critiques ; série 2 : synopsis Kitsu seul ; série 3 : rien.
    Vingt critiques de remplissage portent « un » et « commun » (> 5 %)."""
    with psycopg.connect(dsn, autocommit=True) as cx:
        for sid, titre in ((1, "Une"), (2, "Deux"), (3, "Trois")):
            cx.execute(
                "INSERT INTO manga.ms_series_enriched (series_id, series_title) "
                "VALUES (%s, %s)",
                (sid, titre),
            )
        cx.execute(
            "INSERT INTO manga.work_identity (series_id, kitsu_id) VALUES (2, '20')"
        )
        docs = [("r1", "ms_review", 1, None, "un polar commun")]
        docs += [(f"x{i}", "ms_review", 3 + i, None, "un commun") for i in range(20)]
        docs.append(("k20", "kitsu_synopsis", None, 20, "an English synopsis"))
        for cle, source, sid, kid, texte in docs:
            cx.execute(
                "INSERT INTO bench.corpus_docs (doc_key, source, series_id, kitsu_id, "
                "doc_text) VALUES (%s, %s, %s, %s, %s)",
                (cle, source, sid, kid, texte),
            )
            cx.execute(
                "INSERT INTO bench.corpus_chunks (doc_key, chunk_index, chunk_text) "
                "VALUES (%s, 0, %s)",
                (cle, texte),
            )


def _jeu(tmp_path, minimum=1):
    (tmp_path / "mots_non_significatifs_v1.txt").write_text("# liste\ngenre\n")
    (tmp_path / "controles_v1.json").write_text(
        json.dumps(
            {
                "recouvrement_f3": {
                    "seuil_frequence_critiques": 0.05,
                    "liste_mots_non_significatifs": "mots_non_significatifs_v1.txt",
                },
                "effectifs": {
                    "minimum_par_famille": minimum,
                    "minimum_par_mode_hors_refus": 0,
                    "minimum_refus": 0,
                },
            }
        )
    )
    return tmp_path


def _bilan(qid, texte, series, famille="F3"):
    q = Question(
        qid, texte, "proposition", famille, "au_catalogue", "nouvelle", "", "n"
    )
    return Bilan(q, "confirmee", {str(s): "2" for s in series})


def test_le_marqueur_distingue_critique_synopsis_et_rien(base):
    _corpus(base)
    with psycopg.connect(base) as cx:
        assert synopsis_seul(cx, [1, 2, 3]) == {2}


def test_controler_signale_un_mot_rare_des_critiques_et_ignore_le_frequent(
    base, tmp_path
):
    _corpus(base)
    bilans = [
        _bilan("Q001", "Un polar commun", [1]),  # « polar » est dans la critique de 1
        _bilan("Q002", "Un truc commun", [1, 2]),  # « commun » : > 5 %, ignoré
    ]
    with psycopg.connect(base) as cx:
        r = controler(cx, bilans, _jeu(tmp_path))
    assert r.recouvrement["Q001"]["recouvrement"] == {"polar": [1]}
    assert r.recouvrement["Q002"]["recouvrement"] == {}
    assert r.recouvrement["Q002"]["synopsis_seul"] == [2]
    assert any("Q001" in e for e in r.echecs)


def test_controler_trouve_une_serie_absente_et_un_effectif_court(base, tmp_path):
    _corpus(base)
    bilans = [_bilan("Q001", "rien", [1, 999], famille="F1")]
    with psycopg.connect(base) as cx:
        r = controler(cx, bilans, _jeu(tmp_path, minimum=1))
    assert r.absentes == [999]
    assert any(e.startswith("F2 :") for e in r.effectifs_en_defaut)


@pytest.mark.parametrize("famille", ["F1", "F11"])
def test_les_effectifs_couvrent_onze_familles(base, tmp_path, famille):
    _corpus(base)
    with psycopg.connect(base) as cx:
        r = controler(
            cx, [_bilan("Q001", "rien", [1], famille=famille)], _jeu(tmp_path)
        )
    assert set(r.effectifs["familles"]) == {f"F{i}" for i in range(1, 12)}
    assert r.effectifs["familles"][famille] == 1
