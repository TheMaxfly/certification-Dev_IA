"""Canari — étape 9 : vérifie le spider CORRIGÉ sur le HTML figé, hors réseau.

Rejoue le vrai spider (importé) sur les pages déjà téléchargées par les étapes
2, 5 et 6, et confronte ses sorties aux cibles mesurées par `04_alias_genres.py`
sur ces mêmes pages. Aucune requête : la vérification est rejouable à volonté.

Cibles attendues (mesurées avant correctif) :
  genres 0 -> 51 · tags 0 -> 42 · alias 19 -> 28 · EAN 3/5 · corps 5/5
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scrapy.http import HtmlResponse, Request

MODULE_SCRAPY = Path("manga_sanctuary").resolve()
sys.path.insert(0, str(MODULE_SCRAPY))

from manga_sanctuary.items import VolumeItem  # noqa: E402
from manga_sanctuary.spiders.manga_sanctuary_volumes import (  # noqa: E402
    MangaSanctuaryVolumesSpider,
)

REFERENCE = Path("canari/reference.json")
ANALYSE = Path("canari/analyse_html.json")
EAN = Path("canari/ean.json")
DOSSIER_HTML = Path("canari/html")


def reponse(fichier: Path, url: str) -> HtmlResponse:
    return HtmlResponse(url=url, body=fichier.read_bytes(), encoding="utf-8")


def meta_serie(spider, resp) -> dict | None:
    for sortie in spider.parse_series(resp, letter="?"):
        if isinstance(sortie, Request):
            meta = (sortie.cb_kwargs or {}).get("series_meta")
            if meta is not None:
                return dict(meta)
    return None


def main() -> None:
    spider = MangaSanctuaryVolumesSpider()
    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))["echantillon"]
    cibles = json.loads(ANALYSE.read_text(encoding="utf-8"))["totaux"]
    echecs = []

    # ---- Séries : genres, tags, alias ----
    tot = {"genres": 0, "tags": 0, "alias": 0}
    for s in ref:
        f = DOSSIER_HTML / f"serie_{s['series_id']}.html"
        if not f.exists():
            continue
        meta = meta_serie(spider, reponse(f, s["series_url"]))
        if meta is None:
            continue
        tot["genres"] += len(meta.get("series_genres") or [])
        tot["tags"] += len(meta.get("series_tags") or [])
        tot["alias"] += len(meta.get("series_other_titles") or [])

    attendu = {
        "genres": cibles["genres_corrige"],
        "tags": cibles["tags_corrige"],
        "alias": cibles["alias_corrige"],
    }
    print("SÉRIES — spider corrigé vs cible mesurée")
    for k in ("genres", "tags", "alias"):
        ok = tot[k] == attendu[k]
        etat = "OK" if ok else "ÉCHEC"
        print(f"  {k:7s} spider={tot[k]:3d}  cible={attendu[k]:3d}  {etat}")
        if not ok:
            echecs.append(f"{k}: {tot[k]} != {attendu[k]}")

    # ---- Tomes : EAN ----
    attendus_ean = {
        e["series_id"]: e["ean_li"] for e in json.loads(EAN.read_text(encoding="utf-8"))
    }
    print("\nTOMES — champ volume_ean")
    trouves = 0
    for sid, ean_attendu in attendus_ean.items():
        f = DOSSIER_HTML / f"tome_{sid}.html"
        if not f.exists():
            continue
        items = [
            o
            for o in spider.parse_volume(
                reponse(f, f"https://www.manga-sanctuary.com/tome_{sid}.html"),
                series_meta={},
            )
            if isinstance(o, VolumeItem)
        ]
        obtenu = items[0].get("volume_ean") if items else None
        ok = obtenu == ean_attendu
        trouves += bool(obtenu)
        print(
            f"  série {sid:>6} ean={obtenu or '—':<14} attendu={ean_attendu or '—':<14}"
            f" {'OK' if ok else 'ÉCHEC'}"
        )
        if not ok:
            echecs.append(f"ean {sid}: {obtenu!r} != {ean_attendu!r}")
    print(f"  -> {trouves}/{len(attendus_ean)} tomes avec EAN")

    # ---- Critiques : corps non vide ----
    print("\nCRITIQUES — review_body (pages à corps vide avant correctif)")
    pleins = 0
    fichiers = sorted(DOSSIER_HTML.glob("review_*.html"))
    for f in fichiers:
        reviews = list(
            spider.parse_staff_review(
                reponse(f, "https://www.manga-sanctuary.com/fiche_serie_critique.php"),
                series_meta={},
                volume_number=None,
                volume_url=None,
            )
        )
        corps = (reviews[0].get("review_body") or "") if reviews else ""
        pleins += bool(corps.strip())
        etat = "OK" if corps.strip() else "ÉCHEC"
        print(f"  {f.name:16s} {len(corps):5d} c  {etat}  {corps[:44]!r}")
        if not corps.strip():
            echecs.append(f"review_body vide : {f.name}")
    print(f"  -> {pleins}/{len(fichiers)} critiques avec corps (avant : 0)")

    print("\n" + "=" * 62)
    if echecs:
        print("ÉCHECS :")
        for e in echecs:
            print(f"  - {e}")
        sys.exit(1)
    print("Tous les correctifs sont vérifiés sur HTML figé.")


if __name__ == "__main__":
    main()
