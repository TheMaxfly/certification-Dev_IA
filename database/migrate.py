#!/usr/bin/env python3
"""Runner de migrations SQL versionnées, volontairement minimal.

Principes :
- les migrations sont des fichiers `migrations/NNN_*.sql`, joués une fois, dans
  l'ordre lexicographique ;
- chaque fichier est appliqué dans **une transaction** : un échec annule le
  fichier entier et arrête le runner, jamais de migration à moitié appliquée ;
- un checksum SHA-256 est enregistré à l'application. Si un fichier déjà joué
  change, le runner refuse d'avancer : l'historique d'une base ne doit jamais
  diverger silencieusement du dépôt ;
- **pas de `down`**. On avance par migrations correctives (cf. README) ;
- `mark-applied` enregistre une migration sans la jouer. Exception réservée à
  la baseline d'héritage (000), déjà présente sur la base historique.

La connexion vient de `DATABASE_URL` : aucun identifiant n'est écrit ici.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
NOM_MIGRATION = re.compile(r"^(\d+)_[^/]+\.sql$")

TABLE_SUIVI = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now(),
    checksum   TEXT NOT NULL
)
"""


class ErreurMigration(Exception):
    """Erreur attendue : message lisible, pas de trace."""


@dataclass(frozen=True)
class Migration:
    version: str
    chemin: Path

    @property
    def nom(self) -> str:
        return self.chemin.name

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.chemin.read_bytes()).hexdigest()


def decouvrir(dossier: Path | None = None) -> list[Migration]:
    """Fichiers de migration, triés par nom (donc par NNN, qui est zéro-padé)."""
    # Résolu à l'appel, et non en valeur par défaut : les tests redirigent
    # MIGRATIONS_DIR vers un dossier jetable.
    dossier = dossier if dossier is not None else MIGRATIONS_DIR
    if not dossier.is_dir():
        raise ErreurMigration(f"Dossier de migrations introuvable : {dossier}")

    migrations = []
    for chemin in sorted(dossier.iterdir(), key=lambda p: p.name):
        if chemin.suffix != ".sql":
            continue
        correspondance = NOM_MIGRATION.match(chemin.name)
        if not correspondance:
            raise ErreurMigration(
                f"Nom de migration invalide : {chemin.name!r} "
                "(attendu : NNN_description.sql)"
            )
        migrations.append(Migration(correspondance.group(1), chemin))

    versions = [m.version for m in migrations]
    doublons = {v for v in versions if versions.count(v) > 1}
    if doublons:
        raise ErreurMigration(f"Versions en double : {sorted(doublons)}")
    return migrations


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurMigration(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://user:pass@localhost:5432/ma_base'"
        )
    return url


def deja_appliquees(connexion) -> dict[str, str]:
    """version -> checksum, pour les migrations déjà jouées sur cette base."""
    with connexion.cursor() as curseur:
        curseur.execute(TABLE_SUIVI)
        curseur.execute("SELECT version, checksum FROM public.schema_migrations")
        return dict(curseur.fetchall())


def verifier_checksums(migrations: list[Migration], appliquees: dict[str, str]) -> None:
    """Refuse d'avancer si un fichier déjà joué a changé depuis son application."""
    for migration in migrations:
        attendu = appliquees.get(migration.version)
        if attendu is not None and attendu != migration.checksum:
            raise ErreurMigration(
                f"Checksum différent pour la migration {migration.version} "
                f"({migration.nom}) : le fichier a changé après son application.\n"
                f"  appliqué : {attendu}\n"
                f"  fichier  : {migration.checksum}\n"
                "Une migration jouée est immuable : créer une migration "
                "corrective (NNN+1) plutôt que de modifier celle-ci."
            )


def connecter(dsn_url: str):
    """Connexion en autocommit — indispensable, et non un détail de confort.

    Sans autocommit, psycopg ouvre une transaction implicite au premier ordre :
    `connexion.transaction()` n'y poserait qu'un SAVEPOINT, et TOUT le run
    vivrait dans une seule transaction. Une migration en échec ferait alors
    annuler, en sortant sur l'exception, les migrations déjà appliquées pendant
    ce même run. En autocommit, chaque `transaction()` est une vraie
    transaction : un échec n'annule que son fichier.
    """
    return psycopg.connect(dsn_url, autocommit=True)


def appliquer(connexion, migration: Migration) -> None:
    """Applique un fichier dans sa propre transaction (DDL transactionnel PG)."""
    sql = migration.chemin.read_text(encoding="utf-8")
    with connexion.transaction():
        with connexion.cursor() as curseur:
            curseur.execute(sql)
            curseur.execute(
                "INSERT INTO public.schema_migrations (version, checksum) "
                "VALUES (%s, %s)",
                (migration.version, migration.checksum),
            )


