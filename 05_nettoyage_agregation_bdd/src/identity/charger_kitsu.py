"""Chargement du catalogue Kitsu : ndjson -> staging -> manga.kitsu_*.

    uv run python -m identity.charger_kitsu
    uv run python -m identity.charger_kitsu --run 20260714T152202Z

Deux fichiers du MÊME run, et cette contrainte n'est pas cosmétique : le filtre
subtype se lit dans `manga.ndjson` et s'applique aux mappings de
`relations/mappings.ndjson`. Croiser deux runs rapprocherait des identifiants
d'un catalogue avec les sous-types d'un autre.

Les deux fichiers sont ENCAPSULÉS par la collecte :
  - manga.ndjson    : une ligne par manga, l'objet dans `data` ;
  - mappings.ndjson : une ligne par PAGE d'API, les mappings dans `data[]`.
Lecture ligne à ligne : manga.ndjson pèse 593 Mo.

Deux filtres, appliqués à la PROMOTION et jamais au chargement :
  - subtype : CIBLE {manga, manhwa, manhua}, 41 249 des 62 768 entrées ;
  - externalSite : les sites de manga seulement.
Ce qu'ils écartent est compté, pas perdu.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import typer

from identity.wikidata_dump import normaliser

RACINE = Path(__file__).resolve().parents[3]
CATALOGUE = RACINE / "03_kitsu_api_exports/exports/full_catalog"
PROMOTION_SQL = Path(__file__).resolve().parent / "sql" / "promotion_kitsu.sql"

SUBTYPES_CIBLE = ("manga", "manhwa", "manhua")

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurChargement(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurChargement(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'"
        )
    return url


def resoudre_run(run: str | None) -> Path:
    """Le run demandé, ou celui que le fichier LATEST désigne."""
    if run is None:
        pointeur = CATALOGUE / "LATEST"
        if not pointeur.is_file():
            raise ErreurChargement(f"Ni --run ni fichier LATEST : {pointeur}")
        run = pointeur.read_text(encoding="utf-8").strip()
    dossier = CATALOGUE / run
    for fichier in (dossier / "manga.ndjson", dossier / "relations/mappings.ndjson"):
        if not fichier.is_file():
            raise ErreurChargement(f"Fichier introuvable : {fichier}")
    return dossier


def lire_ndjson(chemin: Path) -> Iterator[dict]:
    """Flux d'objets. Une ligne illisible arrête tout : le profilage annonce
    0 ligne non parsable, donc une ligne cassée est un fait nouveau."""
    with chemin.open(encoding="utf-8") as fh:
        for numero, ligne in enumerate(fh, start=1):
            if not ligne.strip():
                continue
            try:
                yield json.loads(ligne)
            except json.JSONDecodeError as erreur:
                raise ErreurChargement(
                    f"{chemin.name}, ligne {numero} : JSON illisible ({erreur}). "
                    "Aucune ligne n'est écartée en silence."
                ) from erreur


def formes_d_une_entree(attributs: dict) -> Iterator[tuple[str, str, str | None]]:
    """(forme, forme_type, langue) pour une entrée du catalogue.

    Les clés RÉELLES du ndjson, et non une liste supposée :
      canonicalTitle    -> 'canonical', sans langue
      titles{<langue>}  -> 'title', langue = la clé (ja_jp, en_jp, ko_kr,
                           zh_cn…). Ces clés sont bien plus riches que ja/en :
                           écraser la langue dans le type perdrait le coréen et
                           le chinois, soit les manhwa et manhua de la cible.
      abbreviatedTitles -> 'abbreviated', sans langue déclarée
    """
    canonique = attributs.get("canonicalTitle")
    if canonique:
        yield canonique, "canonical", None
    for langue, titre in (attributs.get("titles") or {}).items():
        if titre:
            yield titre, "title", langue
    for abrege in attributs.get("abbreviatedTitles") or []:
        if abrege:
            yield abrege, "abbreviated", None


def charger_formes(connexion, dossier: Path) -> dict[str, int]:
    """manga.kitsu_formes : la cible seulement, forme_norm par normaliser().

    L'UNIQUE (kitsu_id, forme_norm) dédoublonne : un titre canonique identique à
    son titre en_jp — le cas ordinaire pour une œuvre sans titre traduit — ne
    produit qu'une forme. Le premier type rencontré gagne, et l'ordre de
    `formes_d_une_entree` fait que c'est 'canonical'.
    """
    mesures = {
        "entrees": 0,
        "cible": 0,
        "exclues": 0,
        "formes_brutes": 0,
        "sans_norm": 0,
    }
    lignes: list[tuple] = []
    for objet in lire_ndjson(dossier / "manga.ndjson"):
        donnee = objet.get("data") or {}
        kitsu_id = donnee.get("id")
        attributs = donnee.get("attributes") or {}
        subtype = attributs.get("subtype")
        if kitsu_id is None:
            continue
        mesures["entrees"] += 1
        if subtype not in SUBTYPES_CIBLE:
            mesures["exclues"] += 1
            continue
        mesures["cible"] += 1
        for forme, forme_type, langue in formes_d_une_entree(attributs):
            mesures["formes_brutes"] += 1
            forme_norm = normaliser(forme)
            if not forme_norm:
                mesures["sans_norm"] += 1
                continue
            lignes.append(
                (int(kitsu_id), forme, forme_norm, forme_type, langue, subtype)
            )

    with connexion.cursor() as curseur:
        # Par lots : 200 000 lignes en un seul executemany tiendraient en RAM,
        # mais le lot garde une empreinte plate et un progrès observable.
        for debut in range(0, len(lignes), 10_000):
            curseur.executemany(
                "INSERT INTO manga.kitsu_formes "
                "  (kitsu_id, forme, forme_norm, forme_type, langue, subtype) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (kitsu_id, forme_norm) DO NOTHING",
                lignes[debut : debut + 10_000],
            )
        curseur.execute(
            "SELECT count(*), count(DISTINCT kitsu_id) FROM manga.kitsu_formes"
        )
        mesures["total"], mesures["couvertes"] = curseur.fetchone()
    mesures["candidates"] = len(lignes)
    return mesures


def charger_mappings(connexion, dossier: Path) -> dict[str, int]:
    """staging.kitsu_mappings (tout), puis promotion filtrée vers manga."""
    chemin = dossier / "relations/mappings.ndjson"

    def lignes() -> Iterator[list]:
        for objet in lire_ndjson(chemin):
            kitsu_id = objet.get("manga_id")
            for mapping in objet.get("data") or []:
                attributs = mapping.get("attributes") or {}
                yield [
                    str(kitsu_id) if kitsu_id is not None else None,
                    attributs.get("externalSite"),
                    attributs.get("externalId"),
                    mapping.get("id"),
                    chemin.name,
                ]

    mesures = {}
    with connexion.cursor() as curseur:
        curseur.execute("TRUNCATE staging.kitsu_mappings")
        total = 0
        with curseur.copy(
            "COPY staging.kitsu_mappings "
            "(kitsu_id, external_site, external_id, mapping_id, source_file) "
            "FROM STDIN"
        ) as copie:
            for ligne in lignes():
                copie.write_row(ligne)
                total += 1
        mesures["staging"] = total

        curseur.execute(PROMOTION_SQL.read_text(encoding="utf-8"))
        curseur.execute("SELECT count(*) FROM manga.kitsu_mappings")
        mesures["retenus"] = curseur.fetchone()[0]
        curseur.execute(
            "SELECT count(*) FROM staging.kitsu_mappings "
            "WHERE external_site NOT IN "
            "  ('myanimelist/manga', 'anilist/manga', 'mangaupdates')"
        )
        mesures["exclus_site"] = curseur.fetchone()[0]
        curseur.execute(
            "SELECT count(*) FROM staging.kitsu_mappings s "
            "WHERE s.external_site IN "
            "  ('myanimelist/manga', 'anilist/manga', 'mangaupdates') "
            "AND NOT EXISTS (SELECT 1 FROM manga.kitsu_formes f "
            "                WHERE f.kitsu_id = s.kitsu_id::bigint)"
        )
        mesures["exclus_subtype"] = curseur.fetchone()[0]
    return mesures


@app.command()
def charger(
    run: str = typer.Option(  # noqa: B008
        None, help="Run du catalogue (défaut : le fichier LATEST)."
    ),
) -> None:
    """Charge le catalogue Kitsu vers manga.kitsu_formes et kitsu_mappings."""
    dossier = resoudre_run(run)
    typer.echo(f"→ run {dossier.name}")
    debut = time.monotonic()
    with psycopg.connect(dsn()) as connexion:
        formes = charger_formes(connexion, dossier)
        typer.echo(
            f"  ✓ kitsu_formes : {formes['total']} formes sur "
            f"{formes['couvertes']} entrées "
            f"({formes['cible']} cible / {formes['entrees']} lues, "
            f"{formes['exclues']} hors subtype ; "
            f"{formes['candidates']} candidates, {formes['sans_norm']} sans norme)"
        )
        mappings = charger_mappings(connexion, dossier)
        typer.echo(
            f"  ✓ kitsu_mappings : {mappings['retenus']} retenus sur "
            f"{mappings['staging']} bruts "
            f"({mappings['exclus_site']} hors site, "
            f"{mappings['exclus_subtype']} hors subtype)"
        )
        connexion.commit()
    typer.echo(f"Terminé en {time.monotonic() - debut:.1f} s.")


def main() -> int:
    try:
        app()
    except ErreurChargement as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
