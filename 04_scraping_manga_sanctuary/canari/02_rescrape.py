"""Canari — étape 2 : re-scrape des 25 séries avec les SÉLECTEURS ACTUELS.

Télécharge les fiches série de l'échantillon puis leur applique le vrai
`parse_series` du spider en production (importé, non réimplémenté) : le canari
teste ainsi le code réel du spider, pas une copie qui pourrait diverger.

`parse_series` ne yield pas d'item série : il construit `series_meta` et le passe
aux requêtes tome via `cb_kwargs`. On récupère donc `series_meta` sur la première
requête émise.

Politesse : 2 s entre requêtes, User-Agent du projet, aucune concurrence.

Sorties : canari/scrape_2026-07.jsonl + canari/html/serie_<id>.html
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from scrapy.http import HtmlResponse, Request

MODULE_SCRAPY = Path("manga_sanctuary").resolve()
sys.path.insert(0, str(MODULE_SCRAPY))

from manga_sanctuary.spiders.manga_sanctuary_volumes import (  # noqa: E402
    MangaSanctuaryVolumesSpider,
)

REFERENCE = Path("canari/reference.json")
SORTIE = Path("canari/scrape_2026-07.jsonl")
DOSSIER_HTML = Path("canari/html")
UA = "manga-sanctuary-scraper/0.1"
DELAI_S = 2.0
BLOCAGE = {403, 429, 503}


def telecharger(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def series_meta_via_spider(spider, url: str, corps: bytes) -> dict | None:
    """Rejoue parse_series du spider et récupère le series_meta transmis aux tomes."""
    response = HtmlResponse(url=url, body=corps, encoding="utf-8")
    for sortie in spider.parse_series(response, letter="?"):
        if isinstance(sortie, Request):
            meta = (sortie.cb_kwargs or {}).get("series_meta")
            if meta is not None:
                return dict(meta)
    return None


def main() -> None:
    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))
    echantillon = ref["echantillon"]
    DOSSIER_HTML.mkdir(parents=True, exist_ok=True)
    spider = MangaSanctuaryVolumesSpider()

    resultats = []
    for i, serie in enumerate(echantillon, 1):
        url = serie["series_url"]
        sid = serie["series_id"]
        code, corps = telecharger(url)

        if code in BLOCAGE:
            print(f"  [{code}] BLOCAGE détecté sur {url} -> arrêt net du canari")
            sys.exit(1)
        if code != 200:
            print(f"  [{code}] {url} (ignorée)")
            resultats.append({"series_id": sid, "series_url": url, "_http": code})
            time.sleep(DELAI_S)
            continue

        (DOSSIER_HTML / f"serie_{sid}.html").write_bytes(corps)
        meta = series_meta_via_spider(spider, url, corps)
        if meta is None:
            print(f"  [200] {sid} : aucun lien tome -> series_meta indisponible")
            resultats.append(
                {"series_id": sid, "series_url": url, "_http": 200, "_sans_tome": True}
            )
        else:
            meta["_http"] = 200
            meta["_strate"] = serie["_strate"]
            resultats.append(meta)
            alias = len(meta.get("series_other_titles") or [])
            genres = len(meta.get("series_genres") or [])
            tags = len(meta.get("series_tags") or [])
            print(
                f"  [200] {i:2d}/25 {sid:>6} alias={alias} genres={genres}"
                f" tags={tags}  {(meta.get('series_title') or '')[:34]}"
            )
        time.sleep(DELAI_S)

    with SORTIE.open("w", encoding="utf-8") as f:
        for r in resultats:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n{len(resultats)} séries -> {SORTIE}")
    print(f"HTML conservé dans {DOSSIER_HTML}/ (analyse des étapes 4 et 8)")


if __name__ == "__main__":
    main()