def commande_status(_args) -> int:
    migrations = decouvrir()
    with connecter(dsn()) as connexion:
        appliquees = deja_appliquees(connexion)

    derive = False
    print(f"{'version':>8}  {'état':<12} {'fichier'}")
    print("-" * 60)
    for migration in migrations:
        checksum_applique = appliquees.get(migration.version)
        if checksum_applique is None:
            etat = "en attente"
        elif checksum_applique != migration.checksum:
            etat = "MODIFIÉ"
            derive = True
        else:
            etat = "appliquée"
        print(f"{migration.version:>8}  {etat:<12} {migration.nom}")

    connues = {m.version for m in migrations}
    for version in sorted(set(appliquees) - connues):
        derive = True
        print(f"{version:>8}  {'ORPHELINE':<12} (appliquée en base, absente du dépôt)")

    en_attente = sum(1 for m in migrations if m.version not in appliquees)
    print("-" * 60)
    print(f"{len(appliquees)} appliquée(s), {en_attente} en attente")
    if derive:
        print(
            "\nERREUR : la base a divergé du dépôt (voir MODIFIÉ / ORPHELINE).",
            file=sys.stderr,
        )
        return 1
    return 0


def commande_mark_applied(args) -> int:
    """Enregistre une migration comme appliquée SANS exécuter son SQL.

    Usage prévu : la baseline d'héritage (000), dont les objets existent déjà
    sur la base historique. L'y rejouer échouerait (`CREATE TABLE` sur une
    table présente) ; ne pas l'enregistrer laisserait cette base éternellement
    « en attente » d'une migration qu'elle a, de fait, déjà.

    La commande ment donc délibérément au runner, et c'est son seul emploi
    légitime : dire qu'un état est atteint alors qu'on ne l'a pas produit. À
    n'utiliser que si l'on a vérifié que la base porte bien cet état.
    """
    migrations = decouvrir()
    par_version = {m.version: m for m in migrations}
    migration = par_version.get(args.version)
    if migration is None:
        raise ErreurMigration(
            f"Version inconnue : {args.version!r}. "
            f"Versions disponibles : {sorted(par_version)}"
        )

    with connecter(dsn()) as connexion:
        appliquees = deja_appliquees(connexion)
        if migration.version in appliquees:
            raise ErreurMigration(
                f"La migration {migration.version} est déjà enregistrée dans "
                "schema_migrations : il n'y a rien à marquer."
            )
        with connexion.cursor() as curseur:
            curseur.execute(
                "INSERT INTO public.schema_migrations (version, checksum) "
                "VALUES (%s, %s)",
                (migration.version, migration.checksum),
            )

    print(f"→ {migration.version} ({migration.nom}) marquée appliquée.")
    print("  Le SQL n'a PAS été exécuté : ses objets sont supposés déjà en base.")
    return 0


def commande_up(args) -> int:
    migrations = decouvrir()
    with connecter(dsn()) as connexion:
        appliquees = deja_appliquees(connexion)
        verifier_checksums(migrations, appliquees)

        if args.target is not None:
            if args.target not in {m.version for m in migrations}:
                raise ErreurMigration(
                    f"Cible inconnue : {args.target!r}. "
                    f"Versions disponibles : {[m.version for m in migrations]}"
                )
            migrations = [m for m in migrations if m.version <= args.target]

        en_attente = [m for m in migrations if m.version not in appliquees]
        if not en_attente:
            print("Rien à appliquer : la base est à jour.")
            return 0

        for migration in en_attente:
            print(f"→ application de {migration.nom} ...", flush=True)
            try:
                appliquer(connexion, migration)
            except psycopg.Error as erreur:
                raise ErreurMigration(
                    f"Échec de {migration.nom} : {erreur}\n"
                    "Le fichier a été annulé (rollback) ; les migrations "
                    "précédentes restent appliquées."
                ) from erreur
            print(f"  ✓ {migration.version} appliquée")

    print(f"{len(en_attente)} migration(s) appliquée(s).")
    return 0


def construire_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Applique les migrations SQL versionnées (DATABASE_URL)."
    )
    sous = parser.add_subparsers(dest="commande", required=True)
    sous.add_parser("status", help="Migrations appliquées et en attente.")
    up = sous.add_parser("up", help="Applique les migrations en attente.")
    up.add_argument(
        "--target",
        metavar="NNN",
        help="S'arrêter à cette version incluse (ex. 001).",
    )
    marque = sous.add_parser(
        "mark-applied",
        help="Enregistre une migration comme appliquée SANS l'exécuter "
        "(baseline d'héritage).",
    )
    marque.add_argument(
        "version",
        metavar="NNN",
        help="Version à marquer (ex. 000).",
    )
    return parser


def main() -> int:
    args = construire_parser().parse_args()
    commandes = {
        "status": commande_status,
        "up": commande_up,
        "mark-applied": commande_mark_applied,
    }
    try:
        return commandes[args.commande](args)
    except ErreurMigration as erreur:
        print(f"ERREUR : {erreur}", file=sys.stderr)
        return 1
    except psycopg.Error as erreur:
        print(f"ERREUR de connexion ou SQL : {erreur}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
