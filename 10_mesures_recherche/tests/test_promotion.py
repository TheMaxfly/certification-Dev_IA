"""La comparaison à la mesure 8 et le verdict de la règle (E3, étape 1, bloc G)."""

from __future__ import annotations

import copy
import json
import uuid

import psycopg
import pytest

from mesures_recherche import promotion
from mesures_recherche.enregistrement import uri_suivi

REGLE = promotion.charger_regle()


def test_la_regle_ecrite_avant_la_mesure():
    r, h, m = REGLE["regle"], REGLE["hypothese"], REGLE["mesure"]
    assert (r["metrique"], r["global_min"], r["reconnaissance_min"]) == (
        "hit_rate@10",
        31,
        20,
    )
    assert "global ≥ 31 **et** reconnaissance ≥ 20" in r["texte"]
    assert "La décision reste celle de Max." in r["texte"]
    assert (h["proposition_min"], h["reconnaissance"], h["global"]) == (
        12,
        [20, 22],
        [31, 36],
    )
    assert "Une hypothèse démentie est un résultat" in h["texte"]
    assert m["reference"] == {"global": 31, "reconnaissance": 21, "proposition": 10}
    assert (m["numero"], m["corpus"], m["experience"]) == (8, "v2", "E3-corpus-v2")


@pytest.mark.parametrize(
    "glob, reco, attendu",
    [
        (31, 20, "promotion proposée"),
        (36, 23, "promotion proposée"),
        (30, 23, "refus proposé"),
        (35, 19, "refus proposé"),
    ],
)
def test_verdict_de_la_regle(glob, reco, attendu):
    assert promotion.verdict({"global": glob, "reconnaissance": reco}, REGLE) == attendu


# --------------------------------------------------------------------------- #
#  De bout en bout : deux runs MLflow, leurs lignes eval_runs, l'encodage en service
# --------------------------------------------------------------------------- #


def question(qid, mode, famille, hit, rang):
    return {
        "question_id": qid,
        "mode": mode,
        "famille": famille,
        "de_rang": True,
        "rang_premiere_attendue": rang,
        "metriques": {"hit_rate@10": float(hit)},
    }


def resultat(questions, ndcg=0.3):
    return {
        "questions": questions,
        "agregats": [
            {
                "portee": "global",
                "perimetre": "toutes",
                "metrique": "ndcg",
                "k": 10,
                "valeur": ndcg,
            }
        ],
    }


def run(stockage, contenu) -> str:
    import mlflow

    mlflow.set_tracking_uri(uri_suivi(stockage))
    # Les pièces sous le stockage du test, comme l'enregistrement les range : sinon
    # MLflow les écrirait dans ./mlruns, au milieu du module.
    if mlflow.get_experiment_by_name("essai") is None:
        mlflow.create_experiment(
            "essai", artifact_location=(stockage / "pieces").as_uri()
        )
    mlflow.set_experiment("essai")
    with mlflow.start_run() as r:
        mlflow.log_text(json.dumps(contenu), "resultat.json")
    return r.info.run_id


