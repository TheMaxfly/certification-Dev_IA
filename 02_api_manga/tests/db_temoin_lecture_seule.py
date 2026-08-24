"""Témoin positif : la base REFUSE l'écriture au rôle de l'API.

Exécuté par le Compose d'intégration, sous `manga_api` — le rôle même que
l'API utilise. C'est le test qui compte de l'étape « rôles » : constater que
la lecture fonctionne encore ne prouve rien, puisqu'elle fonctionnait déjà en
superutilisateur. Seul un refus attendu prouve que la restriction existe.

Le refus recherché est `SQLSTATE 42501` (`insufficient_privilege`) : la
signature d'un privilège manquant, et non d'une contrainte métier, d'une table
absente ou d'une transaction en lecture seule — toutes portent d'autres codes,
et passer par le code plutôt que par le message rend le test indifférent à la
langue du serveur.
"""

from __future__ import annotations

import sys

import psycopg

from app.database import build_conninfo
from app.settings import Settings

ECRITURES_A_REFUSER = (
    ("INSERT", "INSERT INTO manga.kitsu_series_core (kitsu_id) VALUES (-1)"),
    ("UPDATE", "UPDATE manga.kitsu_series_core SET slug = 'x'"),
    ("DELETE", "DELETE FROM manga.kitsu_series_core"),
    ("TRUNCATE", "TRUNCATE manga.ms_kitsu_map"),
    # Le schéma `staging` est hors mise à disposition : la lecture elle-même
    # doit être refusée, faute de `USAGE` sur le schéma.
    ("SELECT staging", "SELECT 1 FROM staging.ms_reviews"),
    # Dernière voie d'écriture indirecte : sans `USAGE` sur les séquences,
    # `nextval` est fermé.
    ("nextval", "SELECT nextval('manga.rag_reviews_docs_doc_id_seq')"),
)

PRIVILEGE_INSUFFISANT = "42501"


def echouer(message: str) -> None:
    print(f"TÉMOIN ÉCHOUÉ : {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    # `database_from_env` et non `from_env` : ce témoin prouve un RÔLE
    # PostgreSQL, il ne sert aucune requête HTTP et n'a donc pas de clé d'API.
    # Lui en fabriquer une pour satisfaire la validation du trousseau
    # ajouterait au harnais un secret qui n'ouvre rien.
    conninfo = build_conninfo(Settings.database_from_env())

    with psycopg.connect(conninfo, autocommit=True) as connexion:
        (utilisateur,) = connexion.execute("SELECT current_user").fetchone()
        if utilisateur != "manga_api":
            echouer(
                f"connecté en {utilisateur!r} et non en 'manga_api' : le témoin "
                "ne prouverait rien sur le rôle de l'API."
            )
        print(f"connecté en {utilisateur}")

        # Contre-épreuve : la lecture du schéma `manga`, elle, doit marcher.
        # Sans elle, un rôle sans AUCUN droit ferait passer tout le reste.
        (nombre,) = connexion.execute(
            "SELECT count(*) FROM manga.rag_export_docs"
        ).fetchone()
        if nombre < 1:
            echouer(
                "aucun document lisible dans manga.rag_export_docs : le rôle ne "
                "lit rien, les refus qui suivent ne prouveraient rien."
            )
        print(f"lecture autorisée : {nombre} documents dans rag_export_docs")

        for intitule, ordre in ECRITURES_A_REFUSER:
            try:
                connexion.execute(ordre)
            except psycopg.errors.Error as erreur:
                if erreur.sqlstate != PRIVILEGE_INSUFFISANT:
                    echouer(
                        f"{intitule} refusé, mais avec SQLSTATE "
                        f"{erreur.sqlstate} au lieu de {PRIVILEGE_INSUFFISANT} : "
                        "ce n'est pas un refus de privilège."
                    )
                print(f"{intitule} refusé — SQLSTATE {erreur.sqlstate}")
            else:
                echouer(
                    f"{intitule} a RÉUSSI sous manga_api. Le rôle de l'API peut "
                    "écrire : la restriction n'existe pas."
                )

    print(f"témoin positif : {len(ECRITURES_A_REFUSER)} écritures refusées.")


if __name__ == "__main__":
    main()
