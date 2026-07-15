"""Dump pivot Wikidata pour le référentiel d'identité manga.

Pipeline en 4 étapes (exécutables séparément ou enchaînées) :

    uv run wikidata_dump.py extract       # SPARQL paginé -> raw/pages/*.json
    uv run wikidata_dump.py consolidate   # pages -> staging/wd_pivot.csv
    uv run wikidata_dump.py hydrate       # wbgetentities -> raw/entities/*.json
    uv run wikidata_dump.py parse         # entités -> staging/wd_*.csv
    uv run wikidata_dump.py all           # tout enchaîner

Pattern ELT respecté : tout ce qui vient du réseau est stocké brut et daté
dans data/raw/wikidata/<date>/ avant toute transformation. Le parsing est
rejouable à volonté sans re-télécharger.

Dépendances : requests uniquement (uv add requests).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path

import requests

# --- Configuration ---------------------------------------------------------

CONTACT = "maxime.mcfly@gmail.com"  # requis par la politique Wikimedia
USER_AGENT = f"MangaAdvisorBot/0.1 (contact: {CONTACT}) python-requests"

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
WB_API = "https://www.wikidata.org/w/api.php"

PAGE_SIZE = 5000
BATCH_SIZE = 50  # max autorisé par wbgetentities (non authentifié)
PAUSE_S = 1.5  # politesse entre appels
MAX_RETRIES = 5

DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw" / "wikidata" / date.today().isoformat()
STAGING_DIR = DATA_DIR / "staging"

SPARQL_QUERY = """
SELECT ?item ?mal ?anilist WHERE {{
  {{ ?item wdt:P4087 ?mal }}
  UNION
  {{ ?item wdt:P8731 ?anilist }}
}}
ORDER BY ?item
LIMIT {limit} OFFSET {offset}
"""

# --- Utilitaires réseau ----------------------------------------------------


def _get(url: str, params: dict) -> requests.Response:
    """GET avec User-Agent identifié, backoff exponentiel sur 429/5xx."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    for tentative in range(MAX_RETRIES):
        r = requests.get(url, params=params, headers=headers, timeout=90)
        if r.status_code == 200:
            return r
        if r.status_code in (429, 500, 502, 503):
            attente = float(r.headers.get("Retry-After", 2**tentative * 5))
            print(f"  HTTP {r.status_code}, attente {attente:.0f}s...", file=sys.stderr)
            time.sleep(attente)
            continue
        r.raise_for_status()
    raise RuntimeError(f"Échec après {MAX_RETRIES} tentatives : {url}")


# --- Étape 1 : extraction SPARQL paginée -----------------------------------


def extract() -> None:
    pages_dir = RAW_DIR / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    offset, page = 0, 0
    while True:
        print(f"Page {page} (OFFSET {offset})...")
        r = _get(
            SPARQL_ENDPOINT,
            {
                "query": SPARQL_QUERY.format(limit=PAGE_SIZE, offset=offset),
                "format": "json",
            },
        )
        bindings = r.json()["results"]["bindings"]
        (pages_dir / f"page_{page:02d}.json").write_text(r.text, encoding="utf-8")
        print(f"  {len(bindings)} lignes")
        if len(bindings) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        page += 1
        time.sleep(PAUSE_S)
    print(f"Extraction terminée : {page + 1} pages dans {pages_dir}")


# --- Étape 2 : consolidation en pivot --------------------------------------


