"""Chargement de Manga Insight : parquet -> manga.mi_sorties / mi_series.

    uv run python -m identity.charger_mi
    uv run python -m identity.charger_mi --raw data/raw/mi/2026-08

Un seul parquet, DEUX populations, séparées sur « Original Url » :
  - vide   -> A, grain sortie/volume (48 900 lignes), porte l'EAN ;
  - rempli -> B, grain série (10 162 lignes), issu du crawl Manga-News.

RECHARGEMENT COMPLET, pas upsert : l'EAN n'identifie pas une sortie (3,52 % de
lignes sans EAN, 534 EAN multi-sorties dont des erreurs de la source). La table
EST le snapshot du mois ; son historique vit dans le raw daté et immuable.
La justification longue est en tête de la migration 007.

Le remplacement est protégé par un PLANCHER : si le nouveau snapshot compte
moins de 90 % des lignes déjà en base, tout est annulé. On ne remplace pas une
table saine par un fichier tronqué.

Lecture par record batch : les 59 062 lignes ne sont jamais toutes en RAM.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
import typer

from identity.dates import parser_date_souple
from identity.ean import ean13_valide

RACINE = Path(__file__).resolve().parents[3]
RAW_DEFAUT = RACINE / "05_nettoyage_agregation_bdd/data/raw/mi/2026-07"

LOT = 5_000
PLANCHER = 0.90

# Colonne du parquet -> colonne SQL. Repris de 002 : minuscules, accents
# retirés, séparateurs -> « _ », préfixe « _ » -> « meta_ ».
COMMUNES = {
    "Titre VO": "titre_vo",
    "Éditeur VF": "editeur_vf",
    "Éditeur VO": "editeur_vo",
    "Type": "type",
    "Genre 1": "genre_1",
    "Genre 2": "genre_2",
    "Statut VF": "statut_vf",
    "Statut VO": "statut_vo",
    "Pays": "pays",
    "Année pays d'origine": "annee_pays_d_origine",
    "Date sortie France - année": "date_sortie_france_annee",
    "Date sortie France - mois": "date_sortie_france_mois",
    "Tomes VF": "tomes_vf",
    "Tomes VO": "tomes_vo",
    "_catégorie": "meta_categorie",
    "_fichier": "meta_fichier",
    "_nouveauté": "meta_nouveaute",
    "_nouvelle_édition": "meta_nouvelle_edition",
    "_coffret": "meta_coffret",
    "_collector": "meta_collector",
    "_type_titre": "meta_type_titre",
    "_type_source": "meta_type_source",
    "_doublon_éditeur": "meta_doublon_editeur",
    "_éditeurs_doublons": "meta_editeurs_doublons",
}
SORTIES = {
    **COMMUNES,
    "Titre": "titre",
    "Ean": "ean",
    "Unnamed: 0": "unnamed_0",
    "_année_fichier": "meta_annee_fichier",
    "_mois_fichier": "meta_mois_fichier",
    "Dessin": "dessin",
    "Scénario": "scenario",
}
SERIES = {
    **COMMUNES,
    "Original Url": "original_url",
    "Adresse": "adresse",
    "Adresse.1": "adresse_1",
    "Code HTTP": "code_http",
    "Title": "title",
    "Titre traduit": "titre_traduit",
    "Prépublication": "prepublication",
    "Nombre tomes VF": "nombre_tomes_vf",
    "Nombre tomes VO": "nombre_tomes_vo",
    "Année": "annee",
}

ENTIERS = {
    "annee_pays_d_origine",
    "date_sortie_france_annee",
    "date_sortie_france_mois",
    "tomes_vf",
    "tomes_vo",
    "annee",
    "code_http",
}
BOOLEENS = {
    "meta_nouveaute",
    "meta_nouvelle_edition",
    "meta_coffret",
    "meta_collector",
    "meta_doublon_editeur",
}

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


def _afficher(chemin: Path) -> str:
    """Chemin relatif au dépôt quand c'en est un, absolu sinon : un raw peut
    légitimement vivre ailleurs (un disque externe, un dossier de test)."""
    try:
        return str(chemin.relative_to(RACINE))
    except ValueError:
        return str(chemin)


def vide(valeur: object) -> bool:
    return valeur is None or (isinstance(valeur, str) and not valeur.strip())


def texte(valeur: object) -> str | None:
    return None if vide(valeur) else str(valeur).strip()


def entier(valeur: object) -> int | None:
    """Le parquet type ces colonnes en int64/double : « 2025.0 » doit rendre
    2025, pas échouer."""
    if vide(valeur):
        return None
    try:
        return int(float(valeur))
    except (TypeError, ValueError):
        return None


def booleen(valeur: object) -> bool | None:
    return None if valeur is None else bool(valeur)


def convertir(nom_sql: str, valeur: object):
    if nom_sql in ENTIERS:
        return entier(valeur)
    if nom_sql in BOOLEENS:
        return booleen(valeur)
    return texte(valeur)


def resoudre_colonnes(reelles: list[str], attendues: dict[str, str]) -> dict[str, str]:
    """Nom attendu -> nom RÉEL dans le fichier, apparié sur le nom détouré.

    Le fichier porte « Prépublication » avec une espace finale — un artefact
    d'export tableur, invisible à l'affichage et sans conséquence si l'on
    apparie sur le nom détouré. C'est la seule colonne concernée aujourd'hui ;
    apparier ainsi évite d'écrire l'espace en dur et survivra au jour où la
    source la corrigera.

    Une colonne attendue et introuvable arrête tout : c'est un changement de
    schéma, et le charger à moitié le masquerait.
    """
    par_detoure = {nom.strip(): nom for nom in reelles}
    resolues = {}
    manquantes = []
    for attendue in attendues:
        reelle = par_detoure.get(attendue.strip())
        if reelle is None:
            manquantes.append(attendue)
        else:
            resolues[attendue] = reelle
    if manquantes:
        raise ErreurChargement(
            f"Colonnes absentes du parquet : {manquantes}.\n"
            "Le schéma de la source a changé : re-profiler avant de recharger."
        )
    return resolues


def lignes_du_parquet(chemin: Path, table: str) -> Iterator[tuple[list[str], list]]:
    """Flux (colonnes, valeurs) pour la population demandée."""
    correspondance = SORTIES if table == "sorties" else SERIES
    colonnes_sql = [*correspondance.values()]
    if table == "sorties":
        colonnes_sql += ["ean_valide"]
    colonnes_sql += ["date_sortie_france", "date_sortie_france_raw", "source_file"]

    fichier = pq.ParquetFile(chemin)
    reelles = resoudre_colonnes(
        list(fichier.schema_arrow.names),
        {**correspondance, "Original Url": "", "Ean": "", "Date sortie France": ""},
    )
    for lot in fichier.iter_batches(batch_size=LOT):
        donnees = lot.to_pydict()
        for i in range(lot.num_rows):
            url = donnees[reelles["Original Url"]][i]
            population = "sorties" if vide(url) else "series"
            if population != table:
                continue
            valeurs = [
                convertir(sql, donnees[reelles[parquet]][i])
                for parquet, sql in correspondance.items()
            ]
            if table == "sorties":
                ean = texte(donnees[reelles["Ean"]][i])
                # NULL si pas d'EAN, False si EAN présent mais faux : les deux
                # états ne doivent pas se confondre (même geste qu'en B2).
                valeurs.append(None if ean is None else ean13_valide(ean))
            brute = donnees[reelles["Date sortie France"]][i]
            parsee = parser_date_souple(brute)
            valeurs += [parsee, texte(brute), chemin.name]
            yield colonnes_sql, valeurs


def compter(curseur, table: str) -> int:
    curseur.execute(f"SELECT count(*) FROM manga.{table}")  # noqa: S608
    return curseur.fetchone()[0]


def charger_population(connexion, chemin: Path, table: str) -> dict[str, int]:
    """Remplace le contenu de la table par la population du snapshot.

    DELETE puis INSERT dans la transaction du chargement : à aucun moment un
    lecteur ne voit une table à moitié remplie, et un échec la laisse intacte.
    """
    with connexion.cursor() as curseur:
        avant = compter(curseur, f"mi_{table}")

        flux = lignes_du_parquet(chemin, table)
        premiere = next(flux, None)
        if premiere is None:
            raise ErreurChargement(
                f"Population « {table} » vide dans {chemin.name} : le fichier "
                "n'est pas celui attendu."
            )
        colonnes, _ = premiere

        curseur.execute(f"DELETE FROM manga.mi_{table}")  # noqa: S608
        liste = ", ".join(colonnes)
        total = 0
        with curseur.copy(
            f"COPY manga.mi_{table} ({liste}) FROM STDIN"  # noqa: S608
        ) as copie:
            copie.write_row(premiere[1])
            total += 1
            for _, valeurs in flux:
                copie.write_row(valeurs)
                total += 1

        # Le plancher : un fichier tronqué ne doit pas remplacer une table
        # saine. Vérifié APRÈS le chargement mais AVANT le commit — la
        # transaction est là pour ça.
        if avant > 0 and total < avant * PLANCHER:
            raise ErreurChargement(
                f"PLANCHER DE VOLUMÉTRIE : manga.mi_{table} contenait {avant} "
                f"lignes, le snapshot n'en apporte que {total} "
                f"({100 * total / avant:.1f} %, seuil {PLANCHER:.0%}).\n"
                "Chargement ANNULÉ : on ne remplace pas une table saine par un "
                "fichier tronqué. Vérifier le raw avant de recommencer."
            )
        return {"avant": avant, "apres": total}


@app.command()
def charger(
    raw: Path = typer.Option(  # noqa: B008
        RAW_DEFAUT, help="Dossier du raw daté (lecture seule)."
    ),
) -> None:
    """Recharge Manga Insight depuis un raw daté."""
    chemin = raw / "data.parquet"
    if not chemin.is_file():
        raise ErreurChargement(
            f"Parquet introuvable : {chemin}\n"
            "L'acquérir d'abord : uv run python -m identity.acquerir_mi"
        )
    if not (raw / "MANIFEST.md").is_file():
        raise ErreurChargement(
            f"MANIFEST.md absent de {raw} : un raw sans provenance ne se charge "
            "pas. Utiliser identity.acquerir_mi, qui l'écrit."
        )

    debut = time.monotonic()
    typer.echo(f"→ {_afficher(chemin)}")
    # Une seule transaction pour les deux populations : elles viennent du même
    # fichier, elles entrent ou n'entrent pas ensemble.
    with psycopg.connect(dsn()) as connexion:
        for table in ("sorties", "series"):
            mesures = charger_population(connexion, chemin, table)
            typer.echo(
                f"  ✓ mi_{table} : {mesures['apres']} lignes "
                f"(remplaçait {mesures['avant']})"
            )
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT count(*), count(*) FILTER (WHERE ean_valide), "
                "       count(*) FILTER (WHERE ean_valide = false), "
                "       count(*) FILTER (WHERE ean IS NULL) "
                "FROM manga.mi_sorties"
            )
            total, valides, faux, sans = curseur.fetchone()
            curseur.execute("SELECT count(*) FROM manga.v_mi_ean_multiples")
            multiples = curseur.fetchone()[0]
        typer.echo(
            f"  ✓ EAN : {valides} valides, {faux} faux, {sans} absents "
            f"(sur {total}) ; {multiples} EAN portent plusieurs sorties"
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
