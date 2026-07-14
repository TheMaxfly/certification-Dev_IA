"""Canari — étape 6 : pourquoi 52,8 % des critiques ont un review_body vide.

3 562 critiques sur 6 749 ont un corps vide alors que 3 558 d'entre elles
portent un score. Deux hypothèses à départager sur pièces :

(a) le texte existe sur la page et le sélecteur le rate -> bug, le corpus RAG
    passerait de 3 187 à ~6 700 documents ;
(b) ce sont réellement des notes sans texte -> le 42,22 % de couverture RAG est
    une propriété de la source, pas un défaut.

Sortie : canari/reviews.json + canari/html/review_*.html
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from parsel import Selector

REVIEWS = Path("data/raw/2025-12/manga_sanctuary_reviews.jsonl")
DOSSIER_HTML = Path("canari/html")
SORTIE = Path("canari/reviews.json")
UA = "manga-sanctuary-scraper/0.1"
DELAI_S = 2.0
N = 5
BLOCAGE = {403, 429, 503}

# Sélecteur ACTUEL du spider (parse_staff_review).
XP_BODY_ACTUEL = (
    "//div[contains(@class,'post-single') and contains(@class,'text-justify')]"
    "//p//text()"
)


def telecharger(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> None:
    vides = []
    with REVIEWS.open(encoding="utf-8") as f:
        for ligne in f:
            d = json.loads(ligne)
            if not (d.get("review_body") or "").strip() and d.get("review_url"):
                vides.append(d)
            if len(vides) >= 400:
                break
    echantillon = vides[:: max(1, len(vides) // N)][:N]

    DOSSIER_HTML.mkdir(parents=True, exist_ok=True)
    resultats = []
    for i, r in enumerate(echantillon, 1):
        url = r["review_url"]
        code, corps = telecharger(url)
        if code in BLOCAGE:
            print(f"  [{code}] BLOCAGE sur {url} -> arrêt net")
            sys.exit(1)
        if code != 200:
            print(f"  [{code}] {url}")
            continue

        (DOSSIER_HTML / f"review_{i}.html").write_bytes(corps)
        html = corps.decode("utf-8", "replace")
        sel = Selector(html)

        actuel = " ".join(
            t.strip() for t in sel.xpath(XP_BODY_ACTUEL).getall() if t.strip()
        )
        # Le conteneur visé existe-t-il seulement sur la page ?
        conteneurs = sel.xpath("//div[contains(@class,'post-single')]/@class").getall()
        # Combien de texte la page porte-t-elle en tout (hors script/style) ?
        tout = " ".join(
            t.strip()
            for t in sel.xpath(
                "//body//text()[not(ancestor::script) and not(ancestor::style)]"
            ).getall()
            if t.strip()
        )
        resultats.append(
            {
                "review_url": url,
                "score_reference": r.get("review_score"),
                "titre_reference": r.get("review_title"),
                "body_selecteur_actuel": actuel,
                "longueur_body_actuel": len(actuel),
                "conteneurs_post_single": conteneurs,
                "longueur_texte_page": len(tout),
            }
        )
        print(
            f"  [200] {i}/{N} score={r.get('review_score')}"
            f" body_actuel={len(actuel):5d}c  post-single={conteneurs or 'ABSENT'}"
            f"  texte_page={len(tout)}c"
        )
        print(f"        {url}")
        time.sleep(DELAI_S)

    SORTIE.write_text(
        json.dumps(resultats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n-> {SORTIE}")
    vides_encore = sum(1 for r in resultats if r["longueur_body_actuel"] == 0)
    print(
        f"body toujours vide avec le sélecteur actuel : {vides_encore}/{len(resultats)}"
    )


if __name__ == "__main__":
    main()
