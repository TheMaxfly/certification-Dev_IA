"""Migration 027 — le schéma `application` et son rôle d'écriture `app_rw`.

En une phrase : `app_rw` écrit dans `application` selon une table de droits fermée, et
n'a rien ailleurs, pas même en lecture ; le journal est en ajout seul et une critique ne
se supprime pas, par les droits ; les contraintes refusent ce que l'application ne doit
jamais écrire.

Les droits sont éprouvés sous un rôle de connexion de TEST, membre de `app_rw` (objet
du conteneur jetable) : un refus est un `42501` réel, pas une lecture du catalogue
seule. L'inventaire des relations hors du schéma est dérivé du catalogue de la base
migrée, gardé par des SENTINELLES (planchers), comme `test_role_verification.py`.

Le rôle `app_rw` déjà présent (décision de Max du 8 novembre 2026) : absent, il est
créé ; présent et conforme, il est repris sans aucun `ALTER ROLE` ; présent avec un
attribut fautif, la migration échoue en le nommant.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
import pytest
from conftest import harnais, migrate
from psycopg import conninfo, errors, sql

FICHIER = migrate.MIGRATIONS_DIR / "027_schema_application.sql"

TABLES = ("comptes", "critiques", "journal_actions", "listes", "couts_jour", "reglages")
#: La table des droits de la spec 01 (§ 2.3), fermée : rien de plus, rien de moins.
DROITS = {
    "comptes": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "listes": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "critiques": {"SELECT", "INSERT", "UPDATE"},
    "journal_actions": {"SELECT", "INSERT"},
    "couts_jour": {"SELECT", "INSERT", "UPDATE"},
    "reglages": {"SELECT", "INSERT", "UPDATE"},
}
SCHEMAS_INTERDITS = ("manga", "bench", "staging", "public")
TOUS_LES_DROITS = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
#: Planchers de l'inventaire hors du schéma : ne peuvent que monter.
SENTINELLES = {"manga": 40, "bench": 21, "staging": 5, "public": 1}

ATTRIBUTS = {
    "LOGIN": "NOLOGIN",
    "SUPERUSER": "NOSUPERUSER",
    "CREATEDB": "NOCREATEDB",
    "CREATEROLE": "NOCREATEROLE",
    "REPLICATION": "NOREPLICATION",
    "BYPASSRLS": "NOBYPASSRLS",
}


@dataclass(frozen=True)
class Role:
    nom: str
    mot_de_passe: str


def creer_role(admin: str, prefixe: str, groupe: str | None) -> Role:
    role = Role(f"{prefixe}_{secrets.token_hex(4)}", secrets.token_urlsafe(18))
    ordre = "CREATE ROLE {} LOGIN PASSWORD {}" + (" IN ROLE {}" if groupe else "")
    morceaux = [sql.Identifier(role.nom), sql.Literal(role.mot_de_passe)]
    if groupe:
        morceaux.append(sql.Identifier(groupe))
    with psycopg.connect(admin, autocommit=True) as cx:
        cx.execute(sql.SQL(ordre).format(*morceaux))
    return role


@pytest.fixture(scope="session")
def role_app(conteneur_postgres: str, gabarit: str) -> Role:
    """Un rôle de connexion de test, membre de app_rw (créé par le gabarit)."""
    del gabarit
    return creer_role(conteneur_postgres, "essai_app", "app_rw")


@pytest.fixture(scope="session")
def role_hors(conteneur_postgres: str, gabarit: str) -> Role:
    """Un rôle de connexion de test, membre d'aucun groupe : PUBLIC seul."""
    del gabarit
    return creer_role(conteneur_postgres, "essai_hors", None)


@contextmanager
def sous(dsn: str, role: Role) -> Iterator[psycopg.Connection]:
    with psycopg.connect(
        conninfo.make_conninfo(dsn, user=role.nom, password=role.mot_de_passe)
    ) as cx:
        yield cx


def refuse(
    cx: psycopg.Connection, requete: str, params=(), erreur=errors.InsufficientPrivilege
) -> None:
    with pytest.raises(erreur):
        cx.execute(requete, params)
    cx.rollback()


