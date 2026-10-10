"""Comparer une mesure à la mesure 8 et rendre le verdict de la règle — E3, étape 1.

    uv run python -m mesures_recherche.promotion --run <run_id> --sortie c.json

La règle de promotion et l'hypothèse de gain sont lues dans
`config/promotion_corpus_v2.toml`, commitée AVANT la mesure ; la référence est le
run de la mesure 8 (corpus v1, encodage 2).

GARDE-FOUS (décision de Max, 2026-10-10) — sinon, AUCUN verdict :
  - le run mesuré a sa ligne `bench.eval_runs`, sur le corpus de la règle (v2), avec
    un encodage ;
  - cet encodage n'est PAS celui en service (`bench.v_encodage_en_service`) : on ne
    compare pas l'application à elle-même ;
  - la référence rend bien les nombres de la règle (31, 21, 10).

Le verdict est celui de la règle : « promotion proposée » ou « refus proposé ». La
décision reste celle de Max. Compté en questions, jamais en pourcentage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tomllib
import uuid
from collections import Counter
from pathlib import Path

import psycopg

from mesures_recherche.configuration import RACINE_MODULE
from mesures_recherche.enregistrement import STOCKAGE, uri_suivi

FICHIER = RACINE_MODULE / "config/promotion_corpus_v2.toml"
MODES = ("proposition", "reconnaissance")


class VerdictRefuse(RuntimeError):
    """Un garde-fou n'est pas tenu : la comparaison ne rend pas de verdict."""


def charger_regle(chemin: Path = FICHIER) -> dict:
    brut = chemin.read_bytes()
    r = tomllib.loads(brut.decode("utf-8"))
    r["empreinte"] = hashlib.sha256(brut).hexdigest()
    return r


def resultat_du_run(run_id: str, stockage: Path = STOCKAGE) -> dict:
    """La pièce `resultat.json` d'un run, telle que l'enregistrement l'a écrite."""
    from urllib.parse import urlparse

    from mlflow.tracking import MlflowClient

    run = MlflowClient(tracking_uri=uri_suivi(stockage)).get_run(run_id)
    dossier = Path(urlparse(run.info.artifact_uri).path)
    return json.loads((dossier / "resultat.json").read_text(encoding="utf-8"))


def comptes(resultat: dict) -> dict:
    """Questions de rang réussies : à dix résultats (la règle), et, en lecture, à
    cinquante (rang de la première série attendue)."""
    qs = [q for q in resultat["questions"] if q["de_rang"]]

    def reussie(q, k=10):
        if k == 10:
            return (q["metriques"] or {}).get("hit_rate@10", 0.0) == 1.0
        r = q["rang_premiere_attendue"]
        return r is not None and r <= k

    c = {
        "questions": len(qs),
        "global": sum(reussie(q) for q in qs),
        "a_50": sum(reussie(q, 50) for q in qs),
        "par_question": {q["question_id"]: reussie(q) for q in qs},
        "mode_de": {q["question_id"]: q["mode"] for q in qs},
        "famille_de": {q["question_id"]: q["famille"] for q in qs},
    }
    for mode in MODES:
        c[mode] = sum(reussie(q) for q in qs if q["mode"] == mode)
        c[f"{mode}_sur"] = sum(1 for q in qs if q["mode"] == mode)
    c["par_famille"] = dict(
        sorted(Counter(q["famille"] for q in qs if reussie(q)).items())
    )
    c["ndcg_10"] = next(
        a["valeur"]
        for a in resultat["agregats"]
        if (a["portee"], a["perimetre"], a["metrique"], a["k"])
        == ("global", "toutes", "ndcg", 10)
    )
    return c


def verdict(c: dict, regle: dict) -> str:
    r = regle["regle"]
    if (
        c["global"] >= r["global_min"]
        and c["reconnaissance"] >= r["reconnaissance_min"]
    ):
        return "promotion proposée"
    return "refus proposé"


