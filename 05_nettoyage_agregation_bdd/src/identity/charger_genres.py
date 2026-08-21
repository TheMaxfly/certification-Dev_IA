"""Chargement du référentiel de genres : CSV versionnés -> manga.genre_*.

    uv run python -m identity.charger_genres
    uv run python -m identity.charger_genres --donnees chemin/vers/donnees
    uv run python -m identity.charger_genres --dry-run

Deux fichiers, dans cet ordre imposé par la clé étrangère :
  1. `genre_ref.csv`     -> manga.genre_ref     (les codes et leurs libellés) ;
  2. `genre_mapping.csv` -> manga.genre_mapping (libellé brut -> code).

POURQUOI UN CSV ET PAS UNE MIGRATION. Le schéma est joué une fois et son
checksum est vérifié ; ces deux tables, elles, sont des DONNÉES de référence qui
bougeront — un libellé nouveau apparaît chez une source, un `inconnu` est
arbitré. Le dépôt sépare donc les deux : `013_referentiel_genres.sql` pose les
tables, ce chargeur pose leur contenu, et le contenu se relit en diff dans un
CSV plutôt qu'en `INSERT` empilés.

UPSERT, AUCUN DELETE. Rejouer ce chargeur après avoir retiré une ligne du CSV
ne supprime rien en base : `genre_mapping` référence `genre_ref`, et surtout un
code retiré du CSV peut déjà être écrit dans des données dérivées. Supprimer un
référentiel est une décision qui mérite sa propre migration, pas un effet de
bord de rechargement.

IDEMPOTENCE MESURÉE, PAS PROMISE. Le `DO UPDATE` porte un `WHERE ... IS
DISTINCT FROM ...` : une ligne identique n'est pas réécrite, donc le rejeu
annonce zéro insertion ET zéro modification. Sans ce garde-fou, un rejeu
réécrirait 227 lignes à l'identique et l'idempotence ne serait plus qu'une
intention invérifiable.

CE CHARGEUR NE TOUCHE À AUCUNE DONNÉE DE SÉRIE. Ni `ms_series_enriched`, ni
aucune colonne `*_enriched`. Il pose le vocabulaire ; l'appliquer est une autre
étape, qui attend l'arbitrage des lignes `inconnu`.
"""

from __future__ import annotations

import csv
import os
import sys
import time
from pathlib import Path

import psycopg
import typer

RACINE = Path(__file__).resolve().parents[3]
DONNEES_DEFAUT = RACINE / "database/donnees"

STATUTS = ("mappe", "exclu", "inconnu")
SOURCES = ("ms", "kitsu")
TYPES = ("genre", "format")

# Profondeur maximale de l'arbre de genres. Trois niveaux suffisent au
# vocabulaire (adulte -> ecchi, lgbt -> yaoi) ; au-delà, la dérivation
# ajouterait des codes qu'aucun lecteur ne relierait plus à la série.
PROFONDEUR_MAX = 3

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


def _lire_csv(chemin: Path, colonnes: tuple[str, ...]) -> list[dict[str, str]]:
    """Lit un CSV en vérifiant son en-tête : une colonne renommée doit échouer
    ici, avec le nom du fichier, plutôt qu'en base avec un nom de contrainte."""
    if not chemin.is_file():
        raise ErreurChargement(f"Fichier introuvable : {chemin}")
    with chemin.open(encoding="utf-8", newline="") as f:
        lecteur = csv.DictReader(f)
        attendu = list(colonnes)
        if lecteur.fieldnames != attendu:
            raise ErreurChargement(
                f"{chemin.name} : en-tête inattendu.\n"
                f"  attendu : {attendu}\n"
                f"  lu      : {lecteur.fieldnames}"
            )
        return list(lecteur)


def lire_ref(dossier: Path) -> list[dict[str, str]]:
    lignes = _lire_csv(
        dossier / "genre_ref.csv",
        ("code", "label_fr", "label_en", "label_ja", "ordre", "type", "parent"),
    )
    codes = {ligne["code"] for ligne in lignes}
    vus = set()
    for ligne in lignes:
        code = ligne["code"]
        if code in vus:
            raise ErreurChargement(f"genre_ref.csv : code en double — {code}")
        vus.add(code)
        if not ligne["label_fr"]:
            raise ErreurChargement(f"genre_ref.csv : label_fr vide pour {code}")
        if ligne["type"] not in TYPES:
            raise ErreurChargement(
                f"genre_ref.csv : type « {ligne['type']} » hors {TYPES} pour {code}"
            )
        # Le préfixe et le type disent la même chose : ils ne peuvent pas diverger.
        if code.startswith("format_") != (ligne["type"] == "format"):
            raise ErreurChargement(
                f"genre_ref.csv : {code} — le préfixe et le type se contredisent "
                f"(type « {ligne['type']} »)."
            )
        if ligne["parent"]:
            if ligne["parent"] == code:
                raise ErreurChargement(f"genre_ref.csv : {code} est son propre parent.")
            if ligne["parent"] not in codes:
                raise ErreurChargement(
                    f"genre_ref.csv : parent « {ligne['parent']} » de {code} "
                    "n'existe pas."
                )
    if not lignes:
        raise ErreurChargement("genre_ref.csv est vide.")
    _verifier_arbre(lignes)
    return lignes


