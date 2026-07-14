"""Canari — étapes 4 et 8 : structure réelle des alias, genres et tags.

Le bloc d'infos des fiches est un `<ul class="fiche-infos">` de lignes
`<li><span>LABEL</span><span>VALEUR</span></li>`. Deux pièges :

1. Alias : les titres alternatifs suivants occupent des `<li>` FRÈRES dont le
   `<span>` de label est VIDE. `following::span[1]` n'en retient donc qu'un.
2. Genres/tags : les valeurs sont des `<a>` dont le parent est un `<span>`
   (genres) ou un `<div>` (tags) — jamais un `<p>`, d'où l'échec du
   `parent::p//a` actuel.

Ce script mesure l'écart entre sélecteurs actuels et sélecteurs corrigés sur les
25 pages du canari. Il ne modifie pas le spider.
"""

from __future__ import annotations

import json
from pathlib import Path

from parsel import Selector

REFERENCE = Path("canari/reference.json")
DOSSIER_HTML = Path("canari/html")
SORTIE = Path("canari/analyse_html.json")

# --- Sélecteurs ACTUELS (copiés du spider en production) ---
XP_ALIAS_ACTUEL = "//text()[contains(., 'Autres titres')]/following::span[1]//text()"
XP_GENRES_ACTUEL = "//text()[contains(., 'Genres')]/following::a[1]/parent::p//a/text()"
XP_TAGS_ACTUEL = "//text()[contains(., 'Tags')]/following::a[1]/parent::p//a/text()"

# --- Sélecteurs CORRIGÉS proposés ---
# Le test porte sur span[1] (le label) : une ligne de continuation a un label
# vide mais bien un span[2] de valeur non vide — tester « aucun span non vide »
# échouerait donc à la repérer.
_ANCRE_ALIAS = "//li[normalize-space(span[1])='Autres titres']"
# Alias : la ligne étiquetée, plus les lignes suivantes à label vide dont la
# ligne étiquetée la plus proche en amont est justement « Autres titres »
# (intersection Kayessian : count(A | B) = 1 <=> A et B sont le même noeud).
# C'est ce test qui borne la collecte au bloc alias sans déborder sur « Type ».
XP_ALIAS_CORRIGE = (
    f"{_ANCRE_ALIAS}/span[2]//text()"
    f" | {_ANCRE_ALIAS}/following-sibling::li["
    "not(normalize-space(span[1]))"
    f" and count(preceding-sibling::li[normalize-space(span[1])][1]"
    f" | {_ANCRE_ALIAS}) = 1"
    "]/span[2]//text()"
)
XP_GENRES_CORRIGE = "//li[normalize-space(span[1])='Genres']/span[2]//a/text()"
XP_TAGS_CORRIGE = "//li[normalize-space(span[1])='Tags']/span[2]//a/text()"


def nettoyer(valeurs: list[str], strip_diese: bool = False) -> list[str]:
    out = []
    for v in valeurs:
        v = v.strip()
        if strip_diese:
            v = v.lstrip("#")
        if v:
            out.append(v)
    return out


def main() -> None:
    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))["echantillon"]
    lignes = []
    tot = {
        "alias_actuel": 0,
        "alias_corrige": 0,
        "genres_actuel": 0,
        "genres_corrige": 0,
        "tags_actuel": 0,
        "tags_corrige": 0,
        "series_avec_genres": 0,
        "series_avec_tags": 0,
        "series_alias_tronques": 0,
    }

    for s in ref:
        f = DOSSIER_HTML / f"serie_{s['series_id']}.html"
        if not f.exists():
            continue
        sel = Selector(f.read_text(encoding="utf-8", errors="replace"))

        a_act = nettoyer(sel.xpath(XP_ALIAS_ACTUEL).getall())
        a_cor = nettoyer(sel.xpath(XP_ALIAS_CORRIGE).getall())
        g_act = nettoyer(sel.xpath(XP_GENRES_ACTUEL).getall())
        g_cor = nettoyer(sel.xpath(XP_GENRES_CORRIGE).getall())
        t_act = nettoyer(sel.xpath(XP_TAGS_ACTUEL).getall(), strip_diese=True)
        t_cor = nettoyer(sel.xpath(XP_TAGS_CORRIGE).getall(), strip_diese=True)

        tot["alias_actuel"] += len(a_act)
        tot["alias_corrige"] += len(a_cor)
        tot["genres_actuel"] += len(g_act)
        tot["genres_corrige"] += len(g_cor)
        tot["tags_actuel"] += len(t_act)
        tot["tags_corrige"] += len(t_cor)
        tot["series_avec_genres"] += bool(g_cor)
        tot["series_avec_tags"] += bool(t_cor)
        tot["series_alias_tronques"] += len(a_cor) > len(a_act)

        lignes.append(
            {
                "series_id": s["series_id"],
                "strate": s["_strate"],
                "titre": s["series_title"],
                "alias_actuel": a_act,
                "alias_corrige": a_cor,
                "genres_corrige": g_cor,
                "tags_corrige": t_cor,
            }
        )

        print(
            f"[{s['_strate']:11s}] alias {len(a_act)}->{len(a_cor)}"
            f"  genres {len(g_act)}->{len(g_cor)}"
            f"  tags {len(t_act)}->{len(t_cor)}   {s['series_title'][:30]}"
        )

    n = len(lignes)
    print(f"\n{'=' * 66}\nBILAN sur {n} fiches série")
    print(f"  alias  : {tot['alias_actuel']} -> {tot['alias_corrige']}")
    print(f"           séries tronquées : {tot['series_alias_tronques']}/{n}")
    print(
        f"  genres : {tot['genres_actuel']} -> {tot['genres_corrige']}"
        f"   ({tot['series_avec_genres']}/{n} séries en ont)"
    )
    print(
        f"  tags   : {tot['tags_actuel']} -> {tot['tags_corrige']}"
        f"   ({tot['series_avec_tags']}/{n} séries en ont)"
    )

    SORTIE.write_text(
        json.dumps({"totaux": tot, "series": lignes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n-> {SORTIE}")


if __name__ == "__main__":
    main()