def compte(
    cx: psycopg.Connection, pseudo: str = "client_essai", role: str = "client"
) -> int:
    return cx.execute(
        "INSERT INTO application.comptes (pseudo, nom_affiche, role, hachage)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pseudo, "Nom factice", role, "$argon2id$v=19$factice"),
    ).fetchone()[0]


def critique(
    cx: psycopg.Connection,
    compte_id: int,
    series_id: int = 101,
    volume_url: str | None = None,
    volume_libelle: str | None = None,
    note: int = 8,
    statut: str = "publiee",
) -> int:
    return cx.execute(
        "INSERT INTO application.critiques"
        " (compte_id, series_id, volume_url, volume_libelle, texte, note, statut)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            compte_id,
            series_id,
            volume_url,
            volume_libelle,
            "Texte factice.",
            note,
            statut,
        ),
    ).fetchone()[0]


def relations(cx: psycopg.Connection, schema: str) -> list[str]:
    return [
        ligne[0]
        for ligne in cx.execute(
            "SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname)"
            "  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = %s AND c.relkind IN ('r', 'v', 'm', 'p')"
            " ORDER BY 1",
            (schema,),
        ).fetchall()
    ]


def portrait_du_role(cx: psycopg.Connection) -> tuple:
    """Tout ce qu'un `ALTER ROLE` changerait, et les appartenances."""
    return cx.execute(
        "SELECT to_jsonb(r) - 'oid',"
        "       (SELECT array_agg(m.roleid::regrole::text ORDER BY"
        " m.roleid::regrole::text)"
        "          FROM pg_auth_members m WHERE m.member = r.oid),"
        "       shobj_description(r.oid, 'pg_authid')"
        "  FROM pg_roles r WHERE r.rolname = 'app_rw'"
    ).fetchone()


# --------------------------------------------------------------------------- #
#  Le rôle de groupe : absent, présent et conforme, présent et fautif
# --------------------------------------------------------------------------- #
def test_027_cree_app_rw_quand_il_est_absent():
    """Un serveur neuf : app_rw n'existe pas, la migration le crée NOLOGIN, nu."""
    raison = harnais.raison_de_sauter()
    if raison:
        pytest.skip(raison)
    with harnais.conteneur("app-rw-absent") as admin:
        with psycopg.connect(admin) as cx:
            absent = cx.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw')"
            ).fetchone()[0]
        with harnais.base_vide(admin) as dsn:
            harnais.migrer(dsn)
            with psycopg.connect(dsn) as cx:
                attributs = cx.execute(
                    "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,"
                    "       rolreplication, rolbypassrls"
                    "  FROM pg_roles WHERE rolname = 'app_rw'"
                ).fetchone()
                (groupes,) = cx.execute(
                    "SELECT count(*) FROM pg_auth_members WHERE member ="
                    " 'app_rw'::regrole"
                ).fetchone()

    assert absent, "le serveur neuf portait déjà app_rw : le test ne prouverait rien"
    assert attributs == (False, False, False, False, False, False)
    assert groupes == 0


def test_027_reprend_app_rw_conforme_sans_le_modifier(conteneur_postgres, gabarit):
    """Le rôle existe (le gabarit l'a créé) : la migration passe et ne le touche pas —
    un commentaire témoin, posé avant, survit au rejeu (ni ALTER, ni COMMENT)."""
    with psycopg.connect(conteneur_postgres, autocommit=True) as cx:
        (origine,) = cx.execute(
            "SELECT shobj_description('app_rw'::regrole, 'pg_authid')"
        ).fetchone()
        cx.execute("COMMENT ON ROLE app_rw IS 'témoin du rejeu'")
        avant = portrait_du_role(cx)
    try:
        with harnais.base_vide(conteneur_postgres) as dsn:
            harnais.migrer(dsn)
            with psycopg.connect(dsn) as cx:
                (schema,) = cx.execute(
                    "SELECT count(*) FROM pg_namespace WHERE nspname = 'application'"
                ).fetchone()
                apres = portrait_du_role(cx)
    finally:
        with psycopg.connect(conteneur_postgres, autocommit=True) as cx:
            cx.execute(
                sql.SQL("COMMENT ON ROLE app_rw IS {}").format(sql.Literal(origine))
            )

    assert avant is not None and avant[0]["rolname"] == "app_rw"
    assert origine and "application du libraire" in origine
    assert schema == 1
    assert apres == avant
    assert apres[2] == "témoin du rejeu"


