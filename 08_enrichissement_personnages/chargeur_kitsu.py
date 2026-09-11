#!/usr/bin/env python3
"""Chargement des personnages Kitsu — raw -> staging tout-TEXT -> tables typees.

ELT strict, trois etages. Le raw (121 Mo) n'est jamais modifie ni lu d'un bloc :
il est parcouru ligne a ligne et verse par lots bornes.

LES QUATRE REGLES APPLIQUEES, TOUTES VALIDEES LE 2026-09-12

  A  `mal_id` conserve — 34 224 valeurs distinctes sur 34 293 personnages. Le
     point 16 de la feuille visait le staff, dont les 15 397 personnes portaient
     la meme chaine litterale : l'identifiant y etait factice.
  B  langue : trois classes `en` / `ja` / NULL. `fr` n'est pas tente — 0,4 %
     mesure, et les marqueurs francais apparaissent dans des noms propres au
     sein de textes anglais.
  C  licence : provenance extraite par motif, puis CC BY-SA si elle designe un
     wiki de la galaxie Wikipedia/Wikia/Fandom, « a instruire » sinon.
  D  roles : quatre valeurs francaises, l'original conserve a cote.

CE QUE CE CHARGEUR NE FAIT PAS, ET NE DOIT JAMAIS FAIRE

  - aucune deduplication entre sources : un personnage Kitsu et un personnage
    Wikipedia restent deux lignes jusqu'a la cascade ;
  - aucun moyeu d'identite ;
  - `source = 'kitsu'` sur les quatre tables, **sans exception**. Une ligne qui
    passerait sans source casserait le schema multi-sources en silence, et
    rendrait le chargement Wikipedia impossible sans migration.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg

from commun.chargement import (
    TamponCopy,
    date_iso,
    normaliser,
    promouvoir,
)

SOURCE = "kitsu"
RACINE = Path(__file__).resolve().parent
RAW_DEFAUT = (
    RACINE.parent
    / "03_kitsu_api_exports/exports/full_catalog/20260714T152202Z"
    / "relations/characters.ndjson"
)

CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")

# --- Regle B -----------------------------------------------------------------
MARQUEURS_EN = re.compile(r"\b(the|is|his|her|and|of|to|was|he|she|who)\b", re.I)
#: Au-dela de ce taux de caracteres CJK, le texte est japonais. Un nom propre
#: japonais cite dans une phrase anglaise reste tres en dessous.
SEUIL_CJK = 0.30

# --- Regle C -----------------------------------------------------------------
SOURCE_CITEE = re.compile(r"\(?\s*Source\s*:\s*([^)\n]{1,80})", re.I)
GALAXIE_WIKI = re.compile(r"wikipedia|wikia|fandom", re.I)

# --- Regle D -----------------------------------------------------------------
ROLES = {
    "main": "principal",
    "supporting": "secondaire",
    "recurring": "recurrent",
    "cameo": "apparition",
}

#: Les cles de `names` telles que la source les ecrit. Le mappage suit la
#: DECLARATION de la source, pas le contenu : 196 valeurs sous `ja_jp` sont
#: romanisees, et les « corriger » ici masquerait un defaut de la source au lieu
#: de le rendre mesurable. `en_jp` (9 formes) est conserve tel quel — sa
#: semantique n'est pas etablie, et l'inventer serait pire que l'ignorer.
LANGUES = {"en": "en", "ja_jp": "ja", "jp": "ja"}


def langue_de(texte: str) -> str | None:
    """Regle B. Rend `en`, `ja`, ou NULL quand la regle ne conclut pas."""
    if not texte.strip():
        return None
    if len(CJK.findall(texte)) / len(texte) > SEUIL_CJK:
        return "ja"
    if MARQUEURS_EN.search(texte):
        return "en"
    return None


def licence_de(texte: str) -> tuple[str | None, str | None]:
    """Regle C. Rend (licence, provenance)."""
    trouve = SOURCE_CITEE.search(texte)
    if not trouve:
        return None, None
    provenance = trouve.group(1).strip().rstrip(".,;")
    if not provenance:
        return None, None
    return (
        "CC BY-SA" if GALAXIE_WIKI.search(provenance) else "à instruire",
        provenance,
    )


def formes_de(attributs: dict) -> list[tuple[str, str, str | None]]:
    """Rend (forme, forme_type, forme_lang) — TROIS champs, pas un.

    `canonicalName` donne une forme `canonical`. Chaque entree de `names` donne
    une forme `name_lang` avec sa langue. Chaque entree de `otherNames` donne
    une forme `alias`.

    **La graphie japonaise n'est PAS un alias.** Elle vient de `names.ja_jp`,
    donc en `name_lang` avec `forme_lang = 'ja'` — c'est elle que l'index
    partiel de la migration 015 sert, et c'est le pont de fusion entre les
    trois sources. La releguer en alias le rendrait inexploitable.

    L'attendu de 9 868 formes ne porte donc QUE sur le troisieme champ.
    """
    formes: list[tuple[str, str, str | None]] = []
    canonique = (attributs.get("canonicalName") or "").strip()
    if canonique:
        formes.append((canonique, "canonical", None))
    for cle, valeur in (attributs.get("names") or {}).items():
        forme = str(valeur or "").strip()
        if forme:
            formes.append((forme, "name_lang", LANGUES.get(cle, cle)))
    for alias in attributs.get("otherNames") or []:
        forme = str(alias or "").strip()
        if forme:
            formes.append((forme, "alias", None))
    return formes


def remplir_staging(connexion: psycopg.Connection, raw: Path) -> Counter:
    """Parcourt le raw ligne a ligne et verse les quatre zones d'atterrissage.

    Rien n'est filtre ici : la zone d'atterrissage ne refuse rien. Le typage,
    les casts et l'unicite sont l'affaire de la promotion.
    """
    compteurs: Counter = Counter()
    with connexion.cursor() as curseur:
        curseur.execute(
            "TRUNCATE staging.characters, staging.character_forms, "
            "staging.character_descriptions, staging.character_work"
        )

    # Borne en O(personnages distincts), pas en O(octets) : sans elle, un
    # personnage lie a 12 oeuvres verserait 12 fois ses formes.
    deja_vus: set[str] = set()
    nom_fichier = raw.name

    with (
        TamponCopy(
            connexion,
            "staging.characters",
            (
                "source",
                "source_id",
                "slug",
                "canonical_name",
                "mal_id",
                "source_created_at",
                "source_updated_at",
                "source_file",
            ),
        ) as t_perso,
        TamponCopy(
            connexion,
            "staging.character_forms",
            (
                "source",
                "source_id",
                "forme",
                "forme_norm",
                "forme_type",
                "forme_lang",
                "source_file",
            ),
        ) as t_formes,
        TamponCopy(
            connexion,
            "staging.character_descriptions",
            (
                "source",
                "source_id",
                "lang",
                "texte",
                "licence",
                "provenance",
                "source_file",
            ),
        ) as t_desc,
        TamponCopy(
            connexion,
            "staging.character_work",
            (
                "source",
                "source_id",
                "oeuvre_source",
                "oeuvre_id",
                "role_source",
                "role_normalise",
                "source_file",
            ),
        ) as t_liens,
        raw.open(encoding="utf-8") as flux,
    ):
        for ligne in flux:
            page = json.loads(ligne)
            compteurs["pages_lues"] += 1
            oeuvre_id = str(page["manga_id"])

            for element in page.get("data") or []:
                relation = (
                    (element.get("relationships") or {}).get("character") or {}
                ).get("data") or {}
                if not relation.get("id"):
                    continue
                role_source = (element.get("attributes") or {}).get("role")
                t_liens.ajouter(
                    (
                        SOURCE,
                        str(relation["id"]),
                        SOURCE,
                        oeuvre_id,
                        role_source,
                        ROLES.get(role_source),
                        nom_fichier,
                    )
                )
                compteurs["liens_collectes"] += 1
                if role_source and role_source not in ROLES:
                    compteurs["roles_non_mappes"] += 1

            for inclus in page.get("included") or []:
                if inclus.get("type") != "characters":
                    continue
                identifiant = str(inclus["id"])
                if identifiant in deja_vus:
                    continue
                deja_vus.add(identifiant)
                attributs = inclus.get("attributes") or {}

                t_perso.ajouter(
                    (
                        SOURCE,
                        identifiant,
                        (attributs.get("slug") or "").strip() or None,
                        (attributs.get("canonicalName") or "").strip(),
                        (
                            str(attributs.get("malId")).strip()
                            if attributs.get("malId") not in (None, "")
                            else None
                        ),
                        date_iso(attributs.get("createdAt")),
                        date_iso(attributs.get("updatedAt")),
                        nom_fichier,
                    )
                )
                compteurs["personnages_distincts"] += 1

                for forme, type_forme, langue in formes_de(attributs):
                    t_formes.ajouter(
                        (
                            SOURCE,
                            identifiant,
                            forme,
                            normaliser(forme),
                            type_forme,
                            langue,
                            nom_fichier,
                        )
                    )
                    compteurs[f"formes_{type_forme}"] += 1

                texte = (attributs.get("description") or "").strip()
                if texte:
                    licence, provenance = licence_de(texte)
                    t_desc.ajouter(
                        (
                            SOURCE,
                            identifiant,
                            langue_de(texte),
                            texte,
                            licence,
                            provenance,
                            nom_fichier,
                        )
                    )
                    compteurs["descriptions_chargees"] += 1
    return compteurs


# La promotion vit dans `commun.chargement` : ses quatre requetes ne portent
# rien de propre a Kitsu. Elles joignent le staging au referentiel par
# (source, source_id), et c'est precisement ce que le schema multi-sources
# devait rendre possible — une source de plus est une INSERTION, pas une
# migration ni un second jeu de requetes.


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--dsn", default=os.environ.get("APIMANGA_DSN"))
    analyseur.add_argument("--raw", type=Path, default=RAW_DEFAUT)
    analyseur.add_argument(
        "--staging-seul",
        action="store_true",
        help="s'arreter apres l'atterrissage, sans promouvoir",
    )
    args = analyseur.parse_args(argv)

    if not args.dsn:
        print("APIMANGA_DSN non defini (ou --dsn absent).", file=sys.stderr)
        return 2
    if not args.raw.exists():
        print(f"raw introuvable : {args.raw}", file=sys.stderr)
        return 2

    with psycopg.connect(args.dsn) as connexion:
        compteurs = remplir_staging(connexion, args.raw)
        for nom, valeur in sorted(compteurs.items()):
            print(f"  staging  {nom:26} {valeur}")
        if args.staging_seul:
            connexion.commit()
            print("promotion non demandee (--staging-seul).")
            return 0
        inseres = promouvoir(connexion)
        connexion.commit()
        for table, nombre in inseres.items():
            print(f"  promu    {table:30} {nombre}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as erreur:  # sortie non nulle, et affichee
        print(f"ECHEC : {type(erreur).__name__}: {erreur}", file=sys.stderr)
        sys.exit(1)
