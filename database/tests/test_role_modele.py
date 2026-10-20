"""Migration 023 — le rôle de lecture de l'API du modèle, `modele_ro`, et son rôle de
connexion `modele_api`.

En une phrase : `modele_ro` lit six relations de `bench` et quatre colonnes de `manga`,
énumérées, et rien d'autre ; aucun droit par défaut ; `manga_ro` et `verif_ro` n'en
sont pas changés. Le rôle de connexion, créé par `outils/creer_role_modele.sh`,
hérite de `modele_ro`, n'ouvre que des sessions en lecture seule et ne prend pas plus
de dix connexions.

Même méthode que `test_role_verification.py` : l'inventaire est dérivé du catalogue
de la base migrée, gardé par des SENTINELLES (planchers), pour qu'un inventaire
effondré ne rende pas toutes les assertions vraies en silence. Les six refus sont
prouvés par le rôle de connexion, sa session forcée en écriture : ce sont alors les
droits qui refusent, pas le verrou de session.
"""

from __future__ import annotations

import ast
import os
import secrets
import shutil
import subprocess
from argparse import Namespace

import psycopg
import pytest
from conftest import RACINE, harnais, migrate
from psycopg import conninfo, errors, sql

UP = Namespace(commande="up", target=None)
SCRIPT = RACINE / "outils" / "creer_role_modele.sh"
FICHIER = migrate.MIGRATIONS_DIR / "023_role_modele.sql"
ATTEIGNABILITE = (
    RACINE.parent
    / "05_nettoyage_agregation_bdd"
    / "src"
    / "evaluation"
    / "atteignabilite.py"
)

#: Le périmètre accordé, énuméré.
RELATIONS_BENCH = (
    "bench.corpus",
    "bench.corpus_docs",
    "bench.corpus_chunks",
    "bench.encodages",
    "bench.vecteurs_embeddinggemma",
    "bench.v_encodage_en_service",
)
COLONNES_MANGA = {
    "manga.work_identity": ("series_id", "kitsu_id"),
    "manga.ms_series_enriched": ("series_id", "series_title"),
}
#: Les tables du périmètre : là où les écritures doivent être refusées.
TABLES_ACCORDEES = (
    *(r for r in RELATIONS_BENCH if r != "bench.v_encodage_en_service"),
    *COLONNES_MANGA,
)

# Planchers : 20 tables et 1 vue dans bench au 023 ; ne peut que monter.
SENTINELLE_RELATIONS_BENCH = 21
SENTINELLE_RELATIONS_MANGA = 40
SENTINELLES_HORS_PERIMETRE = (
    "bench.promotions",
    "bench.eval_mesures",
    "bench.vecteurs_bge_m3",
    "manga.ms_reviews_all",
)

ECRITURES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
ECRITURES_COLONNES = ("INSERT", "UPDATE", "REFERENCES")
SCHEMAS = ("bench", "manga", "staging")


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


def tout_l_inventaire(connexion) -> list[str]:
    return [r for s in SCHEMAS for r in relations(connexion, s)] + [
        "public.schema_migrations"
    ]


def colonnes(connexion, relation: str) -> list[str]:
    return [
        ligne[0]
        for ligne in connexion.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass"
            " AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
            (relation,),
        ).fetchall()
    ]


def colonne_modifiable(connexion, relation: str) -> str:
    """La première colonne qu'un UPDATE peut viser (ni identité, ni générée)."""
    return connexion.execute(
        "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass"
        " AND attnum > 0 AND NOT attisdropped AND attidentity = ''"
        " AND attgenerated = '' ORDER BY attnum LIMIT 1",
        (relation,),
    ).fetchone()[0]


def table(connexion, role: str, relation: str, droit: str) -> bool:
    return connexion.execute(
        "SELECT has_table_privilege(%s, %s, %s)", (role, relation, droit)
    ).fetchone()[0]


