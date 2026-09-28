"""La part Kitsu du corpus, construite depuis le raw de juillet — règle du 2026-09-29.

MÊME SNAPSHOT POUR TOUTES LES SOURCES. Le 28, les critiques venaient du raw MS
2026-07 manifesté (D2), mais les synopsis de `kitsu_series_core`, datée de
décembre 2025. Cette incohérence est levée : la part Kitsu se lit dans le run
`20260714T152202Z` (`manga.ndjson`, `relations/staff.ndjson`), après
vérification de leur sha256 contre `manifest.json`.

SÉLECTION (K1). Une œuvre Kitsu entre au corpus si :
  - elle a un SYNOPSIS non vide, une fois les mentions de source retirées. Une
    œuvre sans synopsis a un nom, déjà indexé dans le référentiel, et pas de
    description : le nom va à l'index lexical, la description au corpus ;
  - elle est de type manga, manhwa ou manhua. Les romans ne reviennent que s'ils
    sont rattachés au catalogue par la cascade ; les doujins jamais.

GABARIT — celui de décembre, retrouvé à l'identique (43 085 / 43 085) :

    Titres: <canonique> | <en ?? en_us ?? en_jp> | <ja_jp>
    Auteurs: <nom> (<rôle>); …        K2 : alimentée par le staff de juillet
    Tags: ["…", …]                    genres ∪ catégories, triés
    Synopsis: <synopsis nettoyé>

MÉTADONNÉES (K3, K4). Ni positions de tendance ni `boost_score` : ce sont des
instantanés de décembre, et deux snapshots ne se mêlent pas. La mention de
source retirée du texte (« (Source: MU) ») est gardée en `source_citee`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

RACINE = Path(__file__).resolve().parents[3]
RUN_DEFAUT = RACINE / "03_kitsu_api_exports/exports/full_catalog/20260714T152202Z"
FICHIERS = ("manga.ndjson", "relations/staff.ndjson")

SOUS_TYPES = ("manga", "manhwa", "manhua")
#: Admis seulement si la cascade les rattache au catalogue.
SOUS_TYPES_SUR_RATTACHEMENT = ("novel",)
ROLES = {"Story & Art": "Scénario & Dessin", "Story": "Scénario", "Art": "Dessin"}

#: Les mentions de source que décembre retirait du synopsis. Reproduit
#: décembre sur 42 441 des 43 034 œuvres communes ; les autres sont de vrais
#: changements de texte chez Kitsu.
MENTION_SOURCE = re.compile(
    r"\s*\(\s*Source\s*:([^)]*)\)|\s*\[\s*Source\s*:([^\]]*)\]|(?:^|\n)\s*Source\s*:([^\n]*)",
    re.IGNORECASE,
)


class ErreurKitsu(Exception):
    """Raw introuvable, non conforme à son manifeste, ou mal formé."""


def verifier_manifeste(run: Path) -> dict[str, str]:
    """sha256 de chaque fichier lu, vérifié contre `manifest.json` du run."""
    manifeste = run / "manifest.json"
    if not manifeste.is_file():
        raise ErreurKitsu(f"manifeste introuvable : {manifeste}")
    declares = {
        f["path"]: f["sha256"] for f in json.loads(manifeste.read_text())["files"]
    }
    empreintes = {}
    for nom in FICHIERS:
        chemin = run / nom
        if nom not in declares:
            raise ErreurKitsu(f"{nom} n'est pas déclaré dans {manifeste}")
        if not chemin.is_file():
            raise ErreurKitsu(f"raw introuvable : {chemin}")
        condense = hashlib.sha256()
        with chemin.open("rb") as f:
            for bloc in iter(lambda f=f: f.read(1 << 20), b""):
                condense.update(bloc)
        if condense.hexdigest() != declares[nom]:
            raise ErreurKitsu(
                f"{nom} : sha256 {condense.hexdigest()} ≠ manifeste {declares[nom]}"
            )
        empreintes[nom] = declares[nom]
    return empreintes


def nettoyer(synopsis: str) -> tuple[str, list[str]]:
    """Synopsis sans ses mentions de source, et les sources citées, dans l'ordre."""
    sources = []
    for m in MENTION_SOURCE.finditer(synopsis or ""):
        nom = next((g for g in m.groups() if g is not None), "").strip()
        if nom and nom not in sources:
            sources.append(nom)
    return MENTION_SOURCE.sub("", synopsis or "").strip(), sources


def titre_anglais(titres: dict) -> str | None:
    return titres.get("en") or titres.get("en_us") or titres.get("en_jp") or None