@pytest.fixture
def comparaison(banc, tmp_path):
    """Référence : 2 sur 3 (une reconnaissance réussie) ; nouveau : 3 sur 3."""
    dsn, _, _ = banc
    stockage = tmp_path / "mlflow"
    ref = run(
        stockage,
        resultat(
            [
                question("Q1", "proposition", "F1", 0, 30),
                question("Q2", "proposition", "F2", 1, 1),
                question("Q3", "reconnaissance", "F4", 1, 1),
            ]
        ),
    )
    nouveau = run(
        stockage,
        resultat(
            [
                question("Q1", "proposition", "F1", 1, 4),
                question("Q2", "proposition", "F2", 1, 2),
                question("Q3", "reconnaissance", "F4", 1, 1),
            ],
            ndcg=0.4,
        ),
    )
    with psycopg.connect(dsn) as cx:
        (e1,) = cx.execute("SELECT encodage_id FROM bench.encodages").fetchone()
        cx.execute(
            "INSERT INTO bench.corpus (corpus_id, regle, chunking_id) SELECT 'v2',"
            " 'essai', chunking_id FROM bench.chunking_strategies"
            " WHERE name = 'char_1200_overlap_200'"
        )
        e2 = cx.execute(
            "INSERT INTO bench.encodages (corpus_id, modele, revision, dimension,"
            " precision_calcul, prefixe_document, prefixe_requete, outil,"
            " outil_version,"
            " image, image_digest, taille_lot, nb_fragments, termine_le)"
            " SELECT 'v2', modele, revision, dimension, precision_calcul,"
            " prefixe_document, prefixe_requete, outil, outil_version, image,"
            " image_digest, taille_lot, 0, now() FROM bench.encodages"
            " WHERE encodage_id = %s RETURNING encodage_id",
            (e1,),
        ).fetchone()[0]
        for r, c, e in ((ref, "v1", e1), (nouveau, "v2", e2)):
            cx.execute(
                "INSERT INTO bench.eval_runs (run_id, corpus_id, encodage_id)"
                " VALUES (%s, %s, %s)",
                (uuid.UUID(r), c, e),
            )
        cx.execute(
            "INSERT INTO bench.promotions (decision, encodage_id, motif, decide_par)"
            " VALUES ('promu', %s, 'essai', 'essai')",
            (e1,),
        )
    regle = copy.deepcopy(REGLE)
    regle["mesure"] |= {
        "reference_run": ref,
        "reference": {"global": 2, "reconnaissance": 1, "proposition": 1},
    }
    regle["regle"] |= {"global_min": 2, "reconnaissance_min": 1}
    return dsn, stockage, regle, ref, nouveau, e1, e2


def test_la_comparaison_rend_le_verdict_et_les_mouvements(comparaison):
    dsn, stockage, regle, _, nouveau, _, e2 = comparaison
    r = promotion.comparer(dsn, nouveau, regle, stockage)
    assert r["verdict"] == "promotion proposée"
    assert (r["nouveau"]["global"], r["nouveau"]["proposition"]) == (3, 2)
    assert r["mouvements"] == {
        "par_mode": {"proposition": {"gagnees": 1}},
        "par_famille": {"F1": {"gagnees": 1}},
    }
    assert r["run"]["corpus_id"] == "v2" and r["run"]["encodage_id"] == str(e2)
    assert r["nouveau"]["a_50"] == 3 and r["reference"]["a_50"] == 3


def test_aucun_verdict_si_l_encodage_mesure_est_celui_en_service(comparaison):
    dsn, stockage, regle, _, nouveau, e1, e2 = comparaison
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.promotions (decision, encodage_id,"
            " encodage_precedent_id,"
            " motif, decide_par) VALUES ('promu', %s, %s, 'essai', 'essai')",
            (e2, e1),
        )
    with pytest.raises(promotion.VerdictRefuse, match="celui en service"):
        promotion.comparer(dsn, nouveau, regle, stockage)


def test_aucun_verdict_pour_un_run_d_un_autre_corpus(comparaison):
    dsn, stockage, regle, ref, _, _, _ = comparaison
    with pytest.raises(promotion.VerdictRefuse, match="corpus v1"):
        promotion.comparer(dsn, ref, regle, stockage)


def test_aucun_verdict_pour_un_run_sans_ligne_eval_runs(comparaison, tmp_path):
    dsn, stockage, regle, _, _, _, _ = comparaison
    with pytest.raises(promotion.VerdictRefuse, match="sans ligne"):
        promotion.comparer(dsn, uuid.uuid4().hex, regle, stockage)


def test_aucun_verdict_si_la_reference_a_bouge(comparaison):
    dsn, stockage, regle, _, nouveau, _, _ = comparaison
    regle["mesure"]["reference"] = {
        "global": 31,
        "reconnaissance": 21,
        "proposition": 10,
    }
    with pytest.raises(promotion.VerdictRefuse, match="la référence rend"):
        promotion.comparer(dsn, nouveau, regle, stockage)