def colonne(connexion, role: str, relation: str, nom: str, droit: str) -> bool:
    return connexion.execute(
        "SELECT has_column_privilege(%s, %s, %s, %s)", (role, relation, nom, droit)
    ).fetchone()[0]


def une_colonne(connexion, role: str, relation: str, droit: str) -> bool:
    return connexion.execute(
        "SELECT has_any_column_privilege(%s, %s, %s)", (role, relation, droit)
    ).fetchone()[0]


def ident(relation: str) -> sql.Identifier:
    return sql.Identifier(*relation.split("."))


# --------------------------------------------------------------------------- #
#  Le rôle de groupe et son périmètre
# --------------------------------------------------------------------------- #
def test_023_cree_le_role_de_groupe_sans_attribut(base_migree):
    with psycopg.connect(base_migree) as connexion:
        ligne = connexion.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls,"
            " rolinherit FROM pg_roles WHERE rolname = 'modele_ro'"
        ).fetchone()

    assert ligne is not None, "modele_ro est absent après la migration 023"
    assert ligne == (False, False, False, False, False, True)


def test_023_accorde_connect_sur_la_base_courante(base_migree):
    with psycopg.connect(base_migree) as connexion:
        (connect,) = connexion.execute(
            "SELECT has_database_privilege('modele_ro', current_database(), 'CONNECT')"
        ).fetchone()

    assert connect is True


def test_inventaire_derive_reste_sous_temoin(base_migree):
    with psycopg.connect(base_migree) as connexion:
        bench = relations(connexion, "bench")
        manga = relations(connexion, "manga")

    assert len(bench) >= SENTINELLE_RELATIONS_BENCH, f"{len(bench)} dans bench"
    assert len(manga) >= SENTINELLE_RELATIONS_MANGA, f"{len(manga)} dans manga"
    for connue in (*RELATIONS_BENCH, *COLONNES_MANGA, *SENTINELLES_HORS_PERIMETRE):
        assert connue in bench + manga, connue


def test_modele_ro_lit_ses_six_relations_de_bench(base_migree):
    with psycopg.connect(base_migree) as connexion:
        illisibles = [
            r for r in RELATIONS_BENCH if not table(connexion, "modele_ro", r, "SELECT")
        ]

    assert illisibles == []


def test_modele_ro_lit_ses_quatre_colonnes_de_manga_et_elles_seules(base_migree):
    with psycopg.connect(base_migree) as connexion:
        lisibles = {
            relation: [
                c
                for c in colonnes(connexion, relation)
                if colonne(connexion, "modele_ro", relation, c, "SELECT")
            ]
            for relation in COLONNES_MANGA
        }
        tables_entieres = [
            r for r in COLONNES_MANGA if table(connexion, "modele_ro", r, "SELECT")
        ]

    assert lisibles == {r: list(c) for r, c in COLONNES_MANGA.items()}
    assert tables_entieres == [], "un droit de table, au lieu des colonnes"


def test_rien_d_autre_n_est_lisible(base_migree):
    perimetre = set(RELATIONS_BENCH) | set(COLONNES_MANGA)
    with psycopg.connect(base_migree) as connexion:
        hors = [r for r in tout_l_inventaire(connexion) if r not in perimetre]
        lisibles = [
            r
            for r in hors
            if table(connexion, "modele_ro", r, "SELECT")
            or une_colonne(connexion, "modele_ro", r, "SELECT")
        ]
        (usage_staging,) = connexion.execute(
            "SELECT has_schema_privilege('modele_ro', 'staging', 'USAGE')"
        ).fetchone()

    assert set(SENTINELLES_HORS_PERIMETRE) <= set(hors)
    assert lisibles == []
    assert usage_staging is False


@pytest.mark.parametrize("droit", ECRITURES)
def test_modele_ro_n_ecrit_nulle_part(base_migree, droit):
    with psycopg.connect(base_migree) as connexion:
        cibles = tout_l_inventaire(connexion)
        accordes = [c for c in cibles if table(connexion, "modele_ro", c, droit)]
        if droit in ECRITURES_COLONNES:
            accordes += [
                c for c in cibles if une_colonne(connexion, "modele_ro", c, droit)
            ]

    assert accordes == [], f"{droit} accordé sur {accordes}"