def texte(
    canonique: str | None,
    titres: dict,
    auteurs: list[tuple[str, str]],
    tags: list[str],
    synopsis: str,
) -> str:
    """Le gabarit de décembre. Les parties vides sont omises, comme `concat_ws`."""
    noms = [t for t in (canonique, titre_anglais(titres), titres.get("ja_jp")) if t]
    lignes = []
    if noms:
        lignes.append("Titres: " + " | ".join(noms))
    if auteurs:
        lignes.append("Auteurs: " + "; ".join(f"{n} ({r})" for n, r in auteurs))
    lignes.append("Tags: " + json.dumps(tags, ensure_ascii=False))
    if synopsis:
        lignes.append("Synopsis: " + synopsis)
    return "\n".join(lignes)


def lire_staff(chemin: Path) -> dict[int, list[tuple[str, str]]]:
    """kitsu_id → auteurs (nom, rôle en français), distincts, triés nom puis rôle."""
    auteurs: dict[int, set[tuple[str, str]]] = defaultdict(set)
    with chemin.open(encoding="utf-8") as f:
        for ligne in f:
            r = json.loads(ligne)
            noms = {
                p["id"]: (p.get("attributes") or {}).get("name")
                for p in r.get("included") or []
                if p.get("type") == "people"
            }
            for ms in r.get("data") or []:
                role = ROLES.get((ms.get("attributes") or {}).get("role"))
                personne = ((ms.get("relationships") or {}).get("person") or {}).get(
                    "data"
                ) or {}
                nom = noms.get(personne.get("id"))
                if role and nom:
                    auteurs[int(r["manga_id"])].add((nom.strip(), role))
    return {k: sorted(v) for k, v in auteurs.items()}


@dataclass
class Bilan:
    fichiers: dict[str, str] = field(default_factory=dict)
    oeuvres: int = 0
    exclues_sous_type: Counter = field(default_factory=Counter)
    sur_rattachement_admises: Counter = field(default_factory=Counter)
    rattachees_par_sous_type: Counter = field(default_factory=Counter)
    exclues_sans_synopsis: int = 0
    rattachees_sans_synopsis: int = 0
    retenues: int = 0
    avec_auteurs: int = 0
    sources_citees: Counter = field(default_factory=Counter)


def documents(run: Path, rattachees: set[int]) -> tuple[list[dict], Bilan]:
    """Documents `kitsu_synopsis` du run, triés par `doc_key`, et leur bilan.

    `rattachees` : les `kitsu_id` que la cascade rattache au catalogue.
    """
    bilan = Bilan(fichiers=verifier_manifeste(run))
    auteurs = lire_staff(run / "relations/staff.ndjson")
    docs = []
    with (run / "manga.ndjson").open(encoding="utf-8") as f:
        for ligne in f:
            enregistrement = json.loads(ligne)
            oeuvre = enregistrement["data"]
            a = oeuvre["attributes"]
            kitsu_id = int(oeuvre["id"])
            sous_type = a.get("subtype")
            bilan.oeuvres += 1
            if kitsu_id in rattachees:
                bilan.rattachees_par_sous_type[sous_type] += 1

            if sous_type not in SOUS_TYPES:
                if sous_type in SOUS_TYPES_SUR_RATTACHEMENT and kitsu_id in rattachees:
                    bilan.sur_rattachement_admises[sous_type] += 1
                else:
                    bilan.exclues_sous_type[sous_type] += 1
                    continue

            synopsis, sources = nettoyer(
                a.get("synopsis") or a.get("description") or ""
            )
            if not synopsis:
                bilan.exclues_sans_synopsis += 1
                bilan.rattachees_sans_synopsis += kitsu_id in rattachees
                continue

            inclus = enregistrement.get("included") or []
            tags = sorted(
                {
                    x["attributes"]["name"]
                    for x in inclus
                    if x.get("type") == "genres"
                    and (x.get("attributes") or {}).get("name")
                }
                | {
                    x["attributes"]["title"]
                    for x in inclus
                    if x.get("type") == "categories"
                    and (x.get("attributes") or {}).get("title")
                }
            )
            titres = a.get("titles") or {}
            canonique = a.get("canonicalTitle") or None
            ses_auteurs = auteurs.get(kitsu_id, [])
            meta = {
                "kitsu_id": kitsu_id,
                "title": canonique,
                "tags": tags,
                "subtype": sous_type,
                "popularity_rank": a.get("popularityRank"),
                "rating_rank": a.get("ratingRank"),
            }
            if sources:
                meta["source_citee"] = sources
                bilan.sources_citees.update(sources)
            docs.append(
                {
                    "doc_key": f"kitsu:{kitsu_id}",
                    "source": "kitsu_synopsis",
                    "series_id": None,
                    "kitsu_id": kitsu_id,
                    "boost_score": None,
                    "doc_text": texte(canonique, titres, ses_auteurs, tags, synopsis),
                    "metadata_json": json.dumps(
                        meta, ensure_ascii=False, sort_keys=True
                    ),
                    "title": canonique,
                }
            )
            bilan.retenues += 1
            bilan.avec_auteurs += bool(ses_auteurs)
    docs.sort(key=lambda d: d["doc_key"])
    return docs, bilan
