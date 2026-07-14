"""Canari — étape 7 : présence d'un EAN-13 / ISBN sur les fiches VOLUME.

Enjeu : clé de jointure avec Manga Insight (47 179 EAN en face, 79,9 %). Le
spider volumes ne déclare aucun champ `ean`/`isbn` aujourd'hui.

Cherche partout : bloc fiche technique, balises <meta>, JSON-LD.

Sortie : canari/ean.json + canari/html/tome_*.html
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from parsel import Selector

REFERENCE = Path("canari/reference.json")
DOSSIER_HTML = Path("canari/html")
SORTIE = Path("canari/ean.json")
UA = "manga-sanctuary-scraper/0.1"
DELAI_S = 2.0
N_TOMES = 5
BLOCAGE = {403, 429, 503}

XP_EAN = "//li[normalize-space(span[1])='EAN-13']/span[2]/text()"
XP_ISBN = "//li[normalize-space(span[1])='ISBN']/span[2]/text()"
RE_EAN_LIBRE = re.compile(r"\b97[89]\d{10}\b")


def telecharger(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def cle_ean13(code: str) -> bool:
    """Valide la clé de contrôle EAN-13 (13 chiffres, somme pondérée 1/3)."""
    if len(code) != 13 or not code.isdigit():
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(code[:12]))
    return (10 - total % 10) % 10 == int(code[12])


def main() -> None:
    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))["echantillon"]
    # 5 tomes issus de séries différentes de l'échantillon.
    tomes = []
    for s in ref:
        if s.get("_volume_urls"):
            tomes.append((s["series_id"], s["_volume_urls"][0]))
        if len(tomes) == N_TOMES:
            break

    DOSSIER_HTML.mkdir(parents=True, exist_ok=True)
    resultats = []
    for sid, url in tomes:
        code, corps = telecharger(url)
        if code in BLOCAGE:
            print(f"  [{code}] BLOCAGE sur {url} -> arrêt net")
            sys.exit(1)
        if code != 200:
            print(f"  [{code}] {url}")
            continue

        nom = url.rstrip("/").split("/")[-1][:60]
        (DOSSIER_HTML / f"tome_{sid}.html").write_bytes(corps)
        html = corps.decode("utf-8", "replace")
        sel = Selector(html)

        ean = (sel.xpath(XP_EAN).get() or "").strip() or None
        isbn = (sel.xpath(XP_ISBN).get() or "").strip() or None
        metas = sel.xpath("//meta[contains(@property,'isbn')]/@content").getall()
        jsonld = [
            s
            for s in sel.xpath("//script[@type='application/ld+json']/text()").getall()
            if "isbn" in s.lower() or "gtin" in s.lower()
        ]
        libres = sorted(set(RE_EAN_LIBRE.findall(html)))

        resultats.append(
            {
                "series_id": sid,
                "volume_url": url,
                "ean_li": ean,
                "ean_valide": cle_ean13(ean) if ean else None,
                "isbn_li": isbn,
                "meta_isbn": metas,
                "jsonld_isbn": bool(jsonld),
                "ean_libres_dans_html": libres,
            }
        )
        cle = cle_ean13(ean) if ean else "—"
        print(
            f"  [200] EAN={ean or '—':<14} clé_valide={cle!s:<5}"
            f" ISBN={isbn or '—':<6} meta={metas or '—'}"
            f" jsonld={bool(jsonld)}  {nom}"
        )
        time.sleep(DELAI_S)

    n = len(resultats)
    avec = sum(1 for r in resultats if r["ean_li"])
    valides = sum(1 for r in resultats if r["ean_valide"])
    print(f"\n{'=' * 60}")
    print(f"tomes testés : {n}")
    print(f"  EAN-13 affiché      : {avec}/{n}")
    print(f"  clé de contrôle OK  : {valides}/{avec if avec else 1}")
    SORTIE.write_text(
        json.dumps(resultats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"-> {SORTIE}")


if __name__ == "__main__":
    main()
