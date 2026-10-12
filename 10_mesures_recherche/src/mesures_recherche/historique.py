"""Inscrire dans `bench.eval_runs` les runs mesurés avant la migration 021.

    uv run python -m mesures_recherche.historique --corpus v1

Les runs d'E2 (mesures 1 à 17) ont écrit leurs lignes dans `bench.eval_mesures`
avant que `bench.eval_runs` n'existe. Leur corpus est le v1 — il n'y en avait pas
d'autre —, et leur encodage est dans leurs paramètres MLflow : `encodage_id` pour
une mesure par le sens, `source_<n>_encodage_id` pour une fusion, aucun pour une
mesure lexicale.

Une fois : un run déjà inscrit n'est pas réécrit (la table refuse toute
modification). Un run sans trace dans MLflow arrête tout, avant d'écrire : on
n'inscrit pas un encodage deviné.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

from mesures_recherche.enregistrement import STOCKAGE, uri_suivi


class RunIntrouvable(RuntimeError):
    """Un run d'`eval_mesures` n'a pas de trace dans le magasin MLflow."""


def encodage_du_run(parametres: dict[str, str]) -> int | None:
    """L'encodage lu par un run, d'après ses paramètres ; None sans vecteurs."""
    valeurs = {v for k, v in parametres.items() if k.endswith("encodage_id")}
    if len(valeurs) > 1:
        raise RuntimeError(f"plusieurs encodages dans un même run : {sorted(valeurs)}")
    return int(valeurs.pop()) if valeurs else None


def inscrire(dsn: str, *, corpus_id: str, stockage: Path = STOCKAGE) -> list[tuple]:
    """Inscrit les runs d'`eval_mesures` absents d'`eval_runs` ; rend les lignes
    écrites (run_id, corpus_id, encodage_id, note)."""
    from mlflow.exceptions import MlflowException
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=uri_suivi(stockage))
    with psycopg.connect(dsn) as cx:
        absents = [
            r
            for (r,) in cx.execute(
                "SELECT DISTINCT m.run_id FROM bench.eval_mesures m"
                " WHERE NOT EXISTS (SELECT 1 FROM bench.eval_runs r"
                "                   WHERE r.run_id = m.run_id)"
                " ORDER BY 1"
            )
        ]
        lignes = []
        for run_id in absents:
            try:
                run = client.get_run(run_id.hex)
            except MlflowException as exc:
                raise RunIntrouvable(f"run {run_id} absent du magasin MLflow") from exc
            mesure = run.data.tags.get("mesure", "?")
            lignes.append(
                (
                    run_id,
                    corpus_id,
                    encodage_du_run(run.data.params),
                    f"mesure {mesure} — inscrit après coup, depuis ses paramètres"
                    " MLflow (migration 021)",
                )
            )
        with cx.cursor() as cur:
            cur.executemany(
                "INSERT INTO bench.eval_runs (run_id, corpus_id, encodage_id, note)"
                " VALUES (%s, %s, %s, %s)",
                lignes,
            )
    return lignes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", required=True, help="le corpus de ces runs")
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL absente")
    lignes = inscrire(dsn, corpus_id=args.corpus)
    print(
        json.dumps(
            [
                {"run_id": str(r), "corpus_id": c, "encodage_id": e, "note": n}
                for r, c, e, n in lignes
            ],
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