def test_modele_ro_n_a_aucun_droit_sur_les_sequences(base_migree):
    with psycopg.connect(base_migree) as connexion:
        sequences = [
            ligne[0]
            for ligne in connexion.execute(
                "SELECT n.nspname || '.' || c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname IN ('manga', 'bench', 'staging') AND c.relkind = 'S'"
            ).fetchall()
        ]
        accordees = [
            (sequence, droit)
            for sequence in sequences
            for droit in ("USAGE", "UPDATE", "SELECT")
            if connexion.execute(
                "SELECT has_sequence_privilege('modele_ro', %s, %s)", (sequence, droit)
            ).fetchone()[0]
        ]

    assert sequences, "aucune séquence : le test ne prouverait rien"
    assert accordees == []


@pytest.mark.parametrize("schema", ("bench", "manga"))
def test_aucun_droit_par_defaut_un_objet_futur_reste_illisible(base_migree, schema):
    with psycopg.connect(base_migree, autocommit=True) as connexion:
        defauts = connexion.execute(
            "SELECT count(*) FROM pg_default_acl, aclexplode(defaclacl) a"
            " JOIN pg_roles r ON r.oid = a.grantee WHERE r.rolname = 'modele_ro'"
        ).fetchone()[0]
        connexion.execute(f"CREATE TABLE {schema}.objet_futur_023 (id int)")
        lisible = table(connexion, "modele_ro", f"{schema}.objet_futur_023", "SELECT")

    assert defauts == 0
    assert lisible is False


def test_aucune_appartenance_croisee(base_migree):
    paires = [
        (a, b)
        for a in ("modele_ro", "manga_ro", "verif_ro")
        for b in ("modele_ro", "manga_ro", "verif_ro")
        if a != b
    ]
    with psycopg.connect(base_migree) as connexion:
        membres = [
            (a, b)
            for a, b in paires
            if connexion.execute(
                "SELECT pg_has_role(%s, %s, 'MEMBER')", (a, b)
            ).fetchone()[0]
        ]

    assert membres == []


# --------------------------------------------------------------------------- #
#  Ce que 023 ne change pas, et son idempotence
# --------------------------------------------------------------------------- #
DROITS_DU_ROLE = """
SELECT 'relation', n.nspname || '.' || c.relname, a.privilege_type
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace,
       aclexplode(c.relacl) a
 WHERE a.grantee = %(role)s::regrole
UNION ALL
SELECT 'colonne', n.nspname || '.' || c.relname || '.' || t.attname, a.privilege_type
  FROM pg_attribute t JOIN pg_class c ON c.oid = t.attrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(t.attacl) a
 WHERE a.grantee = %(role)s::regrole
UNION ALL
SELECT 'schema', s.nspname, a.privilege_type
  FROM pg_namespace s, aclexplode(s.nspacl) a WHERE a.grantee = %(role)s::regrole
UNION ALL
SELECT 'base', d.datname, a.privilege_type
  FROM pg_database d, aclexplode(d.datacl) a
 WHERE d.datname = current_database() AND a.grantee = %(role)s::regrole
UNION ALL
SELECT 'defaut', d.defaclobjtype::text, a.privilege_type
  FROM pg_default_acl d, aclexplode(d.defaclacl) a WHERE a.grantee = %(role)s::regrole
ORDER BY 1, 2, 3
"""


@pytest.mark.parametrize("role", ("verif_ro", "manga_ro"))
def test_023_ne_change_pas_les_autres_roles_de_lecture(base, role):
    harnais.migrer(base, "022")
    with psycopg.connect(base) as connexion:
        avant = connexion.execute(DROITS_DU_ROLE, {"role": role}).fetchall()
    harnais.migrer(base)
    with psycopg.connect(base) as connexion:
        (version,) = connexion.execute(
            "SELECT max(version) FROM public.schema_migrations"
        ).fetchone()
        apres = connexion.execute(DROITS_DU_ROLE, {"role": role}).fetchall()

    assert version >= "023"
    assert avant, f"{role} n'a aucun droit avant 023 : le test ne prouverait rien"
    assert apres == avant