@pytest.mark.parametrize("attribut", sorted(ATTRIBUTS))
def test_027_refuse_app_rw_avec_un_attribut_fautif(
    conteneur_postgres, gabarit, attribut
):
    with harnais.base_vide(conteneur_postgres) as dsn:
        harnais.migrer(dsn, "026")
        with psycopg.connect(conteneur_postgres, autocommit=True) as cx:
            cx.execute(f"ALTER ROLE app_rw {attribut}")
        try:
            with pytest.raises(errors.RaiseException) as refus:
                harnais.migrer(dsn, "027")
        finally:
            with psycopg.connect(conteneur_postgres, autocommit=True) as cx:
                cx.execute(f"ALTER ROLE app_rw {ATTRIBUTS[attribut]}")
        with psycopg.connect(dsn) as cx:
            (version,) = cx.execute(
                "SELECT max(version) FROM public.schema_migrations"
            ).fetchone()
            (schema,) = cx.execute(
                "SELECT count(*) FROM pg_namespace WHERE nspname = 'application'"
            ).fetchone()

    assert f"avec {attribut}" in str(refus.value)
    assert version == "026" and schema == 0, "la migration refusée a laissé une trace"


def test_027_refuse_app_rw_membre_d_un_role(conteneur_postgres, gabarit):
    groupe = f"essai_groupe_{secrets.token_hex(4)}"
    with harnais.base_vide(conteneur_postgres) as dsn:
        harnais.migrer(dsn, "026")
        with psycopg.connect(conteneur_postgres, autocommit=True) as cx:
            cx.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(groupe)))
            cx.execute(sql.SQL("GRANT {} TO app_rw").format(sql.Identifier(groupe)))
        try:
            with pytest.raises(errors.RaiseException) as refus:
                harnais.migrer(dsn, "027")
        finally:
            with psycopg.connect(conteneur_postgres, autocommit=True) as cx:
                cx.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(groupe)))
        with psycopg.connect(dsn) as cx:
            (version,) = cx.execute(
                "SELECT max(version) FROM public.schema_migrations"
            ).fetchone()
            (schema,) = cx.execute(
                "SELECT count(*) FROM pg_namespace WHERE nspname = 'application'"
            ).fetchone()

    assert f"membre de {groupe}" in str(refus.value)
    assert version == "026" and schema == 0, "la migration refusée a laissé une trace"


def test_la_migration_ne_cree_aucun_role_de_connexion():
    code = "\n".join(
        ligne
        for ligne in FICHIER.read_text(encoding="utf-8").splitlines()
        if not ligne.lstrip().startswith("--")
    )
    assert "ROLE app_conseiller" not in code
    assert "PASSWORD" not in code
    assert code.count("CREATE ROLE") == 1
    assert "CREATE ROLE app_rw NOLOGIN" in code
    assert "ALTER ROLE" not in code


# --------------------------------------------------------------------------- #
#  Le schéma, ses tables, les droits lus dans le catalogue
# --------------------------------------------------------------------------- #
def test_027_cree_le_schema_et_ses_six_tables(base_migree):
    with psycopg.connect(base_migree) as cx:
        tables = [r.split(".")[1] for r in relations(cx, "application")]
        (proprietaire,) = cx.execute(
            "SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname ="
            " 'application'"
        ).fetchone()

    assert sorted(tables) == sorted(TABLES)
    assert proprietaire == "postgres"


