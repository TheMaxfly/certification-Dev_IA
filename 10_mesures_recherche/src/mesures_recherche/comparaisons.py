"""Les comparaisons déclarées d'avance : bootstrap apparié, Bonferroni.

    uv run python -m mesures_recherche.comparaisons resultats/mesure_*.json

Pour chaque comparaison (A contre B) et chaque métrique par question
(`hit_rate@10`, `ndcg@10`) : on tire, avec remise, 59 questions parmi les 59
questions de rang — les MÊMES pour A et B (apparié) —, 10 000 fois, graine fixe,
et on prend l'écart des moyennes A − B. Intervalle : percentiles à
1 − alpha / m (m = nombre de comparaisons déclarées : 4 au tour 1, 6 avec le
tour 2 ; Bonferroni). Verdict : « différence » si l'intervalle exclut 0, sinon
« non établie ».
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from mesures_recherche import configuration


def par_question(resultat: dict, metrique: str) -> dict[str, float]:
    return {
        q["question_id"]: q["metriques"][metrique]
        for q in resultat["questions"]
        if q["metriques"] is not None
    }


def bootstrap_apparie(
    a: np.ndarray, b: np.ndarray, tirages: int, graine: int, niveau: float
) -> dict:
    if a.shape != b.shape:
        raise ValueError("comparaison appariée : mêmes questions des deux côtés")
    rng = np.random.default_rng(graine)
    n = len(a)
    indices = rng.integers(0, n, size=(tirages, n))
    ecarts = (a[indices] - b[indices]).mean(axis=1)
    bas, haut = np.quantile(ecarts, [(1 - niveau) / 2, 1 - (1 - niveau) / 2])
    p = min(1.0, 2 * min((ecarts <= 0).mean(), (ecarts >= 0).mean()))
    return {
        "ecart": float(a.mean() - b.mean()),
        "ic_bas": float(bas),
        "ic_haut": float(haut),
        "p_bilateral": float(p),
        "verdict": "différence" if bas > 0 or haut < 0 else "non établie",
        "n_questions": n,
    }


def comparer(resultats: dict[int, dict], config: dict) -> list[dict]:
    b = config["bootstrap"]
    m = len(b["comparaisons"])
    niveau = 1 - b["alpha"] / m
    lignes = []
    for x, y in b["comparaisons"]:
        for metrique in b["metriques"]:
            qa, qb = (
                par_question(resultats[x], metrique),
                par_question(resultats[y], metrique),
            )
            if qa.keys() != qb.keys():
                raise ValueError(f"{x} contre {y} : questions de rang différentes")
            ids = sorted(qa)
            r = bootstrap_apparie(
                np.array([qa[i] for i in ids]),
                np.array([qb[i] for i in ids]),
                b["tirages"],
                b["graine"],
                niveau,
            )
            lignes.append(
                {
                    "comparaison": f"{x} contre {y}",
                    "metrique": metrique,
                    "niveau": niveau,
                    "seuil": b["alpha"] / m,
                    **r,
                }
            )
    return lignes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("fichiers", nargs="+", type=Path)
    parser.add_argument("--sortie", type=Path, required=True)
    args = parser.parse_args(argv)
    config = configuration.charger()
    resultats = {}
    for f in args.fichiers:
        r = json.loads(f.read_text(encoding="utf-8"))
        resultats[r["mesure"]] = r
    lignes = comparer(resultats, config)
    args.sortie.write_text(json.dumps(lignes, ensure_ascii=False, indent=1), "utf-8")
    for lg in lignes:
        print(
            f"{lg['comparaison']:>10}  {lg['metrique']:<12} écart {lg['ecart']:+.4f}"
            f"  IC [{lg['ic_bas']:+.4f} ; {lg['ic_haut']:+.4f}]"
            f"  p {lg['p_bilateral']:.4f}  {lg['verdict']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
