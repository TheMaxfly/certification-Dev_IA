"""La comparaison à la mesure 8 et le verdict de la règle (E3, étape 1, bloc G)."""

from __future__ import annotations

import copy
import json
import subprocess
import uuid

import psycopg
import pytest

from mesures_recherche import promotion
from mesures_recherche.configuration import RACINE_DEPOT
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


#: Le commit des runs d'essai : celui du dépôt, où la règle est commitée.
HEAD = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd=RACINE_DEPOT,
    capture_output=True,
    text=True,
    check=True,
).stdout.strip()


def run(stockage, contenu, commit: str | None = HEAD) -> str:
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
        if commit is not None:
            mlflow.log_param("commit", commit)
    return r.info.run_id


@pytest.fixture
def comparaison(banc, tmp_path, stockage_mlflow):
    """Référence : 2 sur 3 (une reconnaissance réussie) ; nouveau : 3 sur 3."""
    dsn, _, _ = banc
    stockage = stockage_mlflow
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


# --------------------------------------------------------------------------- #
#  La règle : celle du commit de la mesure, empreinte comparée
# --------------------------------------------------------------------------- #


def test_la_comparaison_porte_le_commit_de_la_regle(comparaison):
    dsn, stockage, regle, _, nouveau, _, _ = comparaison
    r = promotion.comparer(dsn, nouveau, regle, stockage)
    assert r["regle_commit"] == HEAD
    assert promotion.empreinte_au_commit(HEAD, regle["chemin"]) == regle["empreinte"]


def test_aucun_verdict_si_la_regle_differe_de_celle_du_commit(comparaison):
    dsn, stockage, regle, _, nouveau, _, _ = comparaison
    modifiee = regle | {"empreinte": "0" * 64}
    with pytest.raises(promotion.VerdictRefuse, match="pas celle du commit"):
        promotion.comparer(dsn, nouveau, modifiee, stockage)


def test_aucun_verdict_si_la_regle_n_etait_pas_commitee(comparaison):
    """Mesurée à un état du dépôt où le fichier n'existait pas : la règle a suivi
    la mesure. L'arbre vide de git tient lieu de cet état (un clone superficiel
    n'a pas d'historique)."""
    dsn, stockage, regle, _, nouveau, _, _ = comparaison
    premier = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # l'arbre vide de git
    assert promotion.empreinte_au_commit(premier, regle["chemin"]) is None
    with psycopg.connect(dsn) as cx:
        (encodage,) = cx.execute(
            "SELECT encodage_id FROM bench.eval_runs WHERE run_id = %s",
            (uuid.UUID(nouveau),),
        ).fetchone()
    tardif = run(stockage, promotion.resultat_du_run(nouveau, stockage), premier)
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.eval_runs (run_id, corpus_id, encodage_id)"
            " VALUES (%s, 'v2', %s)",
            (uuid.UUID(tardif), encodage),
        )
    with pytest.raises(promotion.VerdictRefuse, match="pas celle du commit"):
        promotion.comparer(dsn, tardif, regle, stockage)


def test_aucun_verdict_pour_un_run_sans_commit(comparaison):
    dsn, stockage, regle, _, nouveau, _, _ = comparaison
    with psycopg.connect(dsn) as cx:
        (encodage,) = cx.execute(
            "SELECT encodage_id FROM bench.eval_runs WHERE run_id = %s",
            (uuid.UUID(nouveau),),
        ).fetchone()
    sans = run(stockage, promotion.resultat_du_run(nouveau, stockage), None)
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.eval_runs (run_id, corpus_id, encodage_id)"
            " VALUES (%s, 'v2', %s)",
            (uuid.UUID(sans), encodage),
        )
    with pytest.raises(promotion.VerdictRefuse, match="sans commit"):
        promotion.comparer(dsn, sans, regle, stockage)
