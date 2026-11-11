"""Le harnais des bases jetables, branché sur l'application (E4, lot 1, spec 01).

La base du test est migrée par le runner du dépôt (027 comprise) ; l'application y
tourne sous un rôle de connexion de test membre de `app_rw` seul, sans mot de passe
dans le DSN : ce qu'elle écrit, elle doit pouvoir l'écrire sous ce rôle, et rien
d'autre.
"""

from __future__ import annotations

import psycopg
import pytest
from conftest import ROLE_APPLICATION
from psycopg import conninfo, errors


def test_le_gabarit_porte_le_schema_de_l_application(base):
    with psycopg.connect(base) as cx:
        tables = {
            ligne[0]
            for ligne in cx.execute(
                "SELECT relname FROM pg_class"
                " WHERE relnamespace = 'application'::regnamespace AND relkind = 'r'"
            ).fetchall()
        }
        (derniere,) = cx.execute(
            "SELECT max(version) FROM public.schema_migrations"
        ).fetchone()

    assert tables == {
        "comptes",
        "critiques",
        "journal_actions",
        "listes",
        "couts_jour",
        "reglages",
        "questions_jour",  # 029, E4, lot 2b : le quota des comptes clients
    }
    assert derniere >= "029"


def test_le_dsn_de_l_application_ne_porte_aucun_mot_de_passe(base_app):
    parametres = conninfo.conninfo_to_dict(base_app)
    assert "password" not in parametres
    assert parametres["user"] == ROLE_APPLICATION


def test_le_role_de_l_application_ecrit_dans_son_schema(base_app):
    with psycopg.connect(base_app) as cx:
        (role, membre, superutilisateur) = cx.execute(
            "SELECT current_user, pg_has_role('app_rw', 'MEMBER'),"
            " (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)"
        ).fetchone()
        cx.execute(
            "INSERT INTO application.comptes (pseudo, nom_affiche, role, hachage)"
            " VALUES (%s, %s, %s, %s)",
            ("client_essai", "Client", "client", "$argon2id$factice"),
        )
        (comptes,) = cx.execute("SELECT count(*) FROM application.comptes").fetchone()

    assert (role, membre, superutilisateur) == (ROLE_APPLICATION, True, False)
    assert comptes == 1


@pytest.mark.parametrize(
    "relation", ["manga.ms_series_enriched", "bench.corpus", "public.schema_migrations"]
)
def test_le_role_de_l_application_ne_lit_rien_hors_de_son_schema(base_app, relation):
    with psycopg.connect(base_app) as cx:
        with pytest.raises(errors.InsufficientPrivilege):
            cx.execute(f"SELECT 1 FROM {relation} LIMIT 1")
