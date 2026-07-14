"""Canari — étape 1 : échantillon stratifié de 25 séries de référence.

Lit le dump JSONL de référence (grain volume), le ramène au grain série en
dédupliquant sur `series_id`, puis tire 25 séries réparties en quatre strates
disjointes pour couvrir les cas qui font varier les sélecteurs.

Sortie : canari/reference.json
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

REFERENCE = Path("data/raw/2025-12/manga_sanctuary_volumes.jsonl")
SORTIE = Path("canari/reference.json")
GRAINE = 20260714

# Champs série comparés par le canari (grain série uniquement).
CHAMPS_SERIE = [
    "series_id",
    "series_url",
    "series_title",
    "series_type",
    "series_category",
    "series_year",
    "series_other_titles",
    "series_dessinateur",
    "series_scenariste",
    "series_genres",
    "series_tags",
    "series_mag_prepub",
    "series_statuses",
    "series_popularity_rank",
    "series_members_rating",
    "series_members_votes",
    "series_experts_rating",
    "series_experts_votes",
    "series_synopsis",
    "series_related_works",
]

# Hiragana, katakana, kanji (+ extension A) : détecte un titre réellement natif.
NATIF = re.compile(r"[぀-ゟ゠-ヿ一-鿿㐀-䶿]")


def charger_grain_serie(chemin: Path) -> dict[str, dict]:
    """Ramène le JSONL grain volume au grain série (1re occurrence par series_id)."""
    series: dict[str, dict] = {}
    volumes: dict[str, list[str]] = {}
    with chemin.open(encoding="utf-8") as f:
        for ligne in f:
            d = json.loads(ligne)
            sid = d.get("series_id")
            if not sid:
                continue
            if sid not in series:
                series[sid] = {c: d.get(c) for c in CHAMPS_SERIE}
                volumes[sid] = []
            if d.get("volume_url"):
                volumes[sid].append(d["volume_url"])
    for sid, urls in volumes.items():
        series[sid]["_volume_urls"] = sorted(set(urls))
    return series


def a_alias_natif(serie: dict) -> bool:
    return any(NATIF.search(t or "") for t in serie.get("series_other_titles") or [])


def tirer(
    rng: random.Random, candidats: list[dict], n: int, pris: set[str]
) -> list[dict]:
    """Tire n séries parmi les candidats non déjà retenus (strates disjointes)."""
    dispo = [s for s in candidats if s["series_id"] not in pris]
    choix = rng.sample(dispo, min(n, len(dispo)))
    pris.update(s["series_id"] for s in choix)
    return choix


def main() -> None:
    series = charger_grain_serie(REFERENCE)
    toutes = list(series.values())
    rng = random.Random(GRAINE)
    pris: set[str] = set()

    # Strate 1 : populaires (rang faible = populaire ; rang 1 = Death Note).
    populaires = sorted(
        (s for s in toutes if s.get("series_popularity_rank")),
        key=lambda s: s["series_popularity_rank"],
    )[:150]
    # Strate 2 : titre alternatif natif (kanji/kana) — enjeu cascade d'identité.
    natifs = [s for s in toutes if a_alias_natif(s)]
    # Strate 3 : aucun titre alternatif.
    sans_alias = [s for s in toutes if not (s.get("series_other_titles") or [])]
    # Strate 4 : synopsis rempli.
    avec_synopsis = [s for s in toutes if (s.get("series_synopsis") or "").strip()]

    strates = [
        ("populaire", populaires, 8),
        ("alias_natif", natifs, 8),
        ("sans_alias", sans_alias, 5),
        ("synopsis", avec_synopsis, 4),
    ]

    echantillon = []
    for nom, candidats, n in strates:
        for s in tirer(rng, candidats, n, pris):
            s = dict(s)
            s["_strate"] = nom
            echantillon.append(s)

    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(
        json.dumps(
            {
                "genere_le": "2026-07-14",
                "source": str(REFERENCE),
                "graine": GRAINE,
                "series_total_reference": len(series),
                "echantillon": echantillon,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"référence : {len(series)} séries (grain série)")
    print(f"échantillon : {len(echantillon)} séries -> {SORTIE}")
    for nom, _, n in strates:
        reel = sum(1 for s in echantillon if s["_strate"] == nom)
        print(f"  strate {nom:12s} demandé={n} obtenu={reel}")
    print("\ndétail :")
    for s in echantillon:
        alias = s.get("series_other_titles") or []
        print(
            f"  [{s['_strate']:11s}] rang={s['series_popularity_rank'] or '?':<6}"
            f" alias={len(alias)} vol={len(s['_volume_urls']):3d}"
            f"  {s['series_title'][:38]}"
        )


if __name__ == "__main__":
    main()
