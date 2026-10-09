"""Les runs mesurés avant 021, inscrits une fois dans `bench.eval_runs` depuis
leurs paramètres MLflow."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from mesures_recherche import historique
from mesures_recherche.enregistrement import uri_suivi


def run_mlflow(stockage, mesure: str, parametres: dict) -> str:
    import mlflow

    mlflow.set_tracking_uri(uri_suivi(stockage))
    mlflow.set_experiment("essai")
    with mlflow.start_run() as run:
        mlflow.set_tag("mesure", mesure)
        mlflow.log_params(parametres)
    return run.info.run_id


def mesurer(dsn: str, run_id: str) -> None:
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.eval_mesures (run_id, jeu_version, portee, perimetre,"
            " metrique, k, valeur, n_questions) VALUES (%s, 'v9', 'global',"
            " 'toutes', 'hit_rate', 10, 0.5, 2)",
            (uuid.UUID(run_id),),
        )


@pytest.mark.parametrize(
    "parametres, attendu",
    [
        ({"encodage_id": "2", "x": "1"}, 2),
        ({"source_1_encodage_id": "1", "source_3_vocabulaire": "9"}, 1),
        ({"vocabulaire": "9"}, None),
    ],
)
def test_encodage_lu_dans_les_parametres(parametres, attendu):
    assert historique.encodage_du_run(parametres) == attendu


def test_deux_encodages_dans_un_run_refuses():
    with pytest.raises(RuntimeError, match="plusieurs encodages"):
        historique.encodage_du_run(
            {"source_1_encodage_id": "1", "source_2_encodage_id": "2"}
        )


def test_inscrits_une_fois_depuis_mlflow(banc, tmp_path):
    dsn, _, _ = banc
    with psycopg.connect(dsn) as cx:
        (eid,) = cx.execute("SELECT encodage_id FROM bench.encodages").fetchone()
    stockage = tmp_path / "mlflow"
    sens = run_mlflow(stockage, "8", {"encodage_id": str(eid)})
    lexical = run_mlflow(stockage, "3", {"vocabulaire": "100"})
    for r in (sens, lexical):
        mesurer(dsn, r)
    lignes = historique.inscrire(dsn, corpus_id="v1", stockage=stockage)
    assert {(r.hex, c, e) for r, c, e, _ in lignes} == {
        (sens, "v1", eid),
        (lexical, "v1", None),
    }
    assert historique.inscrire(dsn, corpus_id="v1", stockage=stockage) == []
    with psycopg.connect(dsn) as cx:
        assert cx.execute("SELECT count(*) FROM bench.eval_runs").fetchone() == (2,)


def test_un_run_sans_trace_mlflow_arrete_tout(banc, tmp_path):
    dsn, _, _ = banc
    stockage = tmp_path / "mlflow"
    connu = run_mlflow(stockage, "8", {"encodage_id": "2"})
    mesurer(dsn, connu)
    mesurer(dsn, uuid.uuid4().hex)
    with pytest.raises(historique.RunIntrouvable):
        historique.inscrire(dsn, corpus_id="v1", stockage=stockage)
    with psycopg.connect(dsn) as cx:
        assert cx.execute("SELECT count(*) FROM bench.eval_runs").fetchone() == (0,)