def _verifier_arbre(lignes: list[dict[str, str]]) -> None:
    """Aucun cycle, profondeur <= PROFONDEUR_MAX.

    La FK et le CHECK de 014 ne voient qu'une ligne à la fois : `a -> b -> a`
    leur échappe. La question ne se pose que sur le contenu, donc le contrôle
    vit ici — avant l'écriture, pas après."""
    parent = {ligne["code"]: ligne["parent"] or None for ligne in lignes}
    for code in parent:
        vus, courant, profondeur = [code], parent[code], 1
        while courant is not None:
            if courant in vus:
                raise ErreurChargement(
                    "genre_ref.csv : cycle de parenté — " + " -> ".join([*vus, courant])
                )
            vus.append(courant)
            profondeur += 1
            if profondeur > PROFONDEUR_MAX:
                raise ErreurChargement(
                    f"genre_ref.csv : profondeur > {PROFONDEUR_MAX} — "
                    + " -> ".join(vus)
                )
            courant = parent[courant]


def lire_mapping(dossier: Path, codes: set[str]) -> list[dict[str, str]]:
    """Contrôle l'invariant AVANT la base. Le CHECK de 013 dirait la même chose,
    mais avec un nom de contrainte et sans dire QUELLE ligne du CSV est en
    faute — inutilisable pour corriger le fichier."""
    lignes = _lire_csv(
        dossier / "genre_mapping.csv",
        ("source", "libelle_brut", "code", "statut", "note"),
    )
    vus: set[tuple[str, str]] = set()
    for numero, ligne in enumerate(lignes, start=2):  # 1 = en-tête
        cle = (ligne["source"], ligne["libelle_brut"])
        contexte = f"genre_mapping.csv ligne {numero} ({cle[0]} / {cle[1]!r})"
        if cle in vus:
            raise ErreurChargement(f"{contexte} : couple en double.")
        vus.add(cle)
        if ligne["source"] not in SOURCES:
            raise ErreurChargement(f"{contexte} : source hors {SOURCES}.")
        if ligne["statut"] not in STATUTS:
            raise ErreurChargement(f"{contexte} : statut hors {STATUTS}.")
        if (ligne["statut"] == "mappe") != bool(ligne["code"]):
            raise ErreurChargement(
                f"{contexte} : invariant rompu — statut « {ligne['statut']} » "
                f"et code « {ligne['code']} ». Un libellé est mappé si et "
                "seulement s'il porte un code."
            )
        if ligne["code"] and ligne["code"] not in codes:
            raise ErreurChargement(
                f"{contexte} : code « {ligne['code']} » absent de genre_ref.csv."
            )
        if ligne["statut"] != "mappe" and not ligne["note"]:
            raise ErreurChargement(
                f"{contexte} : un statut « {ligne['statut']} » doit dire pourquoi."
            )
    return lignes


def verifier_prerequis(curseur) -> None:
    curseur.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'manga' AND table_name IN "
        "('genre_ref', 'genre_mapping')"
    )
    if curseur.fetchone()[0] != 2:
        raise ErreurChargement(
            "manga.genre_ref / manga.genre_mapping manquent. "
            "Jouer la migration 013 :\n"
            "  cd database && uv run python migrate.py up"
        )
    # Le CSV porte `type` et `parent` depuis 014 : sans la migration, l'INSERT
    # échouerait sur une colonne inconnue, message autrement moins parlant.
    curseur.execute(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'manga' AND table_name = 'genre_ref' "
        "AND column_name IN ('type', 'parent')"
    )
    if curseur.fetchone()[0] != 2:
        raise ErreurChargement(
            "manga.genre_ref.type / .parent manquent. "
            "Jouer la migration 014 :\n"
            "  cd database && uv run python migrate.py up"
        )


def _bilan(resultats: list[tuple[bool]]) -> dict[str, int]:
    """`xmax = 0` distingue l'insertion de la mise à jour sur la ligne renvoyée :
    une ligne insérée n'a pas de transaction de suppression."""
    inseres = sum(1 for (insere,) in resultats if insere)
    return {"inseres": inseres, "modifies": len(resultats) - inseres}


