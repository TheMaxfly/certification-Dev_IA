"""Canari — étape 3 : comparaison champ par champ référence (2025-12) vs 2026-07.

Classe chaque champ de chaque série dans l'une des trois catégories :

- SELECTEUR_CASSE   : rempli en référence, vide aujourd'hui (casse structurelle
                      si observé sur plusieurs séries) ;
- EVOLUTION_DONNEE  : les deux remplis, valeurs différentes mais plausibles ;
- STABLE            : identique.

Deux catégories neutres complètent le tableau :
- VIDE_DES_DEUX     : vide avant et après (ne prouve rien sur le sélecteur) ;
- GAIN              : vide en référence, rempli aujourd'hui.

Sortie : canari/comparaison.json (consommé par le rapport)
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

REFERENCE = Path("canari/reference.json")
SCRAPE = Path("canari/scrape_2026-07.jsonl")
SORTIE = Path("canari/comparaison.json")

CHAMPS = [
    "series_title",
    "series_other_titles",
    "series_year",
    "series_dessinateur",
    "series_scenariste",
    "series_synopsis",
    "series_genres",
    "series_tags",
    "series_statuses",
    "series_type",
    "series_category",
    "series_mag_prepub",
    "series_popularity_rank",
    "series_members_rating",
    "series_members_votes",
    "series_experts_rating",
    "series_experts_votes",
    "series_related_works",
]


def est_vide(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, list | dict):
        return len(v) == 0
    return False


def classer(avant, apres) -> str:
    va, vp = est_vide(avant), est_vide(apres)
    if va and vp:
        return "VIDE_DES_DEUX"
    if not va and vp:
        return "SELECTEUR_CASSE"
    if va and not vp:
        return "GAIN"
    return "STABLE" if avant == apres else "EVOLUTION_DONNEE"


def main() -> None:
    ref = {
        s["series_id"]: s
        for s in json.loads(REFERENCE.read_text(encoding="utf-8"))["echantillon"]
    }
    apres = {}
    with SCRAPE.open(encoding="utf-8") as f:
        for ligne in f:
            d = json.loads(ligne)
            apres[d["series_id"]] = d

    par_champ: dict[str, Counter] = defaultdict(Counter)
    details: dict[str, list] = defaultdict(list)

    for sid, avant in ref.items():
        maintenant = apres.get(sid)
        if not maintenant or maintenant.get("_http") != 200:
            continue
        for champ in CHAMPS:
            verdict = classer(avant.get(champ), maintenant.get(champ))
            par_champ[champ][verdict] += 1
            if verdict in ("SELECTEUR_CASSE", "EVOLUTION_DONNEE", "GAIN"):
                details[champ].append(
                    {
                        "series_id": sid,
                        "titre": avant.get("series_title"),
                        "verdict": verdict,
                        "avant": avant.get(champ),
                        "apres": maintenant.get(champ),
                    }
                )

    n = sum(1 for sid in ref if apres.get(sid, {}).get("_http") == 200)
    print(f"séries comparées : {n}/25\n")
    entete = (
        f"{'champ':24s} {'STABLE':>7} {'CASSE':>6}"
        f" {'ÉVOL':>5} {'GAIN':>5} {'2xVIDE':>7}"
    )
    print(entete)
    print("-" * len(entete))
    for champ in CHAMPS:
        c = par_champ[champ]
        print(
            f"{champ:24s} {c['STABLE']:7d} {c['SELECTEUR_CASSE']:6d}"
            f" {c['EVOLUTION_DONNEE']:5d} {c['GAIN']:5d} {c['VIDE_DES_DEUX']:7d}"
        )

    SORTIE.write_text(
        json.dumps(
            {
                "series_comparees": n,
                "par_champ": {k: dict(v) for k, v in par_champ.items()},
                "details": details,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n-> {SORTIE}")

    casses = [c for c in CHAMPS if par_champ[c]["SELECTEUR_CASSE"] >= 2]
    print(f"\nchamps à casse structurelle (>=2 séries) : {casses or 'AUCUN'}")


if __name__ == "__main__":
    main()