def test_droits_de_app_rw_par_table(base_migree):
    """La table du § 2.3, exactement : un droit en plus ou en moins est un échec."""
    with psycopg.connect(base_migree) as cx:
        lus = {
            table: {
                ligne[0]
                for ligne in cx.execute(
                    "SELECT a.privilege_type FROM pg_class c,"
                    "       aclexplode(c.relacl) a"
                    " WHERE c.oid = %s::regclass AND a.grantee = 'app_rw'::regrole",
                    (f"application.{table}",),
                ).fetchall()
            }
            for table in TABLES
        }

    assert lus == DROITS


def test_app_rw_a_usage_sur_le_schema_et_connect_sur_la_base(base_migree):
    with psycopg.connect(base_migree) as cx:
        schema = cx.execute(
            "SELECT array_agg(a.privilege_type ORDER BY 1) FROM pg_namespace n,"
            "       aclexplode(n.nspacl) a"
            " WHERE n.nspname = 'application' AND a.grantee = 'app_rw'::regrole"
        ).fetchone()[0]
        base = cx.execute(
            "SELECT array_agg(a.privilege_type ORDER BY 1) FROM pg_database d,"
            "       aclexplode(d.datacl) a"
            " WHERE d.datname = current_database() AND a.grantee = 'app_rw'::regrole"
        ).fetchone()[0]
        public = cx.execute(
            "SELECT count(*) FROM pg_namespace n, aclexplode(n.nspacl) a"
            " WHERE n.nspname = 'application' AND a.grantee = 0"
        ).fetchone()[0]

    assert schema == ["USAGE"]
    assert base == ["CONNECT"]
    assert public == 0, "PUBLIC garde un droit sur le schéma application"


def test_app_rw_n_a_aucun_droit_sur_les_sequences(base_migree):
    with psycopg.connect(base_migree) as cx:
        sequences = [
            ligne[0]
            for ligne in cx.execute(
                "SELECT c.oid::regclass::text FROM pg_class c"
                " WHERE c.relnamespace = 'application'::regnamespace AND c.relkind ="
                " 'S'"
            ).fetchall()
        ]
        droits = [
            (s, d)
            for s in sequences
            for d in ("USAGE", "SELECT", "UPDATE")
            if cx.execute(
                "SELECT has_sequence_privilege('app_rw', %s, %s)", (s, d)
            ).fetchone()[0]
        ]

    assert len(sequences) == 3, sequences  # comptes, critiques, journal_actions
    assert droits == []


def test_app_rw_n_a_aucun_droit_hors_du_schema(base_migree):
    """Ni sur une relation, ni sur un schéma, ni par défaut, hors d'application."""
    with psycopg.connect(base_migree) as cx:
        (relations_hors,) = cx.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid ="
            " c.relnamespace,"
            "       aclexplode(c.relacl) a"
            " WHERE n.nspname <> 'application' AND a.grantee = 'app_rw'::regrole"
        ).fetchone()
        (schemas_hors,) = cx.execute(
            "SELECT count(*) FROM pg_namespace n, aclexplode(n.nspacl) a"
            " WHERE n.nspname <> 'application' AND a.grantee = 'app_rw'::regrole"
        ).fetchone()
        (par_defaut,) = cx.execute(
            "SELECT count(*) FROM pg_default_acl d, aclexplode(d.defaclacl) a"
            " WHERE a.grantee = 'app_rw'::regrole"
        ).fetchone()

    assert (relations_hors, schemas_hors, par_defaut) == (0, 0, 0)


@pytest.mark.parametrize("schema", SCHEMAS_INTERDITS)
def test_app_rw_n_a_aucun_droit_effectif_dans(base_migree, schema):
    """Les droits EFFECTIFS, ceux de PUBLIC compris, par relation et par colonne."""
    with psycopg.connect(base_migree) as cx:
        inventaire = relations(cx, schema)
        tables = [
            (r, d)
            for r in inventaire
            for d in TOUS_LES_DROITS
            if cx.execute(
                "SELECT has_table_privilege('app_rw', %s, %s)", (r, d)
            ).fetchone()[0]
        ]
        colonnes = [
            (r, d)
            for r in inventaire
            for d in ("SELECT", "INSERT", "UPDATE", "REFERENCES")
            if cx.execute(
                "SELECT has_any_column_privilege('app_rw', %s, %s)", (r, d)
            ).fetchone()[0]
        ]

    assert len(inventaire) >= SENTINELLES[schema], (schema, len(inventaire))
    assert tables == [] and colonnes == []