def test_023_est_idempotente(base_migree):
    """Rejeu direct du fichier : les droits de modele_ro ne changent pas."""
    with psycopg.connect(base_migree, autocommit=True) as connexion:
        avant = connexion.execute(DROITS_DU_ROLE, {"role": "modele_ro"}).fetchall()
        connexion.execute(FICHIER.read_text(encoding="utf-8"))
        apres = connexion.execute(DROITS_DU_ROLE, {"role": "modele_ro"}).fetchall()

    assert avant
    assert apres == avant


def test_la_migration_ne_cree_aucun_role_de_connexion():
    contenu = FICHIER.read_text(encoding="utf-8")
    code = "\n".join(
        ligne for ligne in contenu.splitlines() if not ligne.lstrip().startswith("--")
    )

    assert "modele_api" not in code
    assert "LOGIN" not in code.replace("NOLOGIN", "")
    assert "DEFAULT PRIVILEGES" not in code
    assert "ALL TABLES" not in code


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
        env["MODELE_API_PASSWORD"] = mot_de_passe
    return subprocess.run(
        ["sh", str(SCRIPT), dsn_admin],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def modele_api(base_migree) -> str:
    """Le DSN de `modele_api` sur la base du test, créé par le script."""
    mot_de_passe = secrets.token_urlsafe(24)
    resultat = lancer_script(base_migree, mot_de_passe)
    assert resultat.returncode == 0, resultat.stderr
    assert mot_de_passe not in resultat.stdout + resultat.stderr
    return conninfo.make_conninfo(base_migree, user="modele_api", password=mot_de_passe)


def sql_du_rattachement() -> str:
    """`SQL_RATTACHEMENT` du module 05, lu dans son fichier sans l'importer."""
    arbre = ast.parse(ATTEIGNABILITE.read_text(encoding="utf-8"))
    for noeud in arbre.body:
        if isinstance(noeud, ast.Assign) and any(
            getattr(cible, "id", None) == "SQL_RATTACHEMENT" for cible in noeud.targets
        ):
            return ast.literal_eval(noeud.value)
    raise AssertionError("SQL_RATTACHEMENT introuvable dans le module 05")


def test_le_script_refuse_sans_mot_de_passe(base_migree):
    resultat = lancer_script(base_migree, None)

    assert resultat.returncode != 0
    assert "MODELE_API_PASSWORD n'est pas définie" in resultat.stderr


def test_le_role_de_connexion_est_borne_et_n_herite_que_de_modele_ro(
    base_migree, modele_api
):
    with psycopg.connect(base_migree) as connexion:
        ligne = connexion.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls,"
            " rolconnlimit FROM pg_roles WHERE rolname = 'modele_api'"
        ).fetchone()
        groupes = [
            g
            for g in ("modele_ro", "manga_ro", "verif_ro")
            if connexion.execute(
                "SELECT pg_has_role('modele_api', %s, 'MEMBER')", (g,)
            ).fetchone()[0]
        ]

    assert ligne == (True, False, False, False, False, 10)
    assert groupes == ["modele_ro"]


def test_le_role_de_connexion_lit_ce_que_l_api_lit(modele_api):
    """Les lectures de l'API, sous son rôle : aucune n'est refusée."""
    with psycopg.connect(modele_api) as connexion:
        assert (
            connexion.execute("SHOW default_transaction_read_only").fetchone()[0]
            == "on"
        )
        for requete in (
            "SELECT * FROM bench.v_encodage_en_service",
            "SELECT encodage_id, termine_le, nb_fragments, dimension"
            " FROM bench.encodages",
            "SELECT corpus_id, clos_le, nb_documents, nb_fragments FROM bench.corpus",
            "SELECT chunk_id, embedding FROM bench.vecteurs_embeddinggemma"
            " WHERE encodage_id = 1 ORDER BY chunk_id",
            "SELECT chunk_id, doc_key, chunk_index, chunk_text"
            " FROM bench.corpus_chunks",
            "SELECT doc_key, source, series_id, kitsu_id, title FROM bench.corpus_docs",
            "SELECT series_id, series_title FROM manga.ms_series_enriched",
        ):
            connexion.execute(requete).fetchall()
        connexion.execute(sql_du_rattachement(), {"corpus": "v1"}).fetchall()


