"""Diagnostic des classements — analyse exploratoire, sans nouvelle mesure.

    uv run python -m mesures_recherche.diagnostic 1 3 4 6 \
        --sortie resultats/diagnostic_bge-m3.json [--lecture F3]

Les classements sont recalculés à blanc, par le harnais des mesures : le calcul
est déterministe. Chaque recalcul est confronté au run MLflow de la même mesure
(pièce `resultat.json`) : mêmes 10 premières entités, même rang de la première
série attendue, pour chaque question — sinon, arrêt. Rien n'est écrit dans
MLflow ni dans `bench.eval_mesures` ; la session reste en lecture seule.

Pour chaque question de rang et chaque mesure :
  - `rang_complet` : rang de la première série attendue dans le classement
    complet (toutes les entités) ;
  - `rang_catalogue` : rang de cette même série parmi les seules séries du
    catalogue (les entités Kitsu retirées, l'ordre conservé) ;
  - `hors_catalogue_top10` : entités hors catalogue dans les 10 premières ;
  - `score_nul` : la première série attendue a un score nul — son rang ne
    tient alors qu'au départage par l'identifiant.

Par portée (global, mode, famille) : la médiane de ces trois valeurs, et la part
des questions dont la première série attendue est dans les 50, puis les 100
premières, des deux classements.

`--lecture F3` ajoute, pour les questions de la famille, les 10 premières
entités avec leur titre, leur appartenance au catalogue et leur score.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from mesures_recherche import configuration, executer, recherche
from mesures_recherche.entites import Entites
from mesures_recherche.jeu import Question

SEUILS = (50, 100)


@dataclass(frozen=True)
class Rangs:
    question_id: str
    famille: str
    mode: str
    serie: int  # la première série attendue du classement complet
    rang_complet: int
    rang_catalogue: int
    hors_catalogue_top10: int
    score: float
    score_nul: bool


def rangs(entites: Entites, c: recherche.Classement, q: Question, k: int = 10) -> Rangs:
    """Les trois valeurs du diagnostic pour une question de rang."""
    inverse = np.empty(len(c.ordre), dtype=np.int64)
    inverse[c.ordre] = np.arange(len(c.ordre))
    presentes = {
        s: int(inverse[entites.index_serie[s]])
        for s in q.attendus
        if s in entites.index_serie
    }
    if not presentes:
        raise ValueError(f"{q.question_id} : aucune série attendue parmi les entités")
    serie, position = min(presentes.items(), key=lambda x: (x[1], x[0]))
    series_avant = np.cumsum(entites.est_serie[c.ordre])
    e = entites.index_serie[serie]
    return Rangs(
        q.question_id,
        q.famille,
        q.mode,
        serie,
        position + 1,
        int(series_avant[position]),
        int((~entites.est_serie[c.ordre[:k]]).sum()),
        float(c.scores[e]),
        bool(c.scores[e] == 0),
    )


def portees(r: Rangs) -> list[str]:
    return ["global", f"mode:{r.mode}", f"famille:{r.famille}"]


def resumer(lignes: list[Rangs]) -> dict[str, dict]:
    """Par portée : n, médianes, parts dans les 50 et 100 premières."""
    groupes: dict[str, list[Rangs]] = {}
    for r in lignes:
        for p in portees(r):
            groupes.setdefault(p, []).append(r)

    def part(valeurs, seuil):
        return sum(v <= seuil for v in valeurs) / len(valeurs)

    sortie = {}
    for p, g in sorted(groupes.items()):
        complet = [r.rang_complet for r in g]
        catalogue = [r.rang_catalogue for r in g]
        sortie[p] = {
            "n": len(g),
            "mediane_rang_complet": statistics.median(complet),
            "mediane_rang_catalogue": statistics.median(catalogue),
            "mediane_hors_catalogue_top10": statistics.median(
                r.hors_catalogue_top10 for r in g
            ),
            **{f"part_complet_{s}": part(complet, s) for s in SEUILS},
            **{f"part_catalogue_{s}": part(catalogue, s) for s in SEUILS},
            "score_nul": sum(r.score_nul for r in g),
        }
    return sortie


def titres(cx, cles: list[str]) -> dict[str, str | None]:
    """Titre d'une entité : celui du catalogue pour une série, celui du document
    Kitsu sinon."""
    series = [int(c.split(":")[1]) for c in cles if c.startswith("serie:")]
    kitsu = [c for c in cles if c.startswith("kitsu:")]
    sortie: dict[str, str | None] = dict.fromkeys(cles)
    for sid, titre in cx.execute(
        "SELECT series_id, series_title FROM manga.ms_series_enriched"
        " WHERE series_id = ANY(%s)",
        (series,),
    ):
        sortie[f"serie:{sid}"] = titre
    for doc_key, titre in cx.execute(
        "SELECT doc_key, title FROM bench.corpus_docs WHERE doc_key = ANY(%s)",
        (kitsu,),
    ):
        sortie[doc_key] = titre
    return sortie


def reference_mlflow(numero: int, spec: str = "E2 jour 2") -> dict:
    """La pièce `resultat.json` du run MLflow de la mesure."""
    import mlflow
    from mesures_recherche.enregistrement import uri_suivi

    mlflow.set_tracking_uri(uri_suivi())
    config = configuration.charger()
    runs = mlflow.search_runs(
        experiment_names=[config["mlflow"]["experience"]],
        filter_string=f"tags.mesure = '{numero}' and tags.spec = '{spec}'",
        output_format="list",
    )
    if len(runs) != 1:
        raise RuntimeError(f"mesure {numero} : {len(runs)} run(s) « {spec} »")
    chemin = Path(
        mlflow.artifacts.download_artifacts(
            run_id=runs[0].info.run_id, artifact_path="resultat.json"
        )
    )
    resultat = json.loads(chemin.read_text(encoding="utf-8"))
    resultat["run_id"] = runs[0].info.run_id
    return resultat


def diagnostiquer(
    ctx: executer.Contexte,
    numero: int,
    reference: dict | None,
    lecture: str | None = None,
) -> dict:
    k = ctx.config["classement"]["k"]
    fonction = executer.configuration_de(ctx, numero)
    attendu = (
        {q["question_id"]: q for q in reference["questions"]} if reference else None
    )
    lignes, ecarts, a_lire = [], [], []
    for q in ctx.questions:
        c = recherche.classer(ctx.entites, fonction(q))
        top = c.top(ctx.entites, k)
        if attendu is not None:
            ref = attendu[q.question_id]
            if [cle for cle, _ in top] != [cle for cle, _ in ref["top"]]:
                ecarts.append(f"{q.question_id} : 10 premières entités différentes")
        if not q.de_rang:
            continue
        r = rangs(ctx.entites, c, q, k)
        if (
            attendu is not None
            and r.rang_complet != attendu[q.question_id]["rang_premiere_attendue"]
        ):
            ecarts.append(f"{q.question_id} : rang {r.rang_complet} ≠ jour 2")
        lignes.append(r)
        if lecture and q.famille == lecture:
            a_lire.append(
                {
                    "question_id": q.question_id,
                    "texte": q.texte,
                    "rang_premiere_attendue": r.rang_complet,
                    "attendus": dict(q.attendus),
                    "top": top,
                }
            )
    if ecarts:
        raise RuntimeError(f"mesure {numero} : recalcul ≠ jour 2 — {ecarts[:5]}")
    if a_lire:
        noms = titres(
            ctx.cx,
            sorted(
                {cle for x in a_lire for cle, _ in x["top"]}
                | {f"serie:{s}" for x in a_lire for s in x["attendus"]}
            ),
        )
        for x in a_lire:
            x["top"] = [
                {
                    "rang": i,
                    "entite": cle,
                    "titre": noms[cle],
                    "catalogue": cle.startswith("serie:"),
                    "score": s,
                }
                for i, (cle, s) in enumerate(x["top"], start=1)
            ]
            x["attendus"] = [
                {"serie": s, "titre": noms[f"serie:{s}"], "grade": g}
                for s, g in sorted(x["attendus"].items(), key=lambda t: (-t[1], t[0]))
            ]
    return {
        "mesure": numero,
        "nom": configuration.mesure(ctx.config, numero)["nom"],
        "verifie_contre_run": reference["run_id"] if reference else None,
        "questions": [asdict(r) for r in lignes],
        "portees": resumer(lignes),
        "lecture": a_lire,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mesures", type=int, nargs="+")
    parser.add_argument("--sortie", type=Path, required=True)
    parser.add_argument("--lecture", default=None, help="famille à lister, ex. F3")
    parser.add_argument("--sans-verification", action="store_true")
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL absente")
    ctx = executer.ouvrir(dsn, configuration.charger())
    try:
        sortie = {
            "jeu_empreinte": ctx.empreinte_jeu,
            "mesures": [
                diagnostiquer(
                    ctx,
                    n,
                    None if args.sans_verification else reference_mlflow(n),
                    args.lecture,
                )
                for n in args.mesures
            ],
        }
    finally:
        ctx.cx.close()
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(
        json.dumps(sortie, ensure_ascii=False, indent=1, default=float), "utf-8"
    )
    for m in sortie["mesures"]:
        g = m["portees"]["global"]
        print(
            f"mesure {m['mesure']} ({m['verifie_contre_run'] or 'non vérifiée'}) : "
            f"médianes rang {g['mediane_rang_complet']} / catalogue "
            f"{g['mediane_rang_catalogue']} / hors catalogue "
            f"{g['mediane_hors_catalogue_top10']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
