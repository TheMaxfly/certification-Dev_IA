"""Migration 029 — le compteur de questions par compte et par jour (E4, lot 2b).

`app_rw` lit, ajoute et incrémente un compteur ; il ne le supprime pas ; la suppression
d'un compte emporte ses compteurs ; le compteur ne descend pas sous zéro ; une ligne par
compte et par jour. Les droits sont éprouvés sous un rôle de connexion de TEST, membre
de `app_rw` (objet du conteneur jetable), comme ceux de la 027.
"""

from __future__ import annotations

import psycopg
import pytest
from conftest import harnais, migrate
from psycopg import errors
from test_schema_application import Role, compte, creer_role, refuse, sous

FICHIER = migrate.MIGRATIONS_DIR / "029_quota_comptes.sql"


@pytest.fixture(scope="module")
def role_app(conteneur_postgres: str, gabarit: str) -> Role:
    """Un rôle de connexion de test, membre de app_rw (créé par le gabarit)."""
    del gabarit
    return creer_role(conteneur_postgres, "essai_quota", "app_rw")


INCREMENT = (
    "INSERT INTO application.questions_jour (compte_id, jour, questions)"
    " VALUES (%s, %s, 1) ON CONFLICT (compte_id, jour)"
    " DO UPDATE SET questions = application.questions_jour.questions + 1"
)


def test_029_existe_et_ne_touche_que_sa_table():
    lignes = [
        ligne
        for ligne in FICHIER.read_text(encoding="utf-8").splitlines()
        if ligne.strip() and not ligne.lstrip().startswith("--")
    ]
    code = "\n".join(lignes).upper()
    ordres = [
        ligne.split()[0].upper()
        for ligne in lignes
        if not ligne.startswith((" ", ")", "'"))
    ]

    assert ordres == ["CREATE", "COMMENT", "GRANT"]
    assert "DROP" not in code and "ALTER" not in code
    assert "ON ALL TABLES" not in code


def test_029_passe_sur_une_base_vide(conteneur_postgres):
    with harnais.base_vide(conteneur_postgres) as dsn:
        harnais.migrer(dsn)
        with psycopg.connect(dsn) as cx:
            (table,) = cx.execute(
                "SELECT to_regclass('application.questions_jour')::text"
            ).fetchone()

    assert table == "application.questions_jour"


def test_029_les_droits_de_app_rw_exactement(base_migree):
    with psycopg.connect(base_migree) as cx:
        lus = {
            ligne[0]
            for ligne in cx.execute(
                "SELECT a.privilege_type FROM pg_class c, aclexplode(c.relacl) a"
                " WHERE c.oid = 'application.questions_jour'::regclass"
                "   AND a.grantee = 'app_rw'::regrole"
            ).fetchall()
        }
        (commentaire,) = cx.execute(
            "SELECT obj_description('application.questions_jour'::regclass)"
        ).fetchone()
        (proprietaire,) = cx.execute(
            "SELECT relowner::regrole::text FROM pg_class"
            " WHERE oid = 'application.questions_jour'::regclass"
        ).fetchone()

    assert lus == {"SELECT", "INSERT", "UPDATE"}
    assert "Aucun texte" in commentaire
    assert proprietaire == "postgres"


def test_029_app_rw_ajoute_et_incremente_un_compteur(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        client = compte(cx, "client_quota")
        for _ in range(3):
            cx.execute(INCREMENT, (client, "2026-11-08"))
        cx.execute(INCREMENT, (client, "2026-11-09"))
        lus = cx.execute(
            "SELECT jour::text, questions FROM application.questions_jour"
            " WHERE compte_id = %s ORDER BY jour",
            (client,),
        ).fetchall()

    assert lus == [("2026-11-08", 3), ("2026-11-09", 1)]


def test_029_app_rw_ne_supprime_pas_un_compteur(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        client = compte(cx, "client_quota_2")
        cx.execute(INCREMENT, (client, "2026-11-08"))
        cx.commit()
        refuse(cx, "DELETE FROM application.questions_jour")


def test_029_la_suppression_du_compte_emporte_ses_compteurs(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        client = compte(cx, "client_quota_3")
        cx.execute(INCREMENT, (client, "2026-11-08"))
        cx.execute("DELETE FROM application.comptes WHERE id = %s", (client,))
        (reste,) = cx.execute(
            "SELECT count(*) FROM application.questions_jour WHERE compte_id = %s",
            (client,),
        ).fetchone()

    assert reste == 0


def test_029_le_compteur_ne_descend_pas_sous_zero(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        client = compte(cx, "client_quota_4")
        with pytest.raises(errors.CheckViolation):
            cx.execute(
                "INSERT INTO application.questions_jour (compte_id, jour, questions)"
                " VALUES (%s, '2026-11-08', -1)",
                (client,),
            )


def test_029_un_compte_inconnu_est_refuse(base_migree, role_app):
    with sous(base_migree, role_app) as cx, pytest.raises(errors.ForeignKeyViolation):
        cx.execute(INCREMENT, (999999, "2026-11-08"))