def hypothese(c: dict, regle: dict) -> dict:
    h = regle["hypothese"]

    def dans(x, bornes):
        return bornes[0] <= x <= bornes[1]

    return {
        "proposition": {
            "attendu": f"≥ {h['proposition_min']}",
            "mesure": c["proposition"],
            "tenue": c["proposition"] >= h["proposition_min"],
        },
        "reconnaissance": {
            "attendu": h["reconnaissance"],
            "mesure": c["reconnaissance"],
            "tenue": dans(c["reconnaissance"], h["reconnaissance"]),
        },
        "global": {
            "attendu": h["global"],
            "mesure": c["global"],
            "tenue": dans(c["global"], h["global"]),
        },
    }


def mouvements(nouveau: dict, reference: dict) -> dict:
    """Questions gagnées et perdues, par mode et par famille — des nombres."""
    sortie = {"par_mode": {}, "par_famille": {}}
    for q, avant in reference["par_question"].items():
        apres = nouveau["par_question"].get(q)
        if apres is None or apres == avant:
            continue
        sens = "gagnees" if apres else "perdues"
        for cle, groupe in (
            ("par_mode", reference["mode_de"][q]),
            ("par_famille", reference["famille_de"][q]),
        ):
            sortie[cle].setdefault(groupe, Counter())[sens] += 1
    return {k: {g: dict(v) for g, v in sorted(d.items())} for k, d in sortie.items()}


def ligne_eval_runs(cx, run_id: str) -> dict | None:
    r = cx.execute(
        "SELECT run_id, corpus_id, encodage_id, inscrit_le, note FROM bench.eval_runs"
        " WHERE run_id = %s",
        (uuid.UUID(run_id),),
    )
    ligne = r.fetchone()
    return (
        dict(zip([c.name for c in r.description], ligne, strict=True))
        if ligne
        else None
    )


def comparer(dsn: str, run_id: str, regle: dict, stockage: Path = STOCKAGE) -> dict:
    m = regle["mesure"]
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as cx:
        run = ligne_eval_runs(cx, run_id)
        if run is None:
            raise VerdictRefuse(f"run {run_id} sans ligne bench.eval_runs")
        if run["corpus_id"] != m["corpus"] or run["encodage_id"] is None:
            raise VerdictRefuse(
                f"run {run_id} : corpus {run['corpus_id']}, encodage"
                f" {run['encodage_id']} — la règle porte sur le corpus {m['corpus']}"
            )
        service = cx.execute(
            "SELECT encodage_id, corpus_id FROM bench.v_encodage_en_service"
        ).fetchone()
        if service is not None and service[0] == run["encodage_id"]:
            raise VerdictRefuse(
                f"l'encodage mesuré ({run['encodage_id']}) est celui en service :"
                " la comparaison ne rend pas de verdict"
            )
        reference_run = ligne_eval_runs(cx, m["reference_run"])
    nouveau = comptes(resultat_du_run(run_id, stockage))
    reference = comptes(resultat_du_run(m["reference_run"], stockage))
    attendu = m["reference"]
    vue = {k: reference[k] for k in attendu}
    if vue != attendu:
        raise VerdictRefuse(f"la référence rend {vue}, la règle dit {attendu}")
    return {
        "run": {k: str(v) for k, v in run.items()},
        "reference_run": {k: str(v) for k, v in (reference_run or {}).items()},
        "encodage_en_service": list(service) if service else None,
        "regle_empreinte": regle["empreinte"],
        "regle": {
            k: regle["regle"][k]
            for k in ("metrique", "global_min", "reconnaissance_min")
        },
        "nouveau": {
            k: v
            for k, v in nouveau.items()
            if k not in ("par_question", "mode_de", "famille_de")
        },
        "reference": {
            k: v
            for k, v in reference.items()
            if k not in ("par_question", "mode_de", "famille_de")
        },
        "mouvements": mouvements(nouveau, reference),
        "hypothese": hypothese(nouveau, regle),
        "verdict": verdict(nouveau, regle),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, help="run_id MLflow de la mesure")
    parser.add_argument("--sortie", type=Path, required=True)
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL absente")
    r = comparer(dsn, args.run, charger_regle())
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(json.dumps(r, ensure_ascii=False, indent=1), "utf-8")
    print(
        json.dumps(
            {k: r[k] for k in ("verdict", "nouveau", "hypothese")}, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
