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

Second témoin, sur la même connexion (E4, contrat 0.5.0) : la normalisation de la
recherche du catalogue. Le SQL de `GET /series` normalise les colonnes
(`translate(lower(…))`, la table de `app.recherche` liée en paramètres), Python
normalise la saisie : les deux chemins doivent rendre la même chose, et un `%` de la
saisie ne doit pas être un joker — prouvé ici sur un vrai PostgreSQL, ce que le pool
simulé des tests unitaires ne peut pas faire.
"""

from __future__ import annotations

import sys

import psycopg

from app import recherche
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

#: Les saisies de la spec : accent circonflexe, macron, majuscules accentuées, rien.
SAISIES_A_NORMALISER = ("Shônen", "Pīsu", "ÉTÉ", "naruto")


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

        verifier_la_normalisation(connexion)

    print(f"témoin positif : {len(ECRITURES_A_REFUSER)} écritures refusées.")


def verifier_la_normalisation(connexion: psycopg.Connection) -> None:
    """Le SQL de la recherche et Python rendent la même forme ; `%` reste littéral."""
    de, vers = recherche.ACCENTS
    for saisie in SAISIES_A_NORMALISER:
        (en_sql,) = connexion.execute(
            "SELECT translate(lower(%s), %s, %s)", (saisie, de, vers)
        ).fetchone()
        if en_sql != recherche.normaliser(saisie):
            echouer(
                f"normalisation : {saisie!r} donne {en_sql!r} en SQL et "
                f"{recherche.normaliser(saisie)!r} en Python."
            )
    motif = recherche.parametres("100%")["contient"]
    (litteral, joker) = connexion.execute(
        "SELECT 'x100%%y' LIKE %s ESCAPE '\\', 'x100zy' LIKE %s ESCAPE '\\'",
        (motif, motif),
    ).fetchone()
    if not litteral or joker:
        echouer("le « % » de la saisie est un joker : il n'est pas échappé.")
    print(
        f"normalisation : {len(SAISIES_A_NORMALISER)} saisies, SQL et Python "
        "égaux ; « % » littéral."
    )


if __name__ == "__main__":
    main()