@pytest.mark.parametrize("ordre", ("INSERT", "UPDATE", "DELETE"))
def test_refus_1_a_3_aucune_ecriture_sur_les_tables_accordees(
    base_migree, modele_api, ordre
):
    """Session forcée en écriture : ce sont les droits qui refusent, table par table."""
    with psycopg.connect(base_migree) as admin:
        premiere = {t: colonne_modifiable(admin, t) for t in TABLES_ACCORDEES}
    refusees = []
    with psycopg.connect(modele_api) as connexion:
        for t in TABLES_ACCORDEES:
            texte = {
                "INSERT": sql.SQL("INSERT INTO {} DEFAULT VALUES").format(ident(t)),
                "UPDATE": sql.SQL("UPDATE {} SET {c} = {c}").format(
                    ident(t), c=sql.Identifier(premiere[t])
                ),
                "DELETE": sql.SQL("DELETE FROM {}").format(ident(t)),
            }[ordre]
            connexion.execute("SET TRANSACTION READ WRITE")
            try:
                connexion.execute(texte)
            except errors.InsufficientPrivilege:
                refusees.append(t)
            connexion.rollback()
        connexion.execute("SET TRANSACTION READ WRITE")
        with pytest.raises(errors.InsufficientPrivilege):
            connexion.execute("TRUNCATE bench.corpus_chunks")
        connexion.rollback()

    assert refusees == list(TABLES_ACCORDEES)


@pytest.mark.parametrize(
    "relation", ("bench.promotions", "bench.eval_mesures", "bench.vecteurs_bge_m3")
)
def test_refus_4_a_6_aucune_lecture_hors_perimetre(modele_api, relation):
    with psycopg.connect(modele_api) as connexion:
        with pytest.raises(errors.InsufficientPrivilege):
            connexion.execute(sql.SQL("SELECT * FROM {}").format(ident(relation)))


def test_une_autre_colonne_de_manga_est_refusee(modele_api):
    with psycopg.connect(modele_api) as connexion:
        with pytest.raises(errors.InsufficientPrivilege):
            connexion.execute("SELECT * FROM manga.ms_series_enriched")


def test_l_elevation_declaree_la_vue_se_lit_sans_droit_sur_promotions(
    base_migree, modele_api
):
    with psycopg.connect(base_migree) as admin:
        admin.execute(
            "INSERT INTO bench.encodages (modele, revision, dimension,"
            " precision_calcul, prefixe_document, prefixe_requete, outil,"
            " outil_version, image, image_digest, taille_lot, nb_fragments,"
            " termine_le) VALUES ('google/embeddinggemma-300m', %s, 768, 'float32',"
            " 'd', 'q', 'text-embeddings-inference', '1.9.4', 'img', %s, 16, 0,"
            " now())",
            ("a" * 40, "sha256:" + "b" * 64),
        )
        admin.execute(
            "INSERT INTO bench.promotions (decision, encodage_id, motif, decide_par)"
            " SELECT 'promu', max(encodage_id), 'essai', 'essai' FROM bench.encodages"
        )
    with psycopg.connect(modele_api) as connexion:
        (modele,) = connexion.execute(
            "SELECT modele FROM bench.v_encodage_en_service"
        ).fetchone()
        assert modele == "google/embeddinggemma-300m"
        with pytest.raises(errors.InsufficientPrivilege):
            connexion.execute("SELECT count(*) FROM bench.promotions")
