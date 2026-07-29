"""Migration 012 — le rôle de consultation `manga_ro`.

Ce que ces tests vérifient tient en une phrase : `manga_ro` peut tout lire dans
`manga`, et rien d'autre, nulle part. L'inventaire n'est pas écrit en dur — il
est dérivé du catalogue de la base migrée — mais deux SENTINELLES en dur le
gardent, sur le modèle de `test_migrate.py` : un inventaire entièrement dérivé
d'une requête qui ne renverrait rien rendrait toutes les assertions vraies en
silence.

Rappel de portée : un rôle est un objet de CLUSTER. Le harnais crée une base
neuve par test dans un conteneur unique, donc `manga_ro` survit d'un test au
suivant. C'est précisément pourquoi la migration garde son `CREATE ROLE` par un
test d'existence — et ces tests s'appuient sur ce comportement plutôt que de
tenter de l'annuler.
"""

from __future__ import annotations

from argparse import Namespace

import psycopg
import pytest
from conftest import migrate

UP = Namespace(commande="up", target=None)

# Sentinelles : témoins indépendants du catalogue qu'on interroge.
SENTINELLE_RELATIONS_MANGA = 38  # 30 tables + 8 vues (inventaire §34)
SENTINELLE_TABLES_STAGING = 11
SENTINELLE_TABLE_CONNUE = "manga.ms_series_enriched"

ECRITURES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


@pytest.fixture
def base_migree(base):
    migrate.commande_up(UP)
    return base


def relations(connexion, schema: str) -> list[str]:
    """Tables et vues d'un schéma, qualifiées, telles que la base les porte."""
    return [
        ligne[0]
        for ligne in connexion.execute(
            """
            SELECT n.nspname || '.' || c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s AND c.relkind IN ('r', 'v')
             ORDER BY 1
            """,
            (schema,),
        ).fetchall()
    ]


def test_012_cree_le_role_de_groupe(base_migree):
    with psycopg.connect(base_migree) as connexion:
        ligne = connexion.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
            "FROM pg_roles WHERE rolname = 'manga_ro'"
        ).fetchone()

    assert ligne is not None, "manga_ro est absent après la migration 012"
    # NOLOGIN : on ne se connecte pas sous un rôle de groupe, on en hérite.
    assert ligne == (False, False, False, False, False)


def test_012_accorde_connect_sur_la_base_courante(base_migree):
    """Le GRANT ne peut pas nommer la base : chaque base de test a un nom unique."""
    with psycopg.connect(base_migree) as connexion:
        (connect,) = connexion.execute(
            "SELECT has_database_privilege('manga_ro', current_database(), 'CONNECT')"
        ).fetchone()

    assert connect is True


def test_inventaire_derive_reste_sous_temoin(base_migree):
    """Garde-fou : sans lui, un catalogue vide validerait tout ce qui suit."""
    with psycopg.connect(base_migree) as connexion:
        manga = relations(connexion, "manga")
        staging = relations(connexion, "staging")

    assert len(manga) == SENTINELLE_RELATIONS_MANGA
    assert len(staging) == SENTINELLE_TABLES_STAGING
    assert SENTINELLE_TABLE_CONNUE in manga


def test_manga_ro_lit_toutes_les_relations_de_manga(base_migree):
    """38 relations sur 38 : aucune n'est oubliée par `GRANT ON ALL TABLES`.

    Ce test attraperait une relation appartenant à un autre rôle que celui qui
    exécute les migrations : `GRANT ON ALL TABLES` ne couvre que ce que le
    propriétaire peut donner.
    """
    with psycopg.connect(base_migree) as connexion:
        cibles = relations(connexion, "manga")
        lisibles = [
            cible
            for cible in cibles
            if connexion.execute(
                "SELECT has_table_privilege('manga_ro', %s, 'SELECT')", (cible,)
            ).fetchone()[0]
        ]

    assert len(lisibles) == len(cibles)
    assert sorted(lisibles) == sorted(cibles)


@pytest.mark.parametrize("privilege", ECRITURES)
def test_manga_ro_n_ecrit_sur_aucune_relation_de_manga(base_migree, privilege):
    with psycopg.connect(base_migree) as connexion:
        cibles = relations(connexion, "manga")
        accordes = [
            cible
            for cible in cibles
            if connexion.execute(
                "SELECT has_table_privilege('manga_ro', %s, %s)", (cible, privilege)
            ).fetchone()[0]
        ]

    assert accordes == [], f"{privilege} accordé sur {accordes}"


