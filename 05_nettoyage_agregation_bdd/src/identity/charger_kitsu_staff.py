"""Chargement du staff et de la méta Kitsu : ndjson -> manga.kitsu_staff/meta.

    uv run python -m identity.charger_kitsu_staff
    uv run python -m identity.charger_kitsu_staff --run 20260714T152202Z
    uv run python -m identity.charger_kitsu_staff --dry-run

Deux fichiers du MÊME run, et la contrainte n'est pas cosmétique : le subtype
et l'année se lisent dans `manga.ndjson`, le staff dans
`relations/staff.ndjson`. Croiser deux runs rapprocherait des auteurs d'un
catalogue avec les sous-types d'un autre.

CE QUE CE CHARGEUR APPORTE À LA CASCADE. L'étage 1 a laissé 665 dossiers en
needs_review faute de confirmateur : Wikidata ne porte l'année qu'à 41,0 %.
Kitsu porte startDate à 99,9 % sur la cible et 53 183 lignes de staff dont
100 % de noms résolvables — mesuré, pas supposé.

STRUCTURE RÉELLE DU STAFF, vérifiée avant d'écrire. Le nom NE VIT PAS dans
`data[]` : celui-ci ne porte que le rôle et un pointeur `relationships.person`.
Le nom est dans `included[]`, sur les objets de type 'people'. La jointure est
INTERNE au fichier, résolue ici, enveloppe par enveloppe — jamais entre deux
enveloppes, car rien ne garantit qu'une personne citée dans l'une soit incluse
dans l'autre.

Deux écritures, deux politiques :
  - kitsu_meta  : upsert par kitsu_id (recalcul systématique, comme wd_*) ;
  - kitsu_staff : staging tout-TEXT non filtré, puis promotion filtrée.
Idempotent des deux côtés : un rechargement n'ajoute aucune ligne.
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
PROMOTION_SQL = Path(__file__).resolve().parent / "sql" / "promotion_kitsu_staff.sql"

SUBTYPES_CIBLE = ("manga", "manhwa", "manhua")

# Garde-fou d'extraction d'année : startDate est une date ISO, mais une valeur
# aberrante ne doit pas devenir une année. Mesuré sur le run : 1 seul cas.
ANNEE_MIN, ANNEE_MAX = 1900, 2100

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


def verifier_prerequis(cur) -> None:
    """Le schéma doit préexister : ce chargeur ne crée aucune structure."""
    manquants = []
    for objet in ("staging.kitsu_staff", "manga.kitsu_staff", "manga.kitsu_meta"):
        cur.execute("SELECT to_regclass(%s)", (objet,))
        if cur.fetchone()[0] is None:
            manquants.append(objet)
    if manquants:
        raise ErreurChargement(
            "Schéma incomplet — la migration 009 n'est pas appliquée. STOP. "
            "Absents : " + ", ".join(manquants)
        )


def resoudre_run(run: str | None) -> Path:
    """Le run demandé, ou celui que le fichier LATEST désigne."""
    if run is None:
        pointeur = CATALOGUE / "LATEST"
        if not pointeur.is_file():
            raise ErreurChargement(f"Ni --run ni fichier LATEST : {pointeur}")
        run = pointeur.read_text(encoding="utf-8").strip()
    dossier = CATALOGUE / run
    for fichier in (dossier / "manga.ndjson", dossier / "relations/staff.ndjson"):
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


def annee_de(start_date: str | None) -> int | None:
    """L'année de startDate ('YYYY-MM-DD'), ou None.

    Extraction en Python et non par un cast SQL : 44 entrées de la cible ont
    startDate NULL et une porte une valeur hors plage. Elles arrivent en base
    honnêtement vides plutôt que fausses.
    """
    if not start_date:
        return None
    try:
        annee = int(str(start_date)[:4])
    except (ValueError, TypeError):
        return None
    return annee if ANNEE_MIN <= annee <= ANNEE_MAX else None


# --------------------------------------------------------------------------- #
# 1. kitsu_meta — l'année et le subtype, grain œuvre
# --------------------------------------------------------------------------- #
def charger_meta(connexion, dossier: Path) -> dict[str, int]:
    """manga.kitsu_meta : la cible seulement, année extraite en Python."""
    mesures = {"entrees": 0, "cible": 0, "exclues": 0, "sans_annee": 0}
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
        annee = annee_de(attributs.get("startDate"))
        if annee is None:
            mesures["sans_annee"] += 1
        lignes.append((int(kitsu_id), annee, subtype))

    with connexion.cursor() as curseur:
        for debut in range(0, len(lignes), 10_000):
            curseur.executemany(
                "INSERT INTO manga.kitsu_meta (kitsu_id, annee, subtype) "
                "VALUES (%s, %s, %s) "
                # Recalcul systématique : au cycle mensuel, une année corrigée
                # à la source doit se propager. Le grain est stable (kitsu_id).
                "ON CONFLICT (kitsu_id) DO UPDATE SET "
                "  annee = EXCLUDED.annee, subtype = EXCLUDED.subtype, "
                "  loaded_at = now()",
                lignes[debut : debut + 10_000],
            )
        curseur.execute("SELECT count(*), count(annee) FROM manga.kitsu_meta")
        mesures["total"], mesures["avec_annee"] = curseur.fetchone()
    return mesures


# --------------------------------------------------------------------------- #
# 2. kitsu_staff — staging fidèle, puis promotion filtrée
# --------------------------------------------------------------------------- #
def lignes_staff(chemin: Path) -> Iterator[tuple[list, str | None]]:
    """(ligne de staging, nom résolu) pour chaque crédit du fichier.

    La résolution data[] -> included[] se fait DANS l'enveloppe : `included`
    est le contexte de la page, pas un index global.
    """
    for objet in lire_ndjson(chemin):
        noms = {
            inclus.get("id"): (inclus.get("attributes") or {}).get("name")
            for inclus in (objet.get("included") or [])
            if inclus.get("type") == "people"
        }
        for credit in objet.get("data") or []:
            attributs = credit.get("attributes") or {}
            personne_ref = (
                (credit.get("relationships") or {}).get("person") or {}
            ).get("data") or {}
            personne_id = personne_ref.get("id")
            nom = noms.get(personne_id)
            yield (
                [
                    str(objet.get("manga_id")) if objet.get("manga_id") else None,
                    personne_id,
                    nom,
                    attributs.get("role"),
                    credit.get("id"),
                    chemin.name,
                ],
                nom,
            )


def charger_staff(connexion, dossier: Path) -> dict[str, int]:
    """staging.kitsu_staff (tout), puis promotion filtrée vers manga."""
    chemin = dossier / "relations/staff.ndjson"
    mesures: dict[str, int] = {"credits": 0, "sans_nom": 0}
    noms_distincts: set[str] = set()

    with connexion.cursor() as curseur:
        curseur.execute("TRUNCATE staging.kitsu_staff")
        with curseur.copy(
            "COPY staging.kitsu_staff "
            "(kitsu_id, personne_id, personne, role, staff_id, source_file) "
            "FROM STDIN"
        ) as copie:
            for ligne, nom in lignes_staff(chemin):
                copie.write_row(ligne)
                mesures["credits"] += 1
                if nom:
                    noms_distincts.add(nom)
                else:
                    mesures["sans_nom"] += 1
        mesures["staging"] = mesures["credits"]
        mesures["noms_distincts"] = len(noms_distincts)

        # UNE SEULE normalisation : la fonction Python, jamais du SQL. La table
        # temporaire porte le résultat jusqu'à la promotion.
        curseur.execute(
            "CREATE TEMP TABLE staff_norm "
            "(personne text PRIMARY KEY, personne_norm text) ON COMMIT DROP"
        )
        vides = 0
        with curseur.copy(
            "COPY staff_norm (personne, personne_norm) FROM STDIN"
        ) as copie:
            for nom in sorted(noms_distincts):
                norme = normaliser(nom)
                if not norme:
                    vides += 1
                copie.write_row([nom, norme])
        mesures["sans_norme"] = vides
        curseur.execute("ANALYZE staff_norm")

        curseur.execute(PROMOTION_SQL.read_text(encoding="utf-8"))
        curseur.execute(
            "SELECT count(*), count(DISTINCT kitsu_id), "
            "       count(DISTINCT personne_norm) FROM manga.kitsu_staff"
        )
        (
            mesures["total"],
            mesures["oeuvres_couvertes"],
            mesures["personnes"],
        ) = curseur.fetchone()
        # Ce que le filtre subtype a écarté : compté, pas perdu.
        curseur.execute(
            "SELECT count(*) FROM staging.kitsu_staff s "
            "WHERE s.kitsu_id IS NOT NULL AND NOT EXISTS "
            "  (SELECT 1 FROM manga.kitsu_meta m "
            "   WHERE m.kitsu_id = s.kitsu_id::bigint)"
        )
        mesures["exclus_subtype"] = curseur.fetchone()[0]
    return mesures


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@app.command()
def charger(
    run: str = typer.Option(  # noqa: B008
        None, help="Run du catalogue (défaut : le fichier LATEST)."
    ),
    dry_run: bool = typer.Option(  # noqa: B008
        False, help="Exécute puis ROLLBACK : rien n'est écrit en base."
    ),
) -> None:
    """Charge manga.kitsu_meta puis manga.kitsu_staff depuis un run Kitsu."""
    dossier = resoudre_run(run)
    typer.echo(f"→ run {dossier.name}")
    debut = time.monotonic()

    with psycopg.connect(dsn()) as connexion:
        with connexion.cursor() as curseur:
            verifier_prerequis(curseur)

        meta = charger_meta(connexion, dossier)
        typer.echo(
            f"  ✓ kitsu_meta  : {meta['total']} œuvres "
            f"({meta['avec_annee']} avec année, "
            f"{100 * meta['avec_annee'] / max(meta['total'], 1):.1f} %) — "
            f"{meta['cible']} cible / {meta['entrees']} lues, "
            f"{meta['exclues']} hors subtype, {meta['sans_annee']} sans année"
        )

        staff = charger_staff(connexion, dossier)
        typer.echo(
            f"  ✓ kitsu_staff : {staff['total']} crédits sur "
            f"{staff['oeuvres_couvertes']} œuvres, "
            f"{staff['personnes']} personnes distinctes — "
            f"{staff['staging']} bruts, {staff['exclus_subtype']} hors subtype, "
            f"{staff['sans_nom']} sans nom résolu, "
            f"{staff['sans_norme']} sans norme"
        )

        if dry_run:
            connexion.rollback()
            typer.echo("⚠ DRY-RUN : transaction annulée, aucune écriture en base.")
        else:
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
