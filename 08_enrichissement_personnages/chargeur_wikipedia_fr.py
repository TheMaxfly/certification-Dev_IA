#!/usr/bin/env python3
"""Chargement des personnages Wikipedia francais — meme schema, aucune migration.

**C'est le test du pari de la migration 015.** Si une deuxieme source entre dans
les memes quatre tables par simple insertion, le choix des noms neutres et de la
colonne `source` etait juste. Si une colonne manque, il ne l'etait pas — et mieux
vaut le decouvrir sur la deuxieme source que sur la troisieme.

CE QUI DIFFERE DE KITSU, ET POURQUOI

  LANGUE  `fr` **affirmee**, jamais inferee. L'article vient de
          `fr.wikipedia.org` : c'est un fait de provenance, pas une supposition
          sur le texte. On n'infere que ce que la source ne dit pas — la regle B
          reste cantonnee a Kitsu, agregateur multilingue qui ne declare rien.
          Mesure a l'appui : appliquee ici, elle rendrait 98,5 % de NULL sur un
          corpus a 97,0 % de marqueurs francais, et 131 faux positifs `en` sur
          du texte francais.

  ROLE    **NULL partout.** Wikipedia ne porte aucun vocabulaire de roles, mais
          une POSITION dans la section. Ce n'est pas un chargement incomplet,
          c'est ce que la source contient.

  CLE     Un personnage Wikipedia n'a pas d'identifiant. La cle est le couple
          (article source, nom normalise) : stable d'un rechargement a l'autre,
          et elle place le personnage sous l'ARTICLE dont le texte vient — non
          sous la serie. C'est ce qui absorbe le double compte : trois series
          MS resolvant vers *L'Attaque des Titans* donnent UN personnage et
          TROIS liens.

  SOURCE  L'article effectif est celui du renvoi quand le renvoi l'emporte. Une
          attribution CC BY-SA qui nommerait l'article de serie alors que le
          texte vient de la liste dediee serait fausse.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

import psycopg

from commun.chargement import TamponCopy, normaliser, promouvoir

SOURCE = "wikipedia_fr"
#: Le referentiel d'oeuvres est ici Manga Sanctuary, pas Kitsu : la colonne
#: `oeuvre_source` existe pour que les deux cohabitent sans se confondre.
OEUVRE_SOURCE = "manga_sanctuary"
LANGUE = "fr"
LICENCE = "CC BY-SA"

RACINE = Path(__file__).resolve().parent
RAW_DEFAUT = RACINE / "data/raw/wikipedia_fr/2026-09/wikipedia_fr_personnages.ndjson"


def article_effectif(charge: dict) -> tuple[str, int | None]:
    """Rend (titre, revid) de l'article D'OU LE TEXTE VIENT reellement."""
    if charge.get("forme_retenue") == "dedie" and charge.get("renvoi_titre_servi"):
        return charge["renvoi_titre_servi"], charge.get("renvoi_revid")
    return charge["titre_servi"], charge.get("revid")


def remplir_staging(connexion: psycopg.Connection, raw: Path) -> Counter:
    """Parcourt le raw en flux et verse les quatre zones d'atterrissage."""
    compteurs: Counter = Counter()
    with connexion.cursor() as curseur:
        curseur.execute(
            "TRUNCATE staging.characters, staging.character_forms, "
            "staging.character_descriptions, staging.character_work"
        )

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
            enveloppe = json.loads(ligne)
            charge = enveloppe["charge"]
            compteurs["articles_traites"] += 1
            article, revid = article_effectif(charge)
            attribution = f"{article} (rev. {revid})" if revid else article

            for personnage in charge["personnages"]:
                nom = personnage["nom"].strip()
                if not nom:
                    compteurs["noms_vides_ecartes"] += 1
                    continue
                identifiant = f"{article}|{normaliser(nom)}"
                compteurs["liens_collectes"] += 1

                # Le lien porte l'identifiant catalogue de la serie, jamais le
                # titre de l'article : la jointure reste native.
                t_liens.ajouter(
                    (
                        SOURCE,
                        identifiant,
                        OEUVRE_SOURCE,
                        str(enveloppe["series_id"]),
                        None,
                        None,
                        nom_fichier,
                    )
                )

                if identifiant in deja_vus:
                    continue
                deja_vus.add(identifiant)
                compteurs["personnages_distincts"] += 1

                t_perso.ajouter(
                    (
                        SOURCE,
                        identifiant,
                        None,
                        nom,
                        None,
                        None,
                        None,
                        nom_fichier,
                    )
                )
                t_formes.ajouter(
                    (
                        SOURCE,
                        identifiant,
                        nom,
                        normaliser(nom),
                        "canonical",
                        None,
                        nom_fichier,
                    )
                )
                compteurs["formes_canonical"] += 1

                japonaise = (personnage.get("forme_japonaise") or "").strip()
                if japonaise:
                    # §4 : elle entre en `name_lang`, jamais en alias — c'est
                    # elle que sert l'index partiel de la migration 015.
                    t_formes.ajouter(
                        (
                            SOURCE,
                            identifiant,
                            japonaise,
                            normaliser(japonaise),
                            "name_lang",
                            "ja",
                            nom_fichier,
                        )
                    )
                    compteurs["formes_japonaises"] += 1

                texte = personnage["description"].strip()
                if texte:
                    t_desc.ajouter(
                        (
                            SOURCE,
                            identifiant,
                            LANGUE,
                            texte,
                            LICENCE,
                            attribution,
                            nom_fichier,
                        )
                    )
                    compteurs["descriptions_chargees"] += 1
    return compteurs


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--dsn", default=os.environ.get("APIMANGA_DSN"))
    analyseur.add_argument("--raw", type=Path, default=RAW_DEFAUT)
    analyseur.add_argument("--staging-seul", action="store_true")
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
            return 0
        for table, nombre in promouvoir(connexion).items():
            print(f"  promu    {table:30} {nombre}")
        connexion.commit()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as erreur:
        print(f"ECHEC : {type(erreur).__name__}: {erreur}", file=sys.stderr)
        sys.exit(1)
