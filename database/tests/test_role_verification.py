"""Migration 022 — le rôle de vérification `verif_ro`, et son rôle de connexion.

En une phrase : `verif_ro` lit `manga`, `bench` et le suivi des migrations, et rien
d'autre ; `manga_ro` (012), dont l'API hérite, reste hors de `bench`. Le rôle de
connexion `verif_lecture`, créé par `outils/creer_role_verification.sh`, hérite de
`verif_ro` et n'ouvre que des sessions en lecture seule.

Même méthode que `test_roles_lecture.py` : l'inventaire est dérivé du catalogue de
la base migrée, gardé par des SENTINELLES (planchers) pour qu'un inventaire effondré
ne rende pas toutes les assertions vraies en silence.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from argparse import Namespace

import psycopg
import pytest
from conftest import RACINE, migrate
from psycopg import conninfo, errors

UP = Namespace(commande="up", target=None)
SCRIPT = RACINE / "outils" / "creer_role_verification.sh"

# Planchers : 20 tables et 1 vue dans bench au 022 ; ne peut que monter.
SENTINELLE_RELATIONS_BENCH = 21
SENTINELLE_RELATIONS_MANGA = 40
SENTINELLES_CONNUES = ("bench.eval_mesures", "bench.v_encodage_en_service")

ECRITURES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
SCHEMAS_LUS = ("manga", "bench")


@pytest.fixture
def base_migree(base):
    migrate.commande_up(UP)
    return base


def relations(connexion, schema: str) -> list[str]:
    """Tables, vues et vues matérialisées d'un schéma, qualifiées."""
    return [
        ligne[0]
        for ligne in connexion.execute(
            """
            SELECT n.nspname || '.' || c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s AND c.relkind IN ('r', 'v', 'm', 'p')
             ORDER BY 1
            """,
            (schema,),
        ).fetchall()
    ]


def privilege(connexion, role: str, relation: str, droit: str) -> bool:
    return connexion.execute(
        "SELECT has_table_privilege(%s, %s, %s)", (role, relation, droit)
    ).fetchone()[0]


def test_022_cree_le_role_de_groupe(base_migree):
    with psycopg.connect(base_migree) as connexion:
        ligne = connexion.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
            "FROM pg_roles WHERE rolname = 'verif_ro'"
        ).fetchone()

    assert ligne is not None, "verif_ro est absent après la migration 022"
    assert ligne == (False, False, False, False, False)


def test_022_accorde_connect_sur_la_base_courante(base_migree):
    with psycopg.connect(base_migree) as connexion:
        (connect,) = connexion.execute(
            "SELECT has_database_privilege('verif_ro', current_database(), 'CONNECT')"
        ).fetchone()

    assert connect is True


def test_inventaire_derive_reste_sous_temoin(base_migree):
    with psycopg.connect(base_migree) as connexion:
        bench = relations(connexion, "bench")
        manga = relations(connexion, "manga")

    assert len(bench) >= SENTINELLE_RELATIONS_BENCH, (
        f"{len(bench)} relations dans bench"
    )
    assert len(manga) >= SENTINELLE_RELATIONS_MANGA, (
        f"{len(manga)} relations dans manga"
    )
    for connue in SENTINELLES_CONNUES:
        assert connue in bench


@pytest.mark.parametrize("schema", SCHEMAS_LUS)
def test_verif_ro_lit_toutes_les_relations(base_migree, schema):
    with psycopg.connect(base_migree) as connexion:
        cibles = relations(connexion, schema)
        illisibles = [
            c for c in cibles if not privilege(connexion, "verif_ro", c, "SELECT")
        ]

    assert cibles
    assert illisibles == []


def test_verif_ro_lit_le_suivi_des_migrations(base_migree):
    with psycopg.connect(base_migree) as connexion:
        assert privilege(connexion, "verif_ro", "public.schema_migrations", "SELECT")


@pytest.mark.parametrize("droit", ECRITURES)
def test_verif_ro_n_ecrit_nulle_part(base_migree, droit):
    with psycopg.connect(base_migree) as connexion:
        cibles = [r for s in SCHEMAS_LUS for r in relations(connexion, s)]
        cibles.append("public.schema_migrations")
        accordes = [c for c in cibles if privilege(connexion, "verif_ro", c, droit)]

    assert accordes == [], f"{droit} accordé sur {accordes}"


def test_verif_ro_n_a_aucun_droit_sur_les_sequences(base_migree):
    with psycopg.connect(base_migree) as connexion:
        sequences = [
            ligne[0]
            for ligne in connexion.execute(
                "SELECT n.nspname || '.' || c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname IN ('manga', 'bench') AND c.relkind = 'S'"
            ).fetchall()
        ]
        accordees = [
            (sequence, droit)
            for sequence in sequences
            for droit in ("USAGE", "UPDATE", "SELECT")
            if connexion.execute(
                "SELECT has_sequence_privilege('verif_ro', %s, %s)", (sequence, droit)
            ).fetchone()[0]
        ]

    assert sequences, "aucune séquence : le test ne prouverait rien"
    assert accordees == []