def consolidate() -> list[str]:
    pivot: dict[str, dict] = {}
    for fichier in sorted((RAW_DIR / "pages").glob("page_*.json")):
        for b in json.loads(fichier.read_text(encoding="utf-8"))["results"]["bindings"]:
            qid = b["item"]["value"].rsplit("/", 1)[-1]
            entree = pivot.setdefault(qid, {"mal_id": None, "anilist_id": None})
            if "mal" in b:
                entree["mal_id"] = b["mal"]["value"]
            if "anilist" in b:
                entree["anilist_id"] = b["anilist"]["value"]

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    with (STAGING_DIR / "wd_pivot.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["qid", "mal_id", "anilist_id"])
        for qid in sorted(pivot):
            w.writerow([qid, pivot[qid]["mal_id"], pivot[qid]["anilist_id"]])

    n_mal = sum(1 for v in pivot.values() if v["mal_id"])
    n_ani = sum(1 for v in pivot.values() if v["anilist_id"])
    print(f"QID distincts : {len(pivot)} (MAL: {n_mal}, AniList: {n_ani})")
    print("Contrôle attendu : ~8213 QID (le COUNT du 2026-07).")
    return sorted(pivot)


# --- Étape 3 : hydratation wbgetentities ------------------------------------


def hydrate(qids: list[str] | None = None) -> None:
    if qids is None:
        with (STAGING_DIR / "wd_pivot.csv").open(encoding="utf-8") as f:
            qids = [row["qid"] for row in csv.DictReader(f)]
    ent_dir = RAW_DIR / "entities"
    ent_dir.mkdir(parents=True, exist_ok=True)
    total = (len(qids) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(qids), BATCH_SIZE):
        lot = qids[i : i + BATCH_SIZE]
        cible = ent_dir / f"entities_{i:06d}.json"
        if cible.exists():  # reprise sur interruption sans re-télécharger
            continue
        r = _get(
            WB_API,
            {
                "action": "wbgetentities",
                "ids": "|".join(lot),
                "props": "labels|aliases|claims|sitelinks",
                "languages": "fr|en|ja",
                "format": "json",
            },
        )
        cible.write_text(r.text, encoding="utf-8")
        print(f"  lot {i // BATCH_SIZE + 1}/{total}")
        time.sleep(PAUSE_S)
    print(f"Hydratation terminée dans {ent_dir}")


# --- Normalisation (composant central, à tester en priorité) ---------------

ARTICLES = re.compile(r"^(le|la|les|l|the|a|an|der|die|das)\s+", re.IGNORECASE)

# Bloc « Combining Diacritical Marks » (U+0300–U+036F) : accents latins uniquement.
# Exclut volontairement le dakuten (U+3099) / handakuten (U+309A) japonais.
DIACRITIQUE_MIN, DIACRITIQUE_MAX = "̀", "ͯ"


def normaliser(titre: str) -> str:
    """Forme canonique d'un titre pour jointure exacte inter-sources.

    Ne retire que les **diacritiques latins** : « Élégante » -> « elegante ».
    Les marques de sonorisation japonaises (dakuten, handakuten) doivent survivre :
    les supprimer transformerait ドラゴンボール en トラコンホール et ferait
    collisionner des titres distincts (パラダイス et ハラダイス). D'où le filtre par
    plage, puis la recomposition NFC qui restitue テ+゛ -> デ.
    """
    t = unicodedata.normalize("NFKD", titre)
    t = "".join(c for c in t if not (DIACRITIQUE_MIN <= c <= DIACRITIQUE_MAX))
    t = unicodedata.normalize("NFC", t)
    t = t.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = ARTICLES.sub("", t.strip())
    return re.sub(r"\s+", " ", t).strip()


# --- Étape 4 : parsing des entités vers staging -----------------------------


def _claim_values(entity: dict, prop: str) -> list:
    """Valeurs d'une propriété (snaks de type value uniquement)."""
    valeurs = []
    for claim in entity.get("claims", {}).get(prop, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            valeurs.append(snak["datavalue"]["value"])
    return valeurs


def _first_claim(entity: dict, prop: str) -> str | None:
    """Première valeur d'une propriété, sinon None."""
    valeurs = _claim_values(entity, prop)
    return valeurs[0] if valeurs else None


def parse() -> None:
    entites_rows, formes_rows, auteurs_rows = [], [], []

    for fichier in sorted((RAW_DIR / "entities").glob("entities_*.json")):
        data = json.loads(fichier.read_text(encoding="utf-8"))
        for qid, ent in data.get("entities", {}).items():
            if "missing" in ent:
                continue

            # -- formes : labels + alias, toutes langues demandées
            formes_vues = set()
            for lang, label in ent.get("labels", {}).items():
                forme = normaliser(label["value"])
                if forme and (forme, lang) not in formes_vues:
                    formes_rows.append([qid, forme, label["value"], lang, "label"])
                    formes_vues.add((forme, lang))
            for lang, aliases in ent.get("aliases", {}).items():
                for alias in aliases:
                    forme = normaliser(alias["value"])
                    if forme and (forme, lang) not in formes_vues:
                        formes_rows.append([qid, forme, alias["value"], lang, "alias"])
                        formes_vues.add((forme, lang))

            # -- auteurs (P50) : QID à résoudre en 2e passe si besoin
            for auteur in _claim_values(ent, "P50"):
                auteurs_rows.append([qid, auteur.get("id")])

            # -- année de publication (P577) : première date trouvée
            annee = None
            dates = _claim_values(ent, "P577")
            if dates:
                m = re.match(r"[+-](\d{4})", dates[0].get("time", ""))
                annee = m.group(1) if m else None

            # -- IDs externes et sitelinks
            sitelinks = ent.get("sitelinks", {})
            entites_rows.append(
                [
                    qid,
                    ent.get("labels", {}).get("fr", {}).get("value")
                    or ent.get("labels", {}).get("en", {}).get("value"),
                    annee,
                    _first_claim(ent, "P4087"),
                    _first_claim(ent, "P8731"),
                    _first_claim(ent, "P1984"),
                    sitelinks.get("frwiki", {}).get("title"),
                    sitelinks.get("enwiki", {}).get("title"),
                ]
            )

    def ecrire(nom: str, entetes: list[str], lignes: list) -> None:
        with (STAGING_DIR / nom).open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(entetes)
            w.writerows(lignes)
        print(f"{nom} : {len(lignes)} lignes")

    ecrire(
        "wd_entities.csv",
        [
            "qid",
            "label_principal",
            "annee",
            "mal_id",
            "anilist_id",
            "ann_id",
            "wiki_fr",
            "wiki_en",
        ],
        entites_rows,
    )
    ecrire(
        "wd_formes.csv",
        ["qid", "forme_normalisee", "forme_originale", "langue", "type"],
        formes_rows,
    )
    ecrire("wd_auteurs.csv", ["qid", "auteur_qid"], auteurs_rows)


# --- CLI --------------------------------------------------------------------

ETAPES = {
    "extract": lambda: extract(),
    "consolidate": lambda: consolidate(),
    "hydrate": lambda: hydrate(),
    "parse": lambda: parse(),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("etape", choices=[*ETAPES, "all"])
    args = parser.parse_args()

    if CONTACT.startswith("CHANGE-MOI"):
        sys.exit(
            "Renseigne CONTACT (email) en tête de fichier : "
            "la politique User-Agent de Wikimedia l'exige."
        )

    if args.etape == "all":
        extract()
        qids = consolidate()
        hydrate(qids)
        parse()
    else:
        ETAPES[args.etape]()


if __name__ == "__main__":
    main()
