"""Chargement du snapshot Manga Sanctuary : JSONL -> staging -> manga.*.

    uv run python -m identity.charger_ms
    uv run python -m identity.charger_ms --snapshot chemin/vers/2026-07

Quatre temps, dans cet ordre :
  1. TRUNCATE du staging, puis COPY en flux des deux JSONL ;
  2. promotion SQL (sql/promotion_ms.sql) : upsert des trois tables manga.* ;
  3. peuplement de manga.ms_formes (normalisation Python) ;
  4. peuplement de manga.volume_identity (validation EAN-13 Python).

Le raw est en LECTURE SEULE : il est la mémoire du pipeline. Le staging est
jetable — tronqué à chaque cycle. Rien n'est jamais supprimé de manga.*.

Les fichiers sont lus LIGNE À LIGNE (315 Mo pour les volumes) : jamais
`read()`, jamais `json.load()` sur le fichier entier. Le DSN vient de
DATABASE_URL ; aucun identifiant, aucun chemin absolu de poste ne figure ici.
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

from identity.dates import iso_ou_none
from identity.ean import isbn13_ou_none
from identity.wikidata_dump import normaliser

# Le dépôt est la racine ; le défaut est relatif, jamais un /mnt/c/... en dur.
RACINE = Path(__file__).resolve().parents[3]
SNAPSHOT_DEFAUT = RACINE / "04_scraping_manga_sanctuary/data/raw/2026-07"
PROMOTION_SQL = Path(__file__).resolve().parent / "sql" / "promotion_ms.sql"

LOT = 5_000

# Ordre EXACT des colonnes de staging.ms_volumes (004), hors techniques.
COLONNES_VOLUMES = [
    "series_id",
    "series_url",
    "series_title",
    "series_type",
    "series_category",
    "series_year",
    "series_other_titles",
    "series_dessinateur",
    "series_scenariste",
    "series_genres",
    "series_tags",
    "series_mag_prepub",
    "series_statuses",
    "series_popularity_rank",
    "series_members_rating",
    "series_members_votes",
    "series_experts_rating",
    "series_experts_votes",
    "series_synopsis",
    "series_related_works",
    "volume_url",
    "volume_title",
    "volume_number",
    "volume_publication_date",
    "volume_dessinateur",
    "volume_scenariste",
    "volume_editeur",
    "volume_ean",
    "volume_format",
    "volume_pages",
    "volume_country",
    "volume_status",
    "volume_tomes_published",
    "volume_tomes_total",
    "volume_members_rating",
    "volume_members_votes",
    "volume_experts_rating",
    "volume_experts_votes",
    "volume_synopsis",
]

COLONNES_REVIEWS = [
    "series_id",
    "series_title",
    "series_url",
    "volume_number",
    "volume_url",
    "review_url",
    "review_title",
    "review_score",
    "review_author",
    "review_date",
    "review_type",
    "review_body",
]

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


def texte(valeur: object) -> str | None:
    """Valeur JSON -> TEXT de staging. Une liste reste du JSON, pas un repr()."""
    if valeur is None:
        return None
    if isinstance(valeur, (list, dict)):
        return json.dumps(valeur, ensure_ascii=False)
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    return str(valeur)


def lire_jsonl(chemin: Path) -> Iterator[tuple[int, dict]]:
    """Flux (numéro de ligne, objet). Une ligne illisible arrête le chargement.

    Le profilage annonce 0 ligne non parsable sur les deux fichiers : une ligne
    cassée serait donc un fait NOUVEAU — corruption, troncature, mauvais
    fichier. La sauter « juste celle-là » perdrait l'information ; on s'arrête.
    """
    with chemin.open(encoding="utf-8") as fh:
        for numero, ligne in enumerate(fh, start=1):
            if not ligne.strip():
                continue
            try:
                yield numero, json.loads(ligne)
            except json.JSONDecodeError as erreur:
                raise ErreurChargement(
                    f"{chemin.name}, ligne {numero} : JSON illisible ({erreur}).\n"
                    "Aucune ligne n'est écartée en silence : le fichier attendu "
                    "n'a aucune ligne cassée, donc celle-ci est une anomalie à "
                    "regarder avant de recharger."
                ) from erreur


def copier(connexion, table: str, colonnes: list[str], lignes: Iterator[list]) -> int:
    """COPY par flux : les lignes ne sont jamais toutes en mémoire."""
    cibles = [*colonnes, "source_file"]
    if table == "staging.ms_volumes":
        cibles.insert(-1, "volume_publication_date_iso")
    else:
        cibles.insert(-1, "review_date_iso")
    liste = ", ".join(cibles)
    total = 0
    with connexion.cursor() as curseur:
        with curseur.copy(f"COPY {table} ({liste}) FROM STDIN") as copie:
            for ligne in lignes:
                copie.write_row(ligne)
                total += 1
    return total


def charger_volumes(connexion, chemin: Path) -> int:
    def lignes():
        for _, objet in lire_jsonl(chemin):
            valeurs = [texte(objet.get(c)) for c in COLONNES_VOLUMES]
            valeurs.append(iso_ou_none(objet.get("volume_publication_date")))
            valeurs.append(chemin.name)
            yield valeurs

    return copier(connexion, "staging.ms_volumes", COLONNES_VOLUMES, lignes())


def charger_reviews(connexion, chemin: Path) -> int:
    def lignes():
        for _, objet in lire_jsonl(chemin):
            valeurs = [texte(objet.get(c)) for c in COLONNES_REVIEWS]
            valeurs.append(iso_ou_none(objet.get("review_date")))
            valeurs.append(chemin.name)
            yield valeurs

    return copier(connexion, "staging.ms_reviews", COLONNES_REVIEWS, lignes())


def peupler_formes(connexion) -> dict[str, int]:
    """manga.ms_formes : UNE ligne 'title' par série, plus ses alias.

    forme_norm vient de `normaliser()`, l'unique source de vérité — d'où ce
    passage en Python plutôt qu'un INSERT SELECT.

    LES SÉRIES SONT RENOMMÉES. 33 l'ont été entre 2025-12 et 2026-07 : des
    corrections de coquille (« Daphne in the Brillant Blue » -> « Brilliant »),
    des changements de titre commercial (« Réincarnations - Please Save my
    Earth » -> « Please save my Earth »). Un simple INSERT laisserait l'ancien
    titre en 'title' à côté du nouveau : deux titres principaux pour une série,
    et une cascade qui lit forme_type='title' aurait deux candidats sans savoir
    lequel fait foi.

    D'où les trois temps, pour les séries de ce chargement seulement :
      1. les 'title' existants sont RÉTROGRADÉS en 'alias' — pas supprimés :
         un nom que l'œuvre a porté reste une cible de rapprochement valable,
         et c'est très exactement ce qu'est un alias ;
      2. le titre courant est posé en 'title' — ou PROMU s'il était déjà là en
         alias (le ON CONFLICT tombe sur l'UNIQUE (series_id, forme_norm,
         source)) ;
      3. les alias sont insérés en DO NOTHING : en collision normalisée avec le
         titre, le titre reste titre.
    Rejouable : rétrograder puis re-promouvoir le même titre ne change rien.
    """
    with connexion.cursor() as curseur:
        curseur.execute(
            "SELECT series_id, series_title, series_other_titles "
            "FROM manga.ms_series_enriched WHERE series_id IN "
            "(SELECT DISTINCT series_id::bigint FROM staging.ms_volumes "
            " WHERE NULLIF(series_id, '') IS NOT NULL)"
        )
        series = curseur.fetchall()

    concernees = [s[0] for s in series]
    titles, alias = [], []
    for series_id, titre, autres in series:
        if titre and titre.strip():
            forme_norm = normaliser(titre)
            if forme_norm:
                titles.append((series_id, titre, forme_norm))
        for autre in autres or []:
            if not isinstance(autre, str) or not autre.strip():
                continue
            forme_norm = normaliser(autre)
            if forme_norm:
                alias.append((series_id, autre, forme_norm))

    poses: dict[str, int] = {
        "title_candidats": len(titles),
        "alias_candidats": len(alias),
    }
    with connexion.cursor() as curseur:
        avant = _compter(curseur, "manga.ms_formes")

        # 1. Rétrogradation : l'ancien titre devient un alias, il ne disparaît pas.
        curseur.execute(
            "UPDATE manga.ms_formes SET forme_type = 'alias' "
            "WHERE source = 'ms' AND forme_type = 'title' "
            "AND series_id = ANY(%s)",
            (concernees,),
        )
        poses["retrogrades"] = curseur.rowcount

        # 2. Le titre courant : posé, ou promu s'il existait déjà en alias.
        curseur.executemany(
            "INSERT INTO manga.ms_formes (series_id, forme, forme_norm, forme_type) "
            "VALUES (%s, %s, %s, 'title') "
            "ON CONFLICT (series_id, forme_norm, source) DO UPDATE SET "
            "  forme_type = 'title', forme = EXCLUDED.forme",
            titles,
        )
        apres_titles = _compter(curseur, "manga.ms_formes")
        poses["title_nouveaux"] = apres_titles - avant

        # 3. Les alias : le titre prime en cas de collision normalisée.
        curseur.executemany(
            "INSERT INTO manga.ms_formes (series_id, forme, forme_norm, forme_type) "
            "VALUES (%s, %s, %s, 'alias') "
            "ON CONFLICT (series_id, forme_norm, source) DO NOTHING",
            alias,
        )
        poses["alias_nouveaux"] = _compter(curseur, "manga.ms_formes") - apres_titles
        curseur.execute(
            "SELECT count(*) FILTER (WHERE forme_type = 'title'), "
            "       count(*) FILTER (WHERE forme_type = 'alias') "
            "FROM manga.ms_formes"
        )
        poses["title"], poses["alias"] = curseur.fetchone()
    return poses


def peupler_volume_identity(connexion) -> dict[str, int]:
    """manga.volume_identity : une ligne par volume, isbn13 si l'EAN est valide.

    work_uid reste NULL : seule la cascade (étape C) a le droit de le poser.
    L'EAN brut, lui, ne bouge pas de ms_volumes_enriched.
    """
    with connexion.cursor() as curseur:
        curseur.execute("SELECT volume_url, volume_ean FROM manga.ms_volumes_enriched")
        volumes = curseur.fetchall()

    lignes = []
    for volume_url, ean in volumes:
        isbn13 = isbn13_ou_none(ean)
        # isbn13_valide distingue « pas d'EAN du tout » (NULL) de « EAN présent
        # mais faux » (False) : sans quoi les deux se confondraient en NULL.
        valide = None if not (ean and ean.strip()) else isbn13 is not None
        lignes.append((volume_url, isbn13, valide))

    with connexion.cursor() as curseur:
        avant = _compter(curseur, "manga.volume_identity")
        curseur.executemany(
            "INSERT INTO manga.volume_identity (volume_url, isbn13, isbn13_valide) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (volume_url) DO UPDATE SET "
            "  isbn13 = EXCLUDED.isbn13, "
            "  isbn13_valide = EXCLUDED.isbn13_valide, "
            "  updated_at = now()",
            lignes,
        )
        apres = _compter(curseur, "manga.volume_identity")
    return {"traites": len(lignes), "nouveaux": apres - avant, "total": apres}


def _compter(curseur, table: str) -> int:
    curseur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 — table littérale
    return curseur.fetchone()[0]


@app.command()
def charger(
    # noqa: B008 — `typer.Option()` en défaut est l'API de Typer, pas un oubli :
    # c'est ainsi que la bibliothèque déclare une option de CLI.
    snapshot: Path = typer.Option(  # noqa: B008
        SNAPSHOT_DEFAUT, help="Dossier du snapshot (lecture seule)."
    ),
    promouvoir: bool = typer.Option(  # noqa: B008
        True, help="Enchaîner la promotion vers manga.* après le staging."
    ),
) -> None:
    """Charge le snapshot en staging, puis promeut vers manga.*."""
    volumes = snapshot / "manga_sanctuary_volumes.jsonl"
    reviews = snapshot / "manga_sanctuary_reviews.jsonl"
    for fichier in (volumes, reviews):
        if not fichier.is_file():
            raise ErreurChargement(f"Fichier introuvable : {fichier}")

    debut = time.monotonic()
    # autocommit=False : le chargement entier est UNE transaction. Un échec en
    # promotion ne doit pas laisser un staging à moitié rempli et des tables
    # manga.* à moitié à jour.
    with psycopg.connect(dsn()) as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("TRUNCATE staging.ms_volumes, staging.ms_reviews")
        typer.echo("→ staging tronqué")

        n_volumes = charger_volumes(connexion, volumes)
        typer.echo(f"  ✓ staging.ms_volumes : {n_volumes} lignes")
        n_reviews = charger_reviews(connexion, reviews)
        typer.echo(f"  ✓ staging.ms_reviews : {n_reviews} lignes")

        if promouvoir:
            typer.echo("→ promotion vers manga.*")
            with connexion.cursor() as curseur:
                curseur.execute(PROMOTION_SQL.read_text(encoding="utf-8"))
            typer.echo("  ✓ séries, volumes, critiques")

            formes = peupler_formes(connexion)
            typer.echo(
                f"  ✓ ms_formes : {formes['title']} titles, "
                f"{formes['alias']} alias "
                f"(+{formes['title_nouveaux']} / +{formes['alias_nouveaux']}, "
                f"{formes['retrogrades']} ex-titres rétrogradés ; "
                f"{formes['alias_candidats']} alias candidats)"
            )
            identites = peupler_volume_identity(connexion)
            typer.echo(
                f"  ✓ volume_identity : {identites['total']} lignes "
                f"({identites['nouveaux']} nouvelles)"
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