def test_staging_reste_hors_de_portee(base_migree):
    with psycopg.connect(base_migree) as connexion:
        (usage,) = connexion.execute(
            "SELECT has_schema_privilege('verif_ro', 'staging', 'USAGE')"
        ).fetchone()
        lisibles = [
            c
            for c in relations(connexion, "staging")
            if privilege(connexion, "verif_ro", c, "SELECT")
        ]

    assert usage is False
    assert lisibles == []


def test_manga_ro_reste_hors_de_bench(base_migree):
    """La raison d'être de 022 : l'API, qui hérite de manga_ro, ne lit rien de bench."""
    with psycopg.connect(base_migree) as connexion:
        (usage,) = connexion.execute(
            "SELECT has_schema_privilege('manga_ro', 'bench', 'USAGE')"
        ).fetchone()
        lisibles = [
            c
            for c in relations(connexion, "bench")
            if privilege(connexion, "manga_ro", c, "SELECT")
        ]
        (membre,) = connexion.execute(
            "SELECT pg_has_role('manga_ro', 'verif_ro', 'MEMBER')"
        ).fetchone()

    assert usage is False
    assert lisibles == []
    assert membre is False


@pytest.mark.parametrize("schema", SCHEMAS_LUS)
def test_022_couvre_les_objets_futurs(base_migree, schema):
    with psycopg.connect(base_migree, autocommit=True) as connexion:
        connexion.execute(f"CREATE TABLE {schema}.objet_futur_022 (id int)")
        cible = f"{schema}.objet_futur_022"
        lisible = privilege(connexion, "verif_ro", cible, "SELECT")
        ecrits = [d for d in ECRITURES if privilege(connexion, "verif_ro", cible, d)]

    assert lisible is True, "l'objet futur n'est pas lisible : le défaut n'a pas pris"
    assert ecrits == []


def test_022_est_idempotente(base_migree):
    """Rejeu direct du fichier : ni les ACL ni les défauts ne changent."""
    sql = (migrate.MIGRATIONS_DIR / "022_role_verification.sql").read_text(
        encoding="utf-8"
    )
    lecture_acl = """
        SELECT n.nspname, c.relname, c.relacl::text
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname IN ('manga', 'bench', 'public')
         ORDER BY 1, 2
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


# --------------------------------------------------------------------------- #
#  Le rôle de connexion, créé par le script versionné
# --------------------------------------------------------------------------- #
def lancer_script(
    dsn_admin: str, mot_de_passe: str | None
) -> subprocess.CompletedProcess:
    if shutil.which("psql") is None:
        pytest.fail(
            "le client psql est introuvable : le script ne peut pas être éprouvé"
        )
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    if mot_de_passe is not None:
        env["VERIF_LECTURE_PASSWORD"] = mot_de_passe
    return subprocess.run(
        ["sh", str(SCRIPT), dsn_admin],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_le_script_refuse_sans_mot_de_passe(base_migree):
    resultat = lancer_script(base_migree, None)

    assert resultat.returncode != 0
    assert "VERIF_LECTURE_PASSWORD n'est pas définie" in resultat.stderr


def test_le_role_de_connexion_lit_en_lecture_seule(base_migree):
    """Deux verrous : la session refuse d'écrire ; levée, les droits refusent."""
    mot_de_passe = secrets.token_urlsafe(24)
    resultat = lancer_script(base_migree, mot_de_passe)
    assert resultat.returncode == 0, resultat.stderr
    assert mot_de_passe not in resultat.stdout + resultat.stderr

    dsn = conninfo.make_conninfo(
        base_migree, user="verif_lecture", password=mot_de_passe
    )
    with psycopg.connect(dsn) as connexion:
        assert (
            connexion.execute("SHOW default_transaction_read_only").fetchone()[0]
            == "on"
        )
        connexion.execute("SELECT count(*) FROM bench.eval_mesures").fetchone()
        connexion.execute("SELECT count(*) FROM manga.ms_series_enriched").fetchone()
        connexion.execute("SELECT count(*) FROM public.schema_migrations").fetchone()
        (membre_api,) = connexion.execute(
            "SELECT pg_has_role('verif_lecture', 'manga_ro', 'MEMBER')"
        ).fetchone()
        assert membre_api is False

        with pytest.raises(errors.ReadOnlySqlTransaction):
            connexion.execute("DELETE FROM public.schema_migrations")
        connexion.rollback()

        connexion.execute("SET TRANSACTION READ WRITE")
        with pytest.raises(errors.InsufficientPrivilege):
            connexion.execute("DELETE FROM public.schema_migrations")
        connexion.rollback()


def test_la_migration_ne_cree_aucun_role_de_connexion():
    contenu = (migrate.MIGRATIONS_DIR / "022_role_verification.sql").read_text(
        encoding="utf-8"
    )
    assert "verif_lecture" not in contenu.split("--", 1)[0]
    assert "CREATE ROLE verif_lecture" not in contenu
    assert "LOGIN" not in contenu.replace("NOLOGIN", "")