def test_manga_ro_n_a_aucun_droit_sur_les_sequences(base_migree):
    """Sans USAGE sur une séquence, `nextval` est fermé — dernière voie d'écriture."""
    with psycopg.connect(base_migree) as connexion:
        sequences = [
            ligne[0]
            for ligne in connexion.execute(
                "SELECT n.nspname || '.' || c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'manga' AND c.relkind = 'S'"
            ).fetchall()
        ]
        accordees = [
            sequence
            for sequence in sequences
            for privilege in ("USAGE", "UPDATE", "SELECT")
            if connexion.execute(
                "SELECT has_sequence_privilege('manga_ro', %s, %s)",
                (sequence, privilege),
            ).fetchone()[0]
        ]

    assert sequences, "aucune séquence dans manga : le test ne prouverait rien"
    assert accordees == []


def test_staging_reste_hors_de_portee(base_migree):
    """`staging` est jetable et hors mise à disposition : rien, pas même USAGE."""
    with psycopg.connect(base_migree) as connexion:
        (usage,) = connexion.execute(
            "SELECT has_schema_privilege('manga_ro', 'staging', 'USAGE')"
        ).fetchone()
        cibles = relations(connexion, "staging")
        lisibles = [
            cible
            for cible in cibles
            if connexion.execute(
                "SELECT has_table_privilege('manga_ro', %s, 'SELECT')", (cible,)
            ).fetchone()[0]
        ]

    assert usage is False
    assert lisibles == []


def test_012_couvre_les_objets_futurs(base_migree):
    """`ALTER DEFAULT PRIVILEGES` ne vaut que pour le rôle qui l'a exécuté.

    Ici c'est `postgres`, qui joue les migrations et possède les 38 relations de
    `manga`. Une table créée après la migration doit donc être lisible sans
    nouvel ordre — sinon chaque migration ultérieure devrait penser à un GRANT,
    et l'oubli passerait inaperçu.
    """
    with psycopg.connect(base_migree, autocommit=True) as connexion:
        connexion.execute("CREATE TABLE manga.objet_futur (id int)")

        (select,) = connexion.execute(
            "SELECT has_table_privilege('manga_ro', 'manga.objet_futur', 'SELECT')"
        ).fetchone()
        accordes = [
            privilege
            for privilege in ECRITURES
            if connexion.execute(
                "SELECT has_table_privilege('manga_ro', 'manga.objet_futur', %s)",
                (privilege,),
            ).fetchone()[0]
        ]

    assert select is True, "l'objet futur n'est pas lisible : le défaut n'a pas pris"
    assert accordes == [], f"l'objet futur est écrivable : {accordes}"


def test_012_est_idempotente(base_migree):
    """Rejeu du fichier : zéro changement sur les ACL et les défauts.

    Le rejeu direct du SQL, et non `migrate.py up` — qui verrait la migration
    déjà enregistrée et ne ferait rien, ce qui ne prouverait pas l'idempotence
    du fichier lui-même.
    """
    sql = (migrate.MIGRATIONS_DIR / "012_roles_lecture.sql").read_text(encoding="utf-8")
    lecture_acl = """
        SELECT c.relname, c.relacl::text
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'manga'
         ORDER BY c.relname
    """
    lecture_defauts = (
        "SELECT defaclobjtype::text, defaclacl::text FROM pg_default_acl ORDER BY 1, 2"
    )

    with psycopg.connect(base_migree, autocommit=True) as connexion:
        acl_avant = connexion.execute(lecture_acl).fetchall()
        defauts_avant = connexion.execute(lecture_defauts).fetchall()

        connexion.execute(sql)

        assert connexion.execute(lecture_acl).fetchall() == acl_avant
        assert connexion.execute(lecture_defauts).fetchall() == defauts_avant


def test_le_role_de_connexion_n_est_pas_dans_le_depot():
    """`manga_api` porte un mot de passe : aucune migration ne doit le créer.

    Une migration est immuable et son checksum est vérifié à chaque `up`. Y
    écrire un secret le figerait dans l'historique du dépôt.
    """
    for chemin in sorted(migrate.MIGRATIONS_DIR.glob("*.sql")):
        contenu = chemin.read_text(encoding="utf-8")
        assert "CREATE ROLE manga_api" not in contenu, (
            f"{chemin.name} crée le rôle de connexion : il porte un mot de passe, "
            "sa création appartient à outils/creer_role_lecture.sh."
        )
        assert "PASSWORD" not in contenu.upper(), (
            f"{chemin.name} contient le mot PASSWORD : un secret n'entre pas "
            "dans une migration."
        )