def charger_ref(connexion, lignes: list[dict[str, str]]) -> dict[str, int]:
    with connexion.cursor() as curseur:
        curseur.execute(
            "INSERT INTO manga.genre_ref "
            "  (code, label_fr, label_en, label_ja, ordre, type, parent) "
            "SELECT * FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[], "
            "                     %s::int[], %s::text[], %s::text[]) "
            "ON CONFLICT (code) DO UPDATE SET "
            "  label_fr = EXCLUDED.label_fr, "
            "  label_en = EXCLUDED.label_en, "
            "  label_ja = EXCLUDED.label_ja, "
            "  ordre    = EXCLUDED.ordre, "
            "  type     = EXCLUDED.type, "
            "  parent   = EXCLUDED.parent "
            "WHERE (genre_ref.label_fr, genre_ref.label_en, genre_ref.label_ja, "
            "       genre_ref.ordre, genre_ref.type, genre_ref.parent) "
            "      IS DISTINCT FROM "
            "      (EXCLUDED.label_fr, EXCLUDED.label_en, EXCLUDED.label_ja, "
            "       EXCLUDED.ordre, EXCLUDED.type, EXCLUDED.parent) "
            "RETURNING (xmax = 0)",
            (
                [ligne["code"] for ligne in lignes],
                [ligne["label_fr"] for ligne in lignes],
                [ligne["label_en"] or None for ligne in lignes],
                [ligne["label_ja"] or None for ligne in lignes],
                [int(ligne["ordre"]) if ligne["ordre"] else None for ligne in lignes],
                [ligne["type"] for ligne in lignes],
                [ligne["parent"] or None for ligne in lignes],
            ),
        )
        bilan = _bilan(curseur.fetchall())
        curseur.execute("SELECT count(*) FROM manga.genre_ref")
        bilan["total"] = curseur.fetchone()[0]
    return bilan


def charger_mapping(connexion, lignes: list[dict[str, str]]) -> dict[str, int]:
    with connexion.cursor() as curseur:
        curseur.execute(
            "INSERT INTO manga.genre_mapping "
            "  (source, libelle_brut, code, statut, note) "
            "SELECT * FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[], "
            "                     %s::text[]) "
            "ON CONFLICT (source, libelle_brut) DO UPDATE SET "
            "  code   = EXCLUDED.code, "
            "  statut = EXCLUDED.statut, "
            "  note   = EXCLUDED.note "
            "WHERE (genre_mapping.code, genre_mapping.statut, genre_mapping.note) "
            "      IS DISTINCT FROM (EXCLUDED.code, EXCLUDED.statut, EXCLUDED.note) "
            "RETURNING (xmax = 0)",
            (
                [ligne["source"] for ligne in lignes],
                [ligne["libelle_brut"] for ligne in lignes],
                [ligne["code"] or None for ligne in lignes],
                [ligne["statut"] for ligne in lignes],
                [ligne["note"] or None for ligne in lignes],
            ),
        )
        bilan = _bilan(curseur.fetchall())
        curseur.execute(
            "SELECT statut, count(*) FROM manga.genre_mapping GROUP BY statut"
        )
        par_statut = dict(curseur.fetchall())
    bilan["total"] = sum(par_statut.values())
    bilan.update({statut: par_statut.get(statut, 0) for statut in STATUTS})
    return bilan


@app.command()
def charger(
    # noqa: B008 — `typer.Option()` en défaut est l'API de Typer, pas un oubli.
    donnees: Path = typer.Option(  # noqa: B008
        DONNEES_DEFAUT, help="Dossier des CSV versionnés (lecture seule)."
    ),
    dry_run: bool = typer.Option(  # noqa: B008
        False, help="Exécute puis ROLLBACK : rien n'est écrit en base."
    ),
) -> None:
    """Charge manga.genre_ref puis manga.genre_mapping depuis les CSV du dépôt."""
    debut = time.monotonic()
    ref = lire_ref(donnees)
    mapping = lire_mapping(donnees, {ligne["code"] for ligne in ref})
    typer.echo(f"→ {donnees} : {len(ref)} codes, {len(mapping)} correspondances")

    # Une seule transaction : un mapping à moitié chargé sur un référentiel
    # complet serait un état que personne ne sait interpréter.
    with psycopg.connect(dsn()) as connexion:
        with connexion.cursor() as curseur:
            verifier_prerequis(curseur)

        bilan_ref = charger_ref(connexion, ref)
        typer.echo(
            f"  ✓ genre_ref     : {bilan_ref['total']} codes "
            f"(+{bilan_ref['inseres']} nouveaux, "
            f"{bilan_ref['modifies']} modifiés)"
        )

        bilan_map = charger_mapping(connexion, mapping)
        typer.echo(
            f"  ✓ genre_mapping : {bilan_map['total']} libellés "
            f"(+{bilan_map['inseres']} nouveaux, "
            f"{bilan_map['modifies']} modifiés) — "
            f"{bilan_map['mappe']} mappés, {bilan_map['exclu']} exclus, "
            f"{bilan_map['inconnu']} en attente d'arbitrage"
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