# --------------------------------------------------------------------------- #
#  Les droits accordés, qui marchent (sous un rôle membre de app_rw)
# --------------------------------------------------------------------------- #
def test_app_rw_ecrit_lit_et_supprime_comptes_et_listes(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        ident = compte(cx)
        cx.execute(
            "UPDATE application.comptes SET echecs_connexion = 1 WHERE id = %s",
            (ident,),
        )
        cx.execute(
            "INSERT INTO application.listes (compte_id, series_id, etat) VALUES (%s,"
            " 101, 'favori')",
            (ident,),
        )
        cx.execute(
            "UPDATE application.listes SET etat = 'a_lire' WHERE compte_id = %s",
            (ident,),
        )
        lu = cx.execute(
            "SELECT c.echecs_connexion, l.etat FROM application.comptes c"
            " JOIN application.listes l ON l.compte_id = c.id WHERE c.id = %s",
            (ident,),
        ).fetchone()
        cx.execute("DELETE FROM application.listes WHERE compte_id = %s", (ident,))
        cx.execute("DELETE FROM application.comptes WHERE id = %s", (ident,))
        (reste,) = cx.execute("SELECT count(*) FROM application.comptes").fetchone()

    assert lu == (1, "a_lire")
    assert reste == 0


def test_app_rw_publie_modifie_et_retire_une_critique(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        auteur = compte(cx, "libraire_essai", "libraire")
        ident = critique(
            cx, auteur, volume_url="https://exemple.invalid/t1", volume_libelle="Tome 1"
        )
        cx.execute(
            "UPDATE application.critiques SET note = 9, statut = 'retiree' WHERE id"
            " = %s",
            (ident,),
        )
        lu = cx.execute(
            "SELECT note, statut FROM application.critiques WHERE id = %s", (ident,)
        ).fetchone()

    assert lu == (9, "retiree")


def test_app_rw_ajoute_au_journal_et_le_lit(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        auteur = compte(cx, "admin_essai", "admin")
        cx.execute(
            "INSERT INTO application.journal_actions (par_compte_id, action,"
            " objet_type, objet_id)"
            " VALUES (%s, 'compte_cree', 'compte', %s)",
            (auteur, auteur),
        )
        lu = cx.execute(
            "SELECT action, details FROM application.journal_actions WHERE"
            " par_compte_id = %s",
            (auteur,),
        ).fetchone()

    assert lu == ("compte_cree", {})


def test_app_rw_ecrit_couts_et_reglages(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        auteur = compte(cx, "admin_essai", "admin")
        cx.execute(
            "INSERT INTO application.couts_jour (jour, profil, mode, appels, cout_usd)"
            " VALUES ('2026-11-08', 'profil_factice', 'strict', 1, 0.0018)"
        )
        cx.execute(
            "UPDATE application.couts_jour SET appels = appels + 1, cout_usd ="
            " cout_usd + 0.002"
            " WHERE jour = '2026-11-08'"
        )
        cx.execute(
            "INSERT INTO application.reglages (cle, valeur, modifie_par)"
            " VALUES ('profil_actif', 'profil_factice', %s)",
            (auteur,),
        )
        cx.execute(
            "UPDATE application.reglages SET valeur = 'autre' WHERE cle ="
            " 'profil_actif'"
        )
        couts = cx.execute(
            "SELECT appels, cout_usd::text FROM application.couts_jour"
        ).fetchone()
        reglage = cx.execute("SELECT valeur FROM application.reglages").fetchone()

    assert couts == (2, "0.003800")
    assert reglage == ("autre",)


# --------------------------------------------------------------------------- #
#  Les refus 42501
# --------------------------------------------------------------------------- #
def test_refus_update_sur_le_journal(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        cx.execute(
            "INSERT INTO application.journal_actions (action) VALUES ('compte_cree')"
        )
        cx.commit()
        refuse(cx, "UPDATE application.journal_actions SET action = 'compte_supprime'")


def test_refus_delete_sur_le_journal(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        cx.execute(
            "INSERT INTO application.journal_actions (action) VALUES ('compte_cree')"
        )
        cx.commit()
        refuse(cx, "DELETE FROM application.journal_actions")
        refuse(cx, "TRUNCATE application.journal_actions")


def test_refus_delete_sur_les_critiques(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        critique(cx, compte(cx, "libraire_essai", "libraire"))
        cx.commit()
        refuse(cx, "DELETE FROM application.critiques")
        refuse(cx, "TRUNCATE application.critiques")


@pytest.mark.parametrize("schema", SCHEMAS_INTERDITS)
def test_app_rw_ne_lit_rien_dans(base_migree, role_app, schema):
    with psycopg.connect(base_migree) as cx:
        inventaire = relations(cx, schema)
    assert len(inventaire) >= SENTINELLES[schema], (schema, len(inventaire))

    acceptees = []
    with sous(base_migree, role_app) as cx:
        for relation in inventaire:
            try:
                cx.execute(f"SELECT 1 FROM {relation} LIMIT 1")
                acceptees.append(relation)
            except errors.InsufficientPrivilege:
                pass
            cx.rollback()

    assert acceptees == []


@pytest.mark.parametrize("schema", SCHEMAS_INTERDITS)
def test_app_rw_n_ecrit_rien_dans(base_migree, role_app, schema):
    with psycopg.connect(base_migree) as cx:
        # Chaque table et sa première colonne : `UPDATE … SET col = NULL WHERE false`
        # n'exige que le droit d'écrire (`SET col = col` exigerait aussi de lire).
        tables = cx.execute(
            "SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname),"
            "       (SELECT quote_ident(a.attname) FROM pg_attribute a"
            "         WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped"
            "         ORDER BY a.attnum LIMIT 1)"
            "  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = %s AND c.relkind IN ('r', 'p') ORDER BY 1",
            (schema,),
        ).fetchall()
    assert tables, schema

    acceptees = []
    with sous(base_migree, role_app) as cx:
        for table, colonne in tables:
            for ordre in (
                f"INSERT INTO {table} DEFAULT VALUES",
                f"UPDATE {table} SET {colonne} = NULL WHERE false",
                f"DELETE FROM {table}",
                f"TRUNCATE {table}",
            ):
                try:
                    cx.execute(ordre)
                    acceptees.append(ordre)
                except errors.InsufficientPrivilege:
                    pass
                cx.rollback()

    assert acceptees == []


def test_public_n_accede_pas_au_schema_application(base_migree, role_hors):
    with sous(base_migree, role_hors) as cx:
        refuse(cx, "SELECT 1 FROM application.comptes")
        refuse(
            cx,
            "INSERT INTO application.comptes (pseudo, nom_affiche, role, hachage)"
            " VALUES ('intrus', 'intrus', 'admin', 'x')",
        )


# --------------------------------------------------------------------------- #
#  Les contraintes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "pseudo",
    ("ab", "x" * 31, "Majuscule", "avec espace", "accentué", "point.final", ""),
)
def test_pseudo_hors_motif_refuse(base_migree, role_app, pseudo):
    with sous(base_migree, role_app) as cx:
        with pytest.raises(errors.CheckViolation, match="comptes_pseudo_forme"):
            compte(cx, pseudo)


def test_pseudo_aux_bornes_accepte(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        compte(cx, "abc")
        compte(cx, "a" * 30)
        compte(cx, "tiret-et_souligne-0")


def test_pseudo_deja_pris_refuse(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        compte(cx, "client_essai")
        with pytest.raises(errors.UniqueViolation):
            compte(cx, "client_essai")


# (table, colonnes et valeurs fautives, contrainte nommée)
FAUTIFS = [
    (
        "role inconnu",
        "INSERT INTO application.comptes (pseudo, nom_affiche, role, hachage)"
        " VALUES ('client_essai', 'Client', 'lecteur', 'h')",
        "comptes_role_connu",
    ),
    (
        "nom affiché vide",
        "INSERT INTO application.comptes (pseudo, nom_affiche, role, hachage)"
        " VALUES ('client_essai', '', 'client', 'h')",
        "comptes_nom_affiche_longueur",
    ),
    (
        "nom affiché trop long",
        "INSERT INTO application.comptes (pseudo, nom_affiche, role, hachage)"
        f" VALUES ('client_essai', '{'n' * 61}', 'client', 'h')",
        "comptes_nom_affiche_longueur",
    ),
    (
        "génération négative",
        "INSERT INTO application.comptes"
        " (pseudo, nom_affiche, role, hachage, generation)"
        " VALUES ('client_essai', 'Client', 'client', 'h', -1)",
        "comptes_generation_positive",
    ),
    (
        "échecs négatifs",
        "INSERT INTO application.comptes"
        " (pseudo, nom_affiche, role, hachage, echecs_connexion)"
        " VALUES ('client_essai', 'Client', 'client', 'h', -1)",
        "comptes_echecs_positifs",
    ),
    (
        "état de liste inconnu",
        "INSERT INTO application.listes (compte_id, series_id, etat)"
        " VALUES ({compte}, 101, 'lu')",
        "listes_etat_connu",
    ),
    (
        "mode de coût inconnu",
        "INSERT INTO application.couts_jour (jour, profil, mode)"
        " VALUES ('2026-11-08', 'p', 'libre')",
        "couts_mode_connu",
    ),
    (
        "appels négatifs",
        "INSERT INTO application.couts_jour (jour, profil, mode, appels)"
        " VALUES ('2026-11-08', 'p', 'strict', -1)",
        "couts_appels_positifs",
    ),
    (
        "coût négatif",
        "INSERT INTO application.couts_jour (jour, profil, mode, cout_usd)"
        " VALUES ('2026-11-08', 'p', 'strict', -0.1)",
        "couts_montant_positif",
    ),
    (
        "clé de réglage hors motif",
        "INSERT INTO application.reglages (cle, valeur) VALUES ('Profil-Actif', 'x')",
        "reglages_cle_forme",
    ),
    (
        "action de journal inconnue",
        "INSERT INTO application.journal_actions (action) VALUES ('connexion')",
        "journal_action_connue",
    ),
    (
        "détails qui ne sont pas un objet",
        "INSERT INTO application.journal_actions"
        " (action, details) VALUES ('compte_cree', '\"texte libre\"')",
        "journal_details_objet",
    ),
]


@pytest.mark.parametrize(
    ("cas", "ordre", "contrainte"), FAUTIFS, ids=[f[0] for f in FAUTIFS]
)
def test_valeur_fautive_refusee(base_migree, role_app, cas, ordre, contrainte):
    del cas
    with sous(base_migree, role_app) as cx:
        ident = compte(cx, "titulaire_essai")
        with pytest.raises(errors.CheckViolation, match=contrainte):
            cx.execute(ordre.replace("{compte}", str(ident)))


@pytest.mark.parametrize("note", (-1, 11))
def test_note_hors_de_0_a_10_refusee(base_migree, role_app, note):
    with sous(base_migree, role_app) as cx:
        auteur = compte(cx, "libraire_essai", "libraire")
        with pytest.raises(errors.CheckViolation, match="critiques_note_sur_dix"):
            critique(cx, auteur, note=note)


@pytest.mark.parametrize("note", (0, 10))
def test_note_aux_bornes_acceptee(base_migree, role_app, note):
    with sous(base_migree, role_app) as cx:
        critique(cx, compte(cx, "libraire_essai", "libraire"), note=note)


def test_statut_de_critique_inconnu_refuse(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        auteur = compte(cx, "libraire_essai", "libraire")
        with pytest.raises(errors.CheckViolation, match="critiques_statut_connu"):
            critique(cx, auteur, statut="supprimee")


@pytest.mark.parametrize(
    ("volume_url", "volume_libelle"),
    (("https://exemple.invalid/t1", None), (None, "Tome 1")),
    ids=("adresse sans libellé", "libellé sans adresse"),
)
def test_tome_incoherent_refuse(base_migree, role_app, volume_url, volume_libelle):
    with sous(base_migree, role_app) as cx:
        auteur = compte(cx, "libraire_essai", "libraire")
        with pytest.raises(errors.CheckViolation, match="critiques_tome_complet"):
            critique(cx, auteur, volume_url=volume_url, volume_libelle=volume_libelle)


@pytest.mark.parametrize(
    ("ordre", "contrainte"),
    (
        ("UPDATE application.critiques SET texte = ''", "critiques_texte_longueur"),
        (
            f"UPDATE application.critiques SET texte = '{'t' * 10001}'",
            "critiques_texte_longueur",
        ),
        (
            "UPDATE application.critiques SET label = 'Coup-de-coeur'",
            "critiques_label_forme",
        ),
    ),
    ids=("texte vide", "texte trop long", "label hors motif"),
)
def test_critique_hors_bornes_refusee(base_migree, role_app, ordre, contrainte):
    with sous(base_migree, role_app) as cx:
        critique(cx, compte(cx, "libraire_essai", "libraire"))
        with pytest.raises(errors.CheckViolation, match=contrainte):
            cx.execute(ordre)


@pytest.mark.parametrize(
    "tome",
    (None, ("https://exemple.invalid/t1", "Tome 1")),
    ids=("critique de série", "critique de tome"),
)
def test_deux_critiques_publiees_pour_le_meme_tome_refusees(
    base_migree, role_app, tome
):
    url, libelle = tome or (None, None)
    with sous(base_migree, role_app) as cx:
        auteur = compte(cx, "libraire_essai", "libraire")
        critique(cx, auteur, volume_url=url, volume_libelle=libelle)
        with pytest.raises(errors.UniqueViolation, match="critiques_une_publiee"):
            critique(cx, auteur, volume_url=url, volume_libelle=libelle)


def test_la_meme_critique_apres_retrait_acceptee(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        auteur = compte(cx, "libraire_essai", "libraire")
        premiere = critique(cx, auteur)
        cx.execute(
            "UPDATE application.critiques SET statut = 'retiree' WHERE id = %s",
            (premiere,),
        )
        seconde = critique(cx, auteur)
        (publiees,) = cx.execute(
            "SELECT count(*) FROM application.critiques WHERE statut = 'publiee'"
        ).fetchone()

    assert seconde != premiere
    assert publiees == 1


def test_deux_libraires_sur_la_meme_serie_acceptes(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        critique(cx, compte(cx, "libraire_un", "libraire"))
        critique(cx, compte(cx, "libraire_deux", "libraire"))


def test_suppression_d_un_compte_qui_a_des_critiques_refusee(base_migree, role_app):
    with sous(base_migree, role_app) as cx:
        auteur = compte(cx, "libraire_essai", "libraire")
        critique(cx, auteur)
        with pytest.raises(errors.ForeignKeyViolation):
            cx.execute("DELETE FROM application.comptes WHERE id = %s", (auteur,))


def test_suppression_d_un_client_emporte_ses_listes_et_garde_le_journal(
    base_migree, role_app
):
    """Sous app_rw, qui n'a pas UPDATE sur le journal : la mise à nul de la clé
    étrangère est faite par la base, pas par un droit du rôle."""
    with sous(base_migree, role_app) as cx:
        client = compte(cx)
        cx.execute(
            "INSERT INTO application.listes (compte_id, series_id, etat) VALUES (%s,"
            " 101, 'favori'),"
            " (%s, 102, 'a_commander')",
            (client, client),
        )
        cx.execute(
            "INSERT INTO application.journal_actions (par_compte_id, action,"
            " objet_type, objet_id)"
            " VALUES (%s, 'compte_cree', 'compte', %s)",
            (client, client),
        )
        cx.execute("DELETE FROM application.comptes WHERE id = %s", (client,))
        (listes,) = cx.execute("SELECT count(*) FROM application.listes").fetchone()
        journal = cx.execute(
            "SELECT par_compte_id, objet_id FROM application.journal_actions"
        ).fetchall()

    assert listes == 0
    assert journal == [(None, client)]
