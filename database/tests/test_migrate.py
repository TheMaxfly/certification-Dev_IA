from __future__ import annotations

from argparse import Namespace

import psycopg
import pytest
from conftest import migrate

UP = Namespace(commande="up", target=None)
STATUS = Namespace(commande="status")

# --------------------------------------------------------------------------- #
#  Inventaire dérivé + sentinelles (dette 22.2)
# --------------------------------------------------------------------------- #
# Ces tests codaient en dur la liste des migrations et le nombre « en attente ».
# Chaque migration ajoutée forçait cinq retouches manuelles (subi en 003, 006,
# 007, 008) — friction croissante, et risque qu'une mise à jour mécanique masque
# un jour une vraie régression. L'inventaire se dérive donc du répertoire.
#
# MAIS un inventaire 100 % dérivé ne peut plus rien attraper : si le glob ne
# renvoie rien, toutes les assertions dérivées s'ajustent en silence. D'où deux
# SENTINELLES écrites en dur — un témoin indépendant du code qu'on teste.
SENTINELLE_PREMIERE = "000"
SENTINELLE_001_CREE = "work_identity"
SENTINELLE_PLANCHER = 9  # 000→008 acquises ; ce plancher ne peut que monter.


def versions_du_depot() -> list[str]:
    """L'inventaire réel, dérivé du répertoire de migrations courant.

    Lit `migrate.MIGRATIONS_DIR` à l'appel : la fixture `migrations_jetables`
    le monkeypatche, donc le helper suit le dossier actif sans le savoir.
    """
    return [m.version for m in migrate.decouvrir(migrate.MIGRATIONS_DIR)]


def test_inventaire_derive_reste_sous_temoin():
    """Le garde-fou de la dérivation : sans lui, un glob vide rendrait tous les
    tests d'inventaire vrais et silencieux."""
    versions = versions_du_depot()

    assert versions, "le glob des migrations ne doit jamais être vide"
    assert versions[0] == SENTINELLE_PREMIERE, "000 doit rester la première"
    assert versions == sorted(versions), "l'ordre lexicographique est l'ordre réel"
    assert len(versions) >= SENTINELLE_PLANCHER, (
        f"{len(versions)} migrations : le plancher {SENTINELLE_PLANCHER} est "
        "acquis, une disparition est une régression"
    )
    assert len(set(versions)) == len(versions), "deux migrations au même numéro"

    socle = next(f for f in migrate.MIGRATIONS_DIR.glob("001_*.sql")).read_text(
        encoding="utf-8"
    )
    assert SENTINELLE_001_CREE in socle, "001 doit toujours créer work_identity"


def versions_appliquees(dsn: str) -> list[str]:
    with psycopg.connect(dsn) as connexion:
        lignes = connexion.execute(
            "SELECT version FROM public.schema_migrations ORDER BY version"
        ).fetchall()
    return [v for (v,) in lignes]


def ecrire_migration(dossier, nom: str, sql: str):
    (dossier / nom).write_text(sql, encoding="utf-8")


@pytest.fixture
def migrations_jetables(tmp_path, monkeypatch):
    """Dossier de migrations à nous : ne jamais modifier les vraies migrations."""
    dossier = tmp_path / "migrations"
    dossier.mkdir()
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", dossier)
    return dossier


# --------------------------------------------------------------------------- #
#  Le runner
# --------------------------------------------------------------------------- #


def test_up_sur_base_vide_applique_tout(base, capsys):
    """Le rejeu part de 000 : une base neuve reconstruit l'héritage, puis le
    versionné par-dessus."""
    assert migrate.commande_up(UP) == 0

    assert versions_appliquees(base) == versions_du_depot()
    assert "001 appliquée" in capsys.readouterr().out


def test_up_rejoue_est_un_noop(base, capsys):
    migrate.commande_up(UP)
    capsys.readouterr()

    assert migrate.commande_up(UP) == 0

    assert "Rien à appliquer" in capsys.readouterr().out
    assert versions_appliquees(base) == versions_du_depot()


def test_status_avant_et_apres(base, capsys):
    total = len(versions_du_depot())

    assert migrate.commande_status(STATUS) == 0
    avant = capsys.readouterr().out
    assert f"0 appliquée(s), {total} en attente" in avant
    assert "en attente" in avant

    migrate.commande_up(UP)
    capsys.readouterr()

    assert migrate.commande_status(STATUS) == 0
    assert f"{total} appliquée(s), 0 en attente" in capsys.readouterr().out


def test_target_s_arrete_a_la_version_demandee(base):
    assert migrate.commande_up(Namespace(commande="up", target="001")) == 0

    assert versions_appliquees(base) == ["000", "001"]

    assert migrate.commande_up(UP) == 0
    assert versions_appliquees(base) == versions_du_depot()


def test_target_inconnue_refusee(base):
    with pytest.raises(migrate.ErreurMigration, match="Cible inconnue"):
        migrate.commande_up(Namespace(commande="up", target="999"))


def test_fichier_modifie_apres_application_erreur_checksum(
    base, migrations_jetables, capsys
):
    ecrire_migration(
        migrations_jetables, "001_socle.sql", "CREATE TABLE a (i INTEGER);"
    )
    migrate.commande_up(UP)
    capsys.readouterr()

    # Le fichier change après coup : c'est exactement ce qu'on veut interdire.
    ecrire_migration(migrations_jetables, "001_socle.sql", "CREATE TABLE a (i BIGINT);")

    with pytest.raises(migrate.ErreurMigration, match="Checksum différent"):
        migrate.commande_up(UP)

    # status doit le signaler aussi, sans faire semblant que tout va bien.
    assert migrate.commande_status(STATUS) == 1
    assert "MODIFIÉ" in capsys.readouterr().out


def test_ordre_lexicographique_respecte(base, migrations_jetables, capsys):
    """010 doit passer après 002, et 002 après 001 : le zéro-padding fait que
    l'ordre lexicographique est l'ordre numérique."""
    ecrire_migration(migrations_jetables, "010_c.sql", "CREATE TABLE c (i INT);")
    ecrire_migration(migrations_jetables, "001_a.sql", "CREATE TABLE a (i INT);")
    ecrire_migration(migrations_jetables, "002_b.sql", "CREATE TABLE b (i INT);")

    migrate.commande_up(UP)

    sortie = capsys.readouterr().out
    assert sortie.index("001_a") < sortie.index("002_b") < sortie.index("010_c")
    assert versions_appliquees(base) == ["001", "002", "010"]


def test_echec_annule_le_fichier_entier(base, migrations_jetables):
    """Une migration qui échoue en cours de route ne doit rien laisser derrière
    elle : c'est tout l'intérêt d'une transaction par fichier."""
    ecrire_migration(
        migrations_jetables,
        "001_cassee.sql",
        "CREATE TABLE ok_avant (i INTEGER); CREATE TABLE ceci n est pas du sql;",
    )

    with pytest.raises(migrate.ErreurMigration, match="Échec de 001_cassee.sql"):
        migrate.commande_up(UP)

    with psycopg.connect(base) as connexion:
        existe = connexion.execute("SELECT to_regclass('public.ok_avant')").fetchone()
        suivi = connexion.execute(
            "SELECT count(*) FROM public.schema_migrations"
        ).fetchone()
    assert existe[0] is None, "la table créée avant l'erreur doit être annulée"
    assert suivi[0] == 0, "une migration échouée ne doit pas être enregistrée"


def test_echec_ne_defait_pas_les_migrations_du_meme_run(base, migrations_jetables):
    """Chaque fichier a SA transaction : si 002 échoue, 001 — appliquée quelques
    lignes plus tôt dans le même run — doit rester en place. Sans autocommit,
    tout le run tenait dans une seule transaction et 001 était annulée avec 002.
    """
    ecrire_migration(migrations_jetables, "001_ok.sql", "CREATE TABLE ok (i INT);")
    ecrire_migration(migrations_jetables, "002_cassee.sql", "CREATE TABLE ??? ;")

    with pytest.raises(migrate.ErreurMigration, match="Échec de 002_cassee.sql"):
        migrate.commande_up(UP)

    assert versions_appliquees(base) == ["001"]
    with psycopg.connect(base) as connexion:
        table = connexion.execute("SELECT to_regclass('public.ok')").fetchone()
    assert table[0] is not None, "001 doit survivre à l'échec de 002"


def test_nom_de_fichier_invalide_refuse(base, migrations_jetables):
    ecrire_migration(migrations_jetables, "socle.sql", "SELECT 1;")

    with pytest.raises(migrate.ErreurMigration, match="Nom de migration invalide"):
        migrate.commande_up(UP)


def test_dsn_absent_message_clair(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(migrate.ErreurMigration, match="DATABASE_URL"):
        migrate.dsn()


# --------------------------------------------------------------------------- #
#  mark-applied — enregistrer sans exécuter
# --------------------------------------------------------------------------- #


def marquer(version: str) -> Namespace:
    return Namespace(commande="mark-applied", version=version)


def test_mark_applied_enregistre_sans_executer(base, migrations_jetables, capsys):
    """Le cœur de la commande : la ligne est écrite, le SQL ne tourne pas."""
    ecrire_migration(
        migrations_jetables, "000_heritage.sql", "CREATE TABLE public.heritage (i INT);"
    )

    assert migrate.commande_mark_applied(marquer("000")) == 0

    assert versions_appliquees(base) == ["000"]
    with psycopg.connect(base) as connexion:
        table = connexion.execute("SELECT to_regclass('public.heritage')").fetchone()
    assert table[0] is None, "mark-applied ne doit RIEN exécuter du fichier"
    assert "n'a PAS été exécuté" in capsys.readouterr().out


def test_mark_applied_enregistre_le_checksum_du_fichier(base, migrations_jetables):
    """Même discipline que `up` : le checksum enregistré est celui du fichier,
    sinon `status` signalerait une dérive dès le lendemain du marquage."""
    ecrire_migration(migrations_jetables, "000_heritage.sql", "CREATE TABLE h (i INT);")
    attendu = migrate.decouvrir(migrations_jetables)[0].checksum

    migrate.commande_mark_applied(marquer("000"))

    with psycopg.connect(base) as connexion:
        enregistre = connexion.execute(
            "SELECT checksum FROM public.schema_migrations WHERE version = '000'"
        ).fetchone()
    assert enregistre[0] == attendu


def test_mark_applied_refuse_une_version_inconnue(base, migrations_jetables):
    ecrire_migration(migrations_jetables, "001_socle.sql", "CREATE TABLE a (i INT);")
    # Matérialise schema_migrations : sans cela le refus, qui tombe avant toute
    # connexion, laisserait la table inexistante et l'assertion finale ne
    # prouverait rien.
    migrate.commande_status(STATUS)

    with pytest.raises(migrate.ErreurMigration, match="Version inconnue"):
        migrate.commande_mark_applied(marquer("000"))

    assert versions_appliquees(base) == [], "un refus ne doit rien enregistrer"


def test_mark_applied_refuse_une_migration_deja_enregistree(base, migrations_jetables):
    ecrire_migration(migrations_jetables, "000_heritage.sql", "CREATE TABLE h (i INT);")
    migrate.commande_mark_applied(marquer("000"))

    with pytest.raises(migrate.ErreurMigration, match="déjà enregistrée"):
        migrate.commande_mark_applied(marquer("000"))

    assert versions_appliquees(base) == ["000"], "pas de doublon en base"


def test_mark_applied_refuse_une_migration_deja_appliquee_par_up(
    base, migrations_jetables
):
    """Marquer ce que `up` a réellement joué n'aurait aucun sens."""
    ecrire_migration(migrations_jetables, "000_heritage.sql", "CREATE TABLE h (i INT);")
    migrate.commande_up(UP)

    with pytest.raises(migrate.ErreurMigration, match="déjà enregistrée"):
        migrate.commande_mark_applied(marquer("000"))


def test_status_voit_la_migration_marquee_comme_appliquee(
    base, migrations_jetables, capsys
):
    ecrire_migration(migrations_jetables, "000_heritage.sql", "CREATE TABLE h (i INT);")
    migrate.commande_mark_applied(marquer("000"))
    capsys.readouterr()

    assert migrate.commande_status(STATUS) == 0

    sortie = capsys.readouterr().out
    assert "appliquée" in sortie
    assert "1 appliquée(s), 0 en attente" in sortie


def test_up_ne_rejoue_pas_une_migration_marquee(base, migrations_jetables, capsys):
    """Le point qui rend le marquage utile sur la base historique : `up` doit
    passer 000 et n'appliquer que la suite."""
    ecrire_migration(
        migrations_jetables, "000_heritage.sql", "CREATE TABLE public.heritage (i INT);"
    )
    ecrire_migration(
        migrations_jetables, "001_socle.sql", "CREATE TABLE public.a (i INT);"
    )
    migrate.commande_mark_applied(marquer("000"))
    capsys.readouterr()

    assert migrate.commande_up(UP) == 0

    sortie = capsys.readouterr().out
    assert "000_heritage" not in sortie, "000 ne doit pas être rejouée"
    assert "001 appliquée" in sortie
    assert versions_appliquees(base) == ["000", "001"]
    with psycopg.connect(base) as connexion:
        heritage = connexion.execute("SELECT to_regclass('public.heritage')").fetchone()
        socle = connexion.execute("SELECT to_regclass('public.a')").fetchone()
    assert heritage[0] is None, "000 marquée : son SQL ne doit jamais tourner"
    assert socle[0] is not None, "001 non marquée : son SQL doit tourner"


# --------------------------------------------------------------------------- #
#  Le SQL livré par 001
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
#  L'héritage livré par 000
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "table",
    [
        "manga.ms_series_enriched",
        "manga.ms_volumes_enriched",
        "manga.ms_reviews_all",
        "manga.ms_kitsu_map",
        "manga.rag_reviews_docs",
        "manga.kitsu_series_core",
        "bench.corpus_chunks",
        "bench.embedding_runs",
    ],
)
def test_000_reconstruit_les_tables_de_l_heritage(base_migree, table):
    """Sans 000, ces tables n'existaient QUE sur apimanga : le dépôt ne savait
    pas les reconstruire."""
    with psycopg.connect(base_migree) as connexion:
        existe = connexion.execute("SELECT to_regclass(%s)", (table,)).fetchone()
    assert existe[0] is not None, f"{table} manque après le rejeu de 000"


def test_000_reconstruit_les_vues_rag(base_migree):
    with psycopg.connect(base_migree) as connexion:
        vues = connexion.execute(
            "SELECT viewname FROM pg_views WHERE schemaname = 'manga' ORDER BY viewname"
        ).fetchall()
    noms = [v for (v,) in vues]
    assert "rag_docs_all" in noms
    assert "rag_export_docs" in noms
    assert "v_match_current" in noms, "la vue de 001 doit coexister avec celles de 000"


def test_000_ne_recree_pas_les_objets_de_001(base_migree):
    """La frontière héritage/versionné : 000 laisse à 001 ce qui lui revient."""
    contenu = (migrate.MIGRATIONS_DIR / "000_baseline.sql").read_text(encoding="utf-8")
    corps = "\n".join(
        ligne for ligne in contenu.splitlines() if not ligne.lstrip().startswith("--")
    )
    for objet in ("work_identity", "volume_identity", "match_decision", "staging."):
        assert objet not in corps, f"{objet} appartient à 001/002, pas à la baseline"


def test_work_identity_accepte_une_disponibilite_connue(base_migree):
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.work_identity (series_id, disponibilite) "
            "VALUES (1, 'vf_disponible')"
        )
        connexion.commit()
        total = connexion.execute("SELECT count(*) FROM manga.work_identity").fetchone()
    assert total[0] == 1


def test_work_identity_refuse_une_disponibilite_inconnue(base_migree):
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.work_identity (series_id, disponibilite) "
                "VALUES (2, 'peut_etre')"
            )


def test_work_identity_series_id_unique_seulement_si_renseigne(base_migree):
    """L'index partiel doit laisser passer plusieurs NULL — une œuvre sans fiche
    Manga Sanctuary est un cas normal — mais refuser deux fois le même id."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute("INSERT INTO manga.work_identity (series_id) VALUES (NULL)")
        connexion.execute("INSERT INTO manga.work_identity (series_id) VALUES (NULL)")
        connexion.execute("INSERT INTO manga.work_identity (series_id) VALUES (7)")
        connexion.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            connexion.execute("INSERT INTO manga.work_identity (series_id) VALUES (7)")


def test_volume_identity_refuse_un_isbn_mal_forme(base_migree):
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.volume_identity (volume_url, isbn13) "
                "VALUES ('https://x/vol-1', '978235592948X')"
            )


def test_volume_identity_accepte_un_isbn_a_zero_de_tete(base_migree):
    """CHAR(13) et non un entier : le zéro de tête est significatif."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.volume_identity (volume_url, isbn13, isbn13_valide) "
            "VALUES ('https://x/vol-2', '0123456789012', true)"
        )
        connexion.commit()
        valeur = connexion.execute(
            "SELECT isbn13 FROM manga.volume_identity WHERE volume_url = %s",
            ("https://x/vol-2",),
        ).fetchone()
    assert valeur[0] == "0123456789012"


def test_volume_identity_isbn13_non_unique(base_migree):
    """Rééditions et coffrets partagent légitimement un EAN."""
    with psycopg.connect(base_migree) as connexion:
        for numero in (1, 2):
            connexion.execute(
                "INSERT INTO manga.volume_identity (volume_url, isbn13) "
                "VALUES (%s, '9782355929489')",
                (f"https://x/edition-{numero}",),
            )
        connexion.commit()
        total = connexion.execute(
            "SELECT count(*) FROM manga.volume_identity WHERE isbn13 = '9782355929489'"
        ).fetchone()
    assert total[0] == 2


@pytest.mark.parametrize("statut", ["auto", "validated", "rejected", "needs_review"])
def test_match_decision_accepte_les_statuts(base_migree, statut):
    """Les quatre statuts du CHECK de 001 (le refus d'un statut inconnu suit).

    Seul test de `database/tests` qui écrit `rejected` et `needs_review` : les
    tests de la contrainte des méthodes, réunis dans les `test_018_*`, n'écrivent
    plus que `auto`.
    """
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.match_decision (series_id, method, status) "
            "VALUES (1, 'exact', %s)",
            (statut,),
        )
        connexion.commit()


def test_match_decision_refuse_un_statut_inconnu(base_migree):
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.match_decision (series_id, method, status) "
                "VALUES (1, 'exact', 'valide_peut_etre')"
            )


def test_v_match_current_rend_la_derniere_decision(base_migree):
    """La vue est LA lecture de référence : elle doit rendre la décision la plus
    récente, y compris quand une revue humaine contredit le pipeline."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.match_decision "
            "(series_id, wikidata_qid, method, status, decided_at) VALUES "
            "(42, 'Q1', 'trgm', 'auto', now() - interval '2 days'), "
            "(42, 'Q2', 'manual', 'validated', now() - interval '1 day'), "
            "(99, 'Q9', 'exact', 'auto', now())"
        )
        connexion.commit()
        lignes = connexion.execute(
            "SELECT series_id, wikidata_qid, status FROM manga.v_match_current "
            "ORDER BY series_id"
        ).fetchall()

    assert lignes == [(42, "Q2", "validated"), (99, "Q9", "auto")]


def test_v_match_current_departage_les_ex_aequo(base_migree):
    """Deux décisions insérées dans la même transaction partagent le now()
    transactionnel : decision_id doit départager, sinon la vue est instable."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.match_decision (series_id, wikidata_qid, method, "
            "status) VALUES (7, 'Q_ancienne', 'trgm', 'auto'), "
            "(7, 'Q_recente', 'manual', 'validated')"
        )
        connexion.commit()
        ligne = connexion.execute(
            "SELECT wikidata_qid FROM manga.v_match_current WHERE series_id = 7"
        ).fetchone()

    assert ligne[0] == "Q_recente"


# --------------------------------------------------------------------------- #
#  Le SQL livré par 002
# --------------------------------------------------------------------------- #


def test_staging_tables_creees(base_migree):
    attendues = {
        "wd_pivot",
        "wd_entities",
        "wd_formes",
        "wd_auteurs",
        "kitsu_formes",
        "mi_sorties",
        "mi_series",
        # 004 — atterrissage du snapshot Manga Sanctuary.
        "ms_volumes",
        "ms_reviews",
        # 006 — l'atterrissage des mappings Kitsu, qui manquait.
        "kitsu_mappings",
        # 009 — le staff Kitsu, confirmateur d'auteur de l'étage 2.
        "kitsu_staff",
        # 015 — les personnages, socle multi-sources. Quatre grains distincts :
        # le personnage, ses formes nominales, ses descriptions (une par
        # source), et ses liens aux œuvres — le rôle appartenant au lien.
        "characters",
        "character_forms",
        "character_descriptions",
        "character_work",
    }
    with psycopg.connect(base_migree) as connexion:
        lignes = connexion.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'staging'"
        ).fetchall()
    assert {t for (t,) in lignes} == attendues


def test_staging_est_en_text_partout(base_migree):
    """Le typage se fait à la promotion : une colonne typée en staging ferait
    échouer un chargement sur une valeur aberrante du fichier source."""
    with psycopg.connect(base_migree) as connexion:
        lignes = connexion.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema = 'staging' "
            "AND column_name NOT IN ('loaded_at')"
        ).fetchall()

    non_text = [(t, c, d) for t, c, d in lignes if d != "text"]
    assert non_text == []


def test_staging_porte_les_colonnes_techniques(base_migree):
    """CHAQUE table de staging porte source_file et loaded_at — la règle vaut
    pour toutes, pas pour un nombre.

    L'inventaire est dérivé (dette 22.2) : le comptage en dur « 7 tables de 002
    + 2 de 004 + 1 de 006 » se démentait à chaque migration. Ce qui compte n'est
    pas qu'il y ait dix tables, mais qu'aucune n'échappe à la convention.
    """
    with psycopg.connect(base_migree) as connexion:
        toutes = {
            t
            for (t,) in connexion.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'staging'"
            ).fetchall()
        }
        avec_source = {
            t
            for (t,) in connexion.execute(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'staging' AND column_name = 'source_file'"
            ).fetchall()
        }
        avec_loaded = {
            t
            for (t,) in connexion.execute(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'staging' AND column_name = 'loaded_at' "
                "AND column_default LIKE 'now()%'"
            ).fetchall()
        }

    assert toutes, "le schéma staging ne doit jamais être vide"
    assert toutes - avec_source == set(), (
        f"sans source_file : {sorted(toutes - avec_source)}"
    )
    assert toutes - avec_loaded == set(), (
        f"sans loaded_at DEFAULT now() : {sorted(toutes - avec_loaded)}"
    )


def test_mi_sorties_porte_l_ean_et_mi_series_non(base_migree):
    """Mesuré sur le parquet : Ean est à 96,5 % en population A et 0 % en B."""
    with psycopg.connect(base_migree) as connexion:
        lignes = connexion.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'staging' AND column_name = 'ean'"
        ).fetchall()
    assert [t for (t,) in lignes] == ["mi_sorties"]


def test_mi_conserve_le_subtype_kitsu(base_migree):
    """Le filtre subtype s'applique à la promotion, pas au staging : filtrer ici
    ferait perdre la mesure de ce qui a été écarté."""
    with psycopg.connect(base_migree) as connexion:
        ligne = connexion.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'staging' AND table_name = 'kitsu_formes' "
            "AND column_name = 'subtype'"
        ).fetchone()
    assert ligne[0] == 1


# --------------------------------------------------------------------------- #
#  Le SQL livré par 003
# --------------------------------------------------------------------------- #


def creer_serie(connexion, series_id: int) -> None:
    """ms_series_enriched a 72 colonnes, dont une seule est NOT NULL."""
    connexion.execute(
        "INSERT INTO manga.ms_series_enriched (series_id) VALUES (%s)", (series_id,)
    )


@pytest.mark.parametrize(
    ("table", "colonne", "type_attendu"),
    [
        ("ms_volumes_enriched", "volume_ean", "text"),
        ("ms_series_enriched", "work_uid", "bigint"),
        ("ms_reviews_all", "review_grain", "text"),
    ],
)
def test_003_ajoute_les_colonnes_attendues(base_migree, table, colonne, type_attendu):
    with psycopg.connect(base_migree) as connexion:
        trouve = connexion.execute(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "WHERE a.attrelid = %s::regclass AND a.attname = %s "
            "AND a.attnum > 0 AND NOT a.attisdropped",
            (f"manga.{table}", colonne),
        ).fetchone()
    assert trouve is not None, f"{table}.{colonne} manque après 003"
    assert trouve[0] == type_attendu


def test_003_ne_recree_pas_ce_qui_existait_deja(base_migree):
    """Le schéma réel portait déjà volume_number, review_type, series_genres et
    series_tags : 003 ne doit pas les avoir touchés."""
    contenu = (migrate.MIGRATIONS_DIR / "003_evolution_ms.sql").read_text(
        encoding="utf-8"
    )
    corps = "\n".join(
        ligne for ligne in contenu.splitlines() if not ligne.lstrip().startswith("--")
    )
    for deja_la in ("volume_number", "review_type", "series_genres", "series_tags"):
        assert f"ADD COLUMN {deja_la}" not in corps


def test_review_grain_vaut_volume_par_defaut(base_migree):
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.ms_reviews_all (review_body) VALUES ('critique')"
        )
        connexion.commit()
        grain = connexion.execute(
            "SELECT review_grain FROM manga.ms_reviews_all"
        ).fetchone()
    assert grain[0] == "volume"


@pytest.mark.parametrize("grain", ["volume", "serie"])
def test_review_grain_accepte_les_deux_grains(base_migree, grain):
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.ms_reviews_all (review_body, review_grain) "
            "VALUES ('critique', %s)",
            (grain,),
        )
        connexion.commit()


def test_review_grain_refuse_un_grain_hors_liste(base_migree):
    """MUTATION : sans le CHECK, cet INSERT passe."""
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.ms_reviews_all (review_body, review_grain) "
                "VALUES ('critique', 'chapitre')"
            )


def test_work_uid_refuse_un_moyeu_orphelin(base_migree):
    """MUTATION : sans la FK, une série peut pointer vers une œuvre inexistante."""
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connexion.execute(
                "INSERT INTO manga.ms_series_enriched (series_id, work_uid) "
                "VALUES (1, 999999)"
            )


def test_work_uid_accepte_null_et_un_moyeu_reel(base_migree):
    """NULL est l'état normal tant que la cascade n'a pas tranché."""
    with psycopg.connect(base_migree) as connexion:
        creer_serie(connexion, 1)
        work_uid = connexion.execute(
            "INSERT INTO manga.work_identity (series_id) VALUES (2) RETURNING work_uid"
        ).fetchone()[0]
        connexion.execute(
            "INSERT INTO manga.ms_series_enriched (series_id, work_uid) VALUES (2, %s)",
            (work_uid,),
        )
        connexion.commit()
        sans_moyeu = connexion.execute(
            "SELECT count(*) FROM manga.ms_series_enriched WHERE work_uid IS NULL"
        ).fetchone()
    assert sans_moyeu[0] == 1


def test_ms_formes_refuse_un_doublon_de_forme(base_migree):
    """MUTATION : sans l'UNIQUE, la même forme se charge deux fois."""
    with psycopg.connect(base_migree) as connexion:
        creer_serie(connexion, 1)
        connexion.execute(
            "INSERT INTO manga.ms_formes (series_id, forme, forme_norm, forme_type) "
            "VALUES (1, 'Bakegyamon', 'bakegyamon', 'title')"
        )
        connexion.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            connexion.execute(
                "INSERT INTO manga.ms_formes "
                "(series_id, forme, forme_norm, forme_type) "
                "VALUES (1, 'BakéGyamon', 'bakegyamon', 'alias')"
            )


def test_ms_formes_accepte_la_meme_forme_pour_deux_series(base_migree):
    """L'unicité porte sur le TRIPLET : deux séries homonymes sont un fait de la
    source — et le problème même que la cascade doit trancher."""
    with psycopg.connect(base_migree) as connexion:
        creer_serie(connexion, 1)
        creer_serie(connexion, 2)
        for series_id in (1, 2):
            connexion.execute(
                "INSERT INTO manga.ms_formes "
                "(series_id, forme, forme_norm, forme_type) "
                "VALUES (%s, 'Monster', 'monster', 'title')",
                (series_id,),
            )
        connexion.commit()
        total = connexion.execute(
            "SELECT count(*) FROM manga.ms_formes WHERE forme_norm = 'monster'"
        ).fetchone()
    assert total[0] == 2


def test_ms_formes_meme_forme_de_deux_sources_coexiste(base_migree):
    """La source fait partie de la clé : Wikidata pourra proposer la même forme
    que Manga Sanctuary sans écraser la sienne."""
    with psycopg.connect(base_migree) as connexion:
        creer_serie(connexion, 1)
        for source in ("ms", "wikidata"):
            connexion.execute(
                "INSERT INTO manga.ms_formes "
                "(series_id, forme, forme_norm, forme_type, source) "
                "VALUES (1, 'Monster', 'monster', 'title', %s)",
                (source,),
            )
        connexion.commit()
        total = connexion.execute("SELECT count(*) FROM manga.ms_formes").fetchone()
    assert total[0] == 2


def test_ms_formes_refuse_un_type_de_forme_inconnu(base_migree):
    with psycopg.connect(base_migree) as connexion:
        creer_serie(connexion, 1)
        connexion.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.ms_formes "
                "(series_id, forme, forme_norm, forme_type) "
                "VALUES (1, 'Monster', 'monster', 'sous-titre')"
            )


def test_ms_formes_refuse_une_serie_inexistante(base_migree):
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connexion.execute(
                "INSERT INTO manga.ms_formes "
                "(series_id, forme, forme_norm, forme_type) "
                "VALUES (404, 'Monster', 'monster', 'title')"
            )


def test_ms_formes_langue_reste_nullable(base_migree):
    """Les alias Manga Sanctuary n'ont pas de langue : la colonne doit
    l'accepter plutôt que de forcer une inférence."""
    with psycopg.connect(base_migree) as connexion:
        creer_serie(connexion, 1)
        connexion.execute(
            "INSERT INTO manga.ms_formes (series_id, forme, forme_norm, forme_type) "
            "VALUES (1, '妖逆門', 'yougyakumon', 'alias')"
        )
        connexion.commit()
        ligne = connexion.execute(
            "SELECT langue, source FROM manga.ms_formes"
        ).fetchone()
    assert ligne[0] is None
    assert ligne[1] == "ms", "source vaut 'ms' par défaut"


def test_003_installe_pg_trgm_et_ses_index(base_migree):
    with psycopg.connect(base_migree) as connexion:
        extension = connexion.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm'"
        ).fetchone()
        index = connexion.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'manga' AND tablename = 'ms_formes'"
        ).fetchall()
    assert extension is not None, "pg_trgm doit être installée par 003"
    definitions = dict(index)
    assert "gin_trgm_ops" in definitions["ms_formes_forme_norm_trgm_idx"]
    assert "btree" in definitions["ms_formes_forme_norm_idx"]


def test_ms_formes_index_trigram_est_utilisable(base_migree):
    """L'index n'a de sens que si l'opérateur % le trouve : on l'exerce."""
    with psycopg.connect(base_migree) as connexion:
        creer_serie(connexion, 1)
        connexion.execute(
            "INSERT INTO manga.ms_formes (series_id, forme, forme_norm, forme_type) "
            "VALUES (1, 'Bakegyamon', 'bakegyamon', 'title')"
        )
        connexion.commit()
        proche = connexion.execute(
            "SELECT forme_norm FROM manga.ms_formes WHERE forme_norm %% %s",
            ("bakegyamonn",),
        ).fetchall()
    assert len(proche) == 1, "la recherche floue doit retrouver la forme voisine"


# --------------------------------------------------------------------------- #
#  Le SQL livré par 004 et 005
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("table", "attendu"),
    [("ms_volumes", 42), ("ms_reviews", 15)],
)
def test_004_staging_porte_toutes_les_cles_du_fichier(base_migree, table, attendu):
    """39 clés + 1 dérivée + 2 techniques pour les volumes ; 12 + 1 + 2 pour les
    critiques. Aucune clé de la source n'est écartée au chargement."""
    with psycopg.connect(base_migree) as connexion:
        total = connexion.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'staging' AND table_name = %s",
            (table,),
        ).fetchone()
    assert total[0] == attendu


def test_004_staging_ms_porte_les_colonnes_iso_derivees(base_migree):
    """Les dates FR sont parsées en Python : le staging reçoit de l'ISO, que la
    promotion caste sans jamais dépendre du lc_time du serveur."""
    with psycopg.connect(base_migree) as connexion:
        lignes = connexion.execute(
            "SELECT table_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'staging' AND column_name IN "
            "('volume_publication_date_iso', 'review_date_iso') "
            "ORDER BY table_name"
        ).fetchall()
    assert lignes == [("ms_reviews", "text"), ("ms_volumes", "text")]


def test_005_review_url_devient_unique(base_migree):
    """MUTATION : sans cet index, la promotion des critiques ne peut pas être un
    upsert et dupliquerait 11 052 lignes par cycle."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.ms_reviews_all (review_url) VALUES ('https://x/r1')"
        )
        connexion.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            connexion.execute(
                "INSERT INTO manga.ms_reviews_all (review_url) VALUES ('https://x/r1')"
            )


def test_005_index_partiel_laisse_passer_les_null(base_migree):
    with psycopg.connect(base_migree) as connexion:
        for _ in range(2):
            connexion.execute(
                "INSERT INTO manga.ms_reviews_all (review_body) VALUES ('sans url')"
            )
        connexion.commit()
        total = connexion.execute(
            "SELECT count(*) FROM manga.ms_reviews_all WHERE review_url IS NULL"
        ).fetchone()
    assert total[0] == 2


# --------------------------------------------------------------------------- #
#  Le SQL livré par 006
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "table",
    [
        "staging.kitsu_mappings",
        "manga.wd_pivot",
        "manga.wd_formes",
        "manga.wd_auteurs",
        "manga.kitsu_mappings",
        "manga.kitsu_formes",
    ],
)
def test_006_cree_les_referentiels(base_migree, table):
    with psycopg.connect(base_migree) as connexion:
        existe = connexion.execute("SELECT to_regclass(%s)", (table,)).fetchone()
    assert existe[0] is not None, f"{table} manque après 006"


def test_006_ne_double_pas_kitsu_series_core(base_migree):
    """L'héritage porte déjà les titres et le synopsis Kitsu : kitsu_formes ne
    contient QUE des formes de matching."""
    with psycopg.connect(base_migree) as connexion:
        colonnes = {
            c
            for (c,) in connexion.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'manga' AND table_name = 'kitsu_formes'"
            ).fetchall()
        }
    for absente in ("synopsis_clean", "rating_average_10", "categories_json"):
        assert absente not in colonnes, "kitsu_series_core reste la fiche Kitsu"


def test_kitsu_formes_refuse_un_subtype_hors_cible(base_migree):
    """MUTATION : sans ce CHECK, un light novel entrerait dans les cibles de
    matching — 15 224 d'entre eux attendent dans le catalogue."""
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.kitsu_formes "
                "(kitsu_id, forme, forme_norm, forme_type, subtype) "
                "VALUES (1, 'Un roman', 'un roman', 'canonical', 'novel')"
            )


def test_kitsu_formes_refuse_un_type_de_forme_inconnu(base_migree):
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.kitsu_formes "
                "(kitsu_id, forme, forme_norm, forme_type, subtype) "
                "VALUES (1, 'X', 'x', 'sous-titre', 'manga')"
            )


def test_kitsu_formes_dedoublonne_par_entree(base_migree):
    """MUTATION : sans l'UNIQUE, le titre canonique et son en_jp identique
    feraient deux formes pour une seule œuvre."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.kitsu_formes "
            "(kitsu_id, forme, forme_norm, forme_type, subtype) "
            "VALUES (1, 'Monster', 'monster', 'canonical', 'manga')"
        )
        connexion.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            connexion.execute(
                "INSERT INTO manga.kitsu_formes "
                "(kitsu_id, forme, forme_norm, forme_type, subtype) "
                "VALUES (1, 'MONSTER', 'monster', 'title', 'manga')"
            )


def test_kitsu_formes_meme_forme_pour_deux_entrees(base_migree):
    """Deux œuvres homonymes : le problème que la cascade doit trancher."""
    with psycopg.connect(base_migree) as connexion:
        for kitsu_id in (1, 2):
            connexion.execute(
                "INSERT INTO manga.kitsu_formes "
                "(kitsu_id, forme, forme_norm, forme_type, subtype) "
                "VALUES (%s, 'Monster', 'monster', 'canonical', 'manga')",
                (kitsu_id,),
            )
        connexion.commit()
        total = connexion.execute(
            "SELECT count(*) FROM manga.kitsu_formes WHERE forme_norm = 'monster'"
        ).fetchone()
    assert total[0] == 2


def test_kitsu_mappings_dedoublonne_le_triplet(base_migree):
    """MUTATION : sans l'UNIQUE, un rechargement dupliquerait les 74 866 ponts."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.kitsu_mappings (kitsu_id, external_site, external_id) "
            "VALUES (1, 'myanimelist/manga', '101')"
        )
        connexion.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            connexion.execute(
                "INSERT INTO manga.kitsu_mappings "
                "(kitsu_id, external_site, external_id) "
                "VALUES (1, 'myanimelist/manga', '101')"
            )


def test_kitsu_mappings_accepte_plusieurs_sites_par_entree(base_migree):
    """La clé est le triplet : une œuvre a légitimement un id MAL et un AniList."""
    with psycopg.connect(base_migree) as connexion:
        for site in ("myanimelist/manga", "anilist/manga", "mangaupdates"):
            connexion.execute(
                "INSERT INTO manga.kitsu_mappings "
                "(kitsu_id, external_site, external_id) VALUES (1, %s, '101')",
                (site,),
            )
        connexion.commit()
        total = connexion.execute(
            "SELECT count(*) FROM manga.kitsu_mappings WHERE kitsu_id = 1"
        ).fetchone()
    assert total[0] == 3


def test_wd_formes_refuse_un_type_inconnu(base_migree):
    with psycopg.connect(base_migree) as connexion:
        connexion.execute("INSERT INTO manga.wd_pivot (qid) VALUES ('Q1')")
        connexion.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.wd_formes (qid, forme, forme_norm, forme_type) "
                "VALUES ('Q1', 'X', 'x', 'surnom')"
            )


def test_wd_formes_refuse_un_qid_inexistant(base_migree):
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connexion.execute(
                "INSERT INTO manga.wd_formes (qid, forme, forme_norm, forme_type) "
                "VALUES ('Q404', 'X', 'x', 'label')"
            )


# Les trois côtés que la cascade compare réellement. Sentinelle EN DUR : c'est
# le contrat, pas un inventaire. Une table qui rejoint la famille (kitsu_staff
# en 009) est couverte par la dérivation ci-dessous sans retoucher ce test ;
# une table qui la QUITTE doit faire échouer, d'où la liste explicite.
TABLES_DU_RAPPROCHEMENT = {"ms_formes", "wd_formes", "kitsu_formes"}


def test_006_pose_les_index_trigram_des_deux_cotes(base_migree):
    """La cascade compare des colonnes normalisées entre elles : chacune doit
    porter le MÊME outillage des deux côtés, sinon le plan d'exécution diverge.

    L'inventaire est dérivé du catalogue (dette 22.2) ; le noyau attendu reste
    écrit en dur, sans quoi le test comparerait la base à elle-même.
    """
    with psycopg.connect(base_migree) as connexion:
        trigramme = {
            t
            for (t,) in connexion.execute(
                "SELECT DISTINCT tablename FROM pg_indexes "
                "WHERE schemaname = 'manga' AND indexdef LIKE '%gin_trgm_ops%'"
            ).fetchall()
        }
        btree = {
            t
            for (t,) in connexion.execute(
                "SELECT DISTINCT tablename FROM pg_indexes "
                "WHERE schemaname = 'manga' AND indexdef LIKE '%USING btree%' "
                "AND (indexdef LIKE '%_norm)%' OR indexdef LIKE '%_norm %')"
            ).fetchall()
        }

    manquantes = TABLES_DU_RAPPROCHEMENT - trigramme
    assert not manquantes, f"index trigramme absent sur {sorted(manquantes)}"

    # Dérivé : toute table indexée en trigramme doit aussi l'être en btree —
    # l'égalité exacte passe par le btree, le flou par le GIN. En avoir un seul
    # des deux est une demi-mesure silencieuse.
    sans_btree = trigramme - btree
    assert not sans_btree, (
        f"{sorted(sans_btree)} portent un index trigramme sans btree sur la "
        "colonne normalisée"
    )


def test_le_pont_wikidata_kitsu_est_joignable(base_migree):
    """La raison d'être de 006 : sans identifiant commun entre plateformes, le
    pivot Wikidata (mal_id) ne rejoint Kitsu (kitsu_id) que par cette table."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.wd_pivot (qid, mal_id) VALUES ('Q1', '101')"
        )
        connexion.execute(
            "INSERT INTO manga.kitsu_mappings (kitsu_id, external_site, external_id) "
            "VALUES (42, 'myanimelist/manga', '101')"
        )
        connexion.commit()
        pont = connexion.execute(
            "SELECT p.qid, m.kitsu_id FROM manga.wd_pivot p "
            "JOIN manga.kitsu_mappings m ON m.external_site = 'myanimelist/manga' "
            "  AND m.external_id = p.mal_id"
        ).fetchall()
    assert pont == [("Q1", 42)]


# --------------------------------------------------------------------------- #
#  Le SQL livré par 007
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table", ["manga.mi_sorties", "manga.mi_series"])
def test_007_cree_les_tables_mi(base_migree, table):
    with psycopg.connect(base_migree) as connexion:
        existe = connexion.execute("SELECT to_regclass(%s)", (table,)).fetchone()
    assert existe[0] is not None


def test_007_ean_n_est_pas_une_cle(base_migree):
    """La révision de décision, rendue exécutable : 534 EAN portent plusieurs
    sorties dans le fichier réel — un UNIQUE en perdrait 687."""
    with psycopg.connect(base_migree) as connexion:
        for titre in ("Berserk of Gluttony Vol.12", "Martial Universe Vol.10"):
            connexion.execute(
                "INSERT INTO manga.mi_sorties (ean, titre) "
                "VALUES ('9782487369641', %s)",
                (titre,),
            )
        connexion.commit()
        total = connexion.execute(
            "SELECT count(*) FROM manga.mi_sorties WHERE ean = '9782487369641'"
        ).fetchone()
    assert total[0] == 2, "deux œuvres au même EAN doivent coexister"


def test_007_une_sortie_sans_ean_est_acceptee(base_migree):
    """1 721 lignes (3,52 %) n'ont aucun EAN : un NOT NULL les perdrait."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute("INSERT INTO manga.mi_sorties (titre) VALUES ('Sans EAN')")
        connexion.commit()
        total = connexion.execute(
            "SELECT count(*) FROM manga.mi_sorties WHERE ean IS NULL"
        ).fetchone()
    assert total[0] == 1


def test_v_mi_ean_multiples_signale_les_titres_divergents(base_migree):
    """Un EAN sur des titres différents est signalé, sur un même titre il ne l'est pas.

    Deux œuvres sur un EAN (erreur de la source), ou deux éditions titrées
    différemment (NonNonBa) : titres divergents. Le même titre deux fois (une
    réimpression) : non. Un EAN seul n'est pas multiple.
    """
    sorties = [
        ("9782487369641", "Berserk of Gluttony Vol.12"),
        ("9782487369641", "Martial Universe Vol.10"),
        ("9782360810284", "NonNonBa - Edition 2011"),
        ("9782360810284", "NonNonBa - Edition 2024"),
        ("9780000000017", "Réimpression Vol.1"),
        ("9780000000017", "Réimpression Vol.1"),
        ("9780000000024", "Seul"),
    ]
    with psycopg.connect(base_migree) as connexion:
        with connexion.cursor() as curseur:
            curseur.executemany(
                "INSERT INTO manga.mi_sorties (ean, titre) VALUES (%s, %s)", sorties
            )
        connexion.commit()
        lignes = connexion.execute(
            "SELECT ean, nb_sorties, titres_divergents FROM manga.v_mi_ean_multiples"
            " ORDER BY ean"
        ).fetchall()
    assert lignes == [
        ("9780000000017", 2, False),
        ("9782360810284", 2, True),
        ("9782487369641", 2, True),
    ], "un EAN seul n'est pas multiple"


# --------------------------------------------------------------------------- #
#  Le SQL livré par 009
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "table", ["staging.kitsu_staff", "manga.kitsu_staff", "manga.kitsu_meta"]
)
def test_009_cree_les_tables_du_staff_et_de_la_meta(base_migree, table):
    with psycopg.connect(base_migree) as connexion:
        existe = connexion.execute("SELECT to_regclass(%s)", (table,)).fetchone()
    assert existe[0] is not None, f"{table} manque après 009"


def test_kitsu_staff_dedoublonne_personne_et_role(base_migree):
    """MUTATION : sans l'UNIQUE, un rechargement dupliquerait les 53 183 lignes."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.kitsu_staff "
            "(kitsu_id, personne, personne_norm, role) "
            "VALUES (1, 'Shouko Fukaki', 'shouko fukaki', 'Art')"
        )
        connexion.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            connexion.execute(
                "INSERT INTO manga.kitsu_staff "
                "(kitsu_id, personne, personne_norm, role) "
                "VALUES (1, 'SHOUKO FUKAKI', 'shouko fukaki', 'Art')"
            )


def test_kitsu_staff_accepte_une_personne_dans_deux_roles(base_migree):
    """Le rôle fait partie de la clé : un auteur crédité Story ET Art sur la
    même œuvre est un fait de la source, pas un doublon."""
    with psycopg.connect(base_migree) as connexion:
        for role in ("Story", "Art"):
            connexion.execute(
                "INSERT INTO manga.kitsu_staff "
                "(kitsu_id, personne, personne_norm, role) "
                "VALUES (1, 'Akira Toriyama', 'akira toriyama', %s)",
                (role,),
            )
        connexion.commit()
        total = connexion.execute(
            "SELECT count(*) FROM manga.kitsu_staff WHERE kitsu_id = 1"
        ).fetchone()
    assert total[0] == 2


def test_kitsu_staff_meme_personne_sur_deux_oeuvres(base_migree):
    """L'axe utile de l'étage 2 : un auteur signe plusieurs œuvres."""
    with psycopg.connect(base_migree) as connexion:
        for kitsu_id in (1, 2):
            connexion.execute(
                "INSERT INTO manga.kitsu_staff "
                "(kitsu_id, personne, personne_norm, role) "
                "VALUES (%s, 'Naoki Urasawa', 'naoki urasawa', 'Story & Art')",
                (kitsu_id,),
            )
        connexion.commit()
        total = connexion.execute(
            "SELECT count(*) FROM manga.kitsu_staff "
            "WHERE personne_norm = 'naoki urasawa'"
        ).fetchone()
    assert total[0] == 2


def test_kitsu_meta_refuse_un_subtype_hors_cible(base_migree):
    """MUTATION : sans ce CHECK, les 15 224 light novels entreraient dans la
    cible de matching par la porte de l'année."""
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.kitsu_meta (kitsu_id, annee, subtype) "
                "VALUES (1, 2005, 'novel')"
            )


def test_kitsu_meta_accepte_une_annee_absente(base_migree):
    """44 entrées de la cible ont startDate NULL : une colonne honnêtement vide
    vaut mieux qu'une année inventée."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.kitsu_meta (kitsu_id, annee, subtype) "
            "VALUES (1, NULL, 'manga')"
        )
        connexion.commit()
        ligne = connexion.execute(
            "SELECT annee FROM manga.kitsu_meta WHERE kitsu_id = 1"
        ).fetchone()
    assert ligne[0] is None


def test_kitsu_meta_un_seul_enregistrement_par_oeuvre(base_migree):
    """Le grain est l'œuvre : l'année ne doit pas pouvoir diverger d'elle-même."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.kitsu_meta (kitsu_id, annee, subtype) "
            "VALUES (1, 2005, 'manga')"
        )
        connexion.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            connexion.execute(
                "INSERT INTO manga.kitsu_meta (kitsu_id, annee, subtype) "
                "VALUES (1, 1999, 'manga')"
            )


def test_018_ajoute_kitsu_propagation(base_migree):
    """La propagation du kitsu_id par identifiant est un étage nommé : sa
    décision porte sa méthode, et désigne sa décision source dans details."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.match_decision (series_id, method, status, details) "
            "VALUES (1, 'kitsu_propagation', 'auto', %s)",
            ('{"decision_source": 7, "kitsu_id": 38}',),
        )
        connexion.commit()


def test_018_conserve_les_methodes_anterieures(base_migree):
    """Élargir n'est pas remplacer : les dix méthodes de 011 restent valides."""
    with psycopg.connect(base_migree) as connexion:
        for methode in (
            "kitsu_bridge",
            "exact",
            "exact_author",
            "exact_kitsu",
            "exact_kitsu_author",
            "trgm",
            "embedding",
            "llm_review",
            "human_review",
            "manual",
        ):
            connexion.execute(
                "INSERT INTO manga.match_decision (series_id, method, status) "
                "VALUES (1, %s, 'auto')",
                (methode,),
            )
        connexion.commit()


def test_018_refuse_toujours_une_methode_hors_cascade(base_migree):
    """MUTATION : le CHECK à onze valeurs doit rester un CHECK."""
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.match_decision (series_id, method, status) "
                "VALUES (1, 'kitsu_propagation_bis', 'auto')"
            )


def test_details_est_nul_par_defaut_et_accepte_du_jsonb(base_migree):
    """Les décisions des étages 0 et 1 restent sans details : le journal est
    append-only, on ne rétro-remplit pas."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.match_decision (series_id, method, status) "
            "VALUES (1, 'exact', 'auto')"
        )
        connexion.execute(
            "INSERT INTO manga.match_decision (series_id, method, status, details) "
            "VALUES (2, 'exact_kitsu', 'auto', %s)",
            ('{"case": "auto_k_historique_confirme"}',),
        )
        connexion.commit()
        lignes = connexion.execute(
            "SELECT series_id, details FROM manga.match_decision ORDER BY series_id"
        ).fetchall()
    assert lignes[0][1] is None, "une décision sans contexte le dit"
    assert lignes[1][1] == {"case": "auto_k_historique_confirme"}


def test_details_est_interrogeable_par_case(base_migree):
    """La raison d'être de la colonne : ré-instruire un dossier depuis la SEULE
    base, sans dépendre des CSV de rapport."""
    with psycopg.connect(base_migree) as connexion:
        for series_id, case in ((1, "auto_k_auteur"), (2, "review_k_sans_signal")):
            connexion.execute(
                "INSERT INTO manga.match_decision "
                "(series_id, method, status, details) "
                "VALUES (%s, 'exact_kitsu', 'auto', %s)",
                (series_id, '{"case": "' + case + '"}'),
            )
        connexion.commit()
        trouve = connexion.execute(
            "SELECT series_id FROM manga.match_decision "
            "WHERE details->>'case' = 'review_k_sans_signal'"
        ).fetchall()
    assert trouve == [(2,)]


# --------------------------------------------------------------------------- #
#  Le SQL livré par 015 — les personnages, multi-sources
# --------------------------------------------------------------------------- #


def test_015_l_unicite_porte_sur_la_source_jamais_sur_le_nom(base_migree):
    """Deux sources, deux lignes : un personnage Wikipédia coexiste avec son
    homologue Kitsu, c'est la cascade qui les rapprochera plus tard. Une même
    source ne porte jamais deux fois le même identifiant."""
    with psycopg.connect(base_migree) as connexion:
        (contrainte,) = connexion.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'characters_source_id_unique'"
        ).fetchone()
        assert contrainte == "UNIQUE (source, source_id)"
        for source in ("kitsu", "wikipedia_fr"):
            connexion.execute(
                "INSERT INTO manga.characters (source, source_id, canonical_name)"
                " VALUES (%s, '1', 'Monkey D. Luffy')",
                (source,),
            )
        with pytest.raises(psycopg.errors.UniqueViolation):
            connexion.execute(
                "INSERT INTO manga.characters (source, source_id, canonical_name)"
                " VALUES ('kitsu', '1', 'Un autre nom')"
            )


def _personnage(connexion, source_id: str = "1") -> int:
    """Un personnage Kitsu ; rend son character_uid."""
    (uid,) = connexion.execute(
        "INSERT INTO manga.characters (source, source_id, canonical_name)"
        " VALUES ('kitsu', %s, 'Monkey D. Luffy') RETURNING character_uid",
        (source_id,),
    ).fetchone()
    return uid


def test_015_les_vocabulaires_fermes_refusent_l_inconnu(base_migree):
    """Trois types de forme, trois langues de description : une valeur hors liste
    est refusée, jamais rangée dans la case la moins fausse. Une description sans
    langue reste admise, et sa longueur est dérivée du texte."""
    with psycopg.connect(base_migree, autocommit=True) as connexion:
        uid = _personnage(connexion)
        for forme_type in ("canonical", "name_lang", "alias"):
            connexion.execute(
                "INSERT INTO manga.character_forms"
                " (character_uid, forme, forme_norm, forme_type, source)"
                " VALUES (%s, 'Luffy', 'luffy', %s, 'kitsu')",
                (uid, forme_type),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.character_forms"
                " (character_uid, forme, forme_norm, forme_type, source)"
                " VALUES (%s, 'Mugiwara', 'mugiwara', 'surnom', 'kitsu')",
                (uid,),
            )
        connexion.execute(
            "INSERT INTO manga.character_descriptions (character_uid, source, texte)"
            " VALUES (%s, 'kitsu', 'Capitaine des Chapeaux de paille.')",
            (uid,),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.character_descriptions"
                " (character_uid, source, lang, texte)"
                " VALUES (%s, 'wikipedia_fr', 'de', 'Kapitän der Strohhutbande.')",
                (uid,),
            )
        descriptions = connexion.execute(
            "SELECT lang, longueur FROM manga.character_descriptions"
        ).fetchall()
    assert descriptions == [(None, 33)]


def test_015_supprimer_un_personnage_emporte_ses_formes_descriptions_et_liens(
    base_migree,
):
    """ON DELETE CASCADE sur les trois tables filles : un personnage retiré ne
    laisse aucune forme, description ni lien orphelin — ceux d'un autre
    personnage restent."""
    with psycopg.connect(base_migree) as connexion:
        retire, garde = _personnage(connexion, "1"), _personnage(connexion, "2")
        for uid in (retire, garde):
            connexion.execute(
                "INSERT INTO manga.character_forms"
                " (character_uid, forme, forme_norm, forme_type, source)"
                " VALUES (%s, 'Luffy', 'luffy', 'canonical', 'kitsu')",
                (uid,),
            )
            connexion.execute(
                "INSERT INTO manga.character_descriptions"
                " (character_uid, source, lang, texte)"
                " VALUES (%s, 'kitsu', 'en', 'Captain.')",
                (uid,),
            )
            connexion.execute(
                "INSERT INTO manga.character_work (character_uid, oeuvre_source,"
                " oeuvre_id, role_source, role_normalise, source)"
                " VALUES (%s, 'kitsu', '13', 'main', 'principal', 'kitsu')",
                (uid,),
            )
        connexion.execute(
            "DELETE FROM manga.characters WHERE character_uid = %s", (retire,)
        )
        connexion.commit()
        restants = [
            connexion.execute(
                f"SELECT array_agg(DISTINCT character_uid) FROM {table}"  # nosec B608
            ).fetchone()[0]
            for table in (
                "manga.character_forms",
                "manga.character_descriptions",
                "manga.character_work",
            )
        ]
    assert restants == [[garde], [garde], [garde]]


# --------------------------------------------------------------------------- #
#  Le SQL livré par 008 — formes des auteurs et sitelink japonais
# --------------------------------------------------------------------------- #


def test_008_ajoute_wiki_ja_et_auteur_lang(base_migree):
    """Deux colonnes additives, en TEXT et facultatives, à côté de leurs sœurs."""
    with psycopg.connect(base_migree) as connexion:
        colonnes = connexion.execute(
            "SELECT table_name, column_name, data_type, is_nullable"
            " FROM information_schema.columns WHERE table_schema = 'manga'"
            " AND (table_name, column_name) IN"
            " (('wd_pivot', 'wiki_ja'), ('wd_auteurs', 'auteur_lang'))"
            " ORDER BY table_name"
        ).fetchall()
        connexion.execute(
            "INSERT INTO manga.wd_pivot (qid, wiki_fr, wiki_ja)"
            " VALUES ('Q1', 'JoJo''s Bizarre Adventure', 'ジョジョの奇妙な冒険')"
        )
        connexion.execute(
            "INSERT INTO manga.wd_auteurs (qid, auteur_qid, auteur, auteur_lang)"
            " VALUES ('Q1', 'Q2', '荒木飛呂彦', 'ja')"
        )
        connexion.commit()
        lu = connexion.execute(
            "SELECT p.wiki_ja, a.auteur_lang FROM manga.wd_pivot p"
            " JOIN manga.wd_auteurs a USING (qid)"
        ).fetchone()
    assert colonnes == [
        ("wd_auteurs", "auteur_lang", "text", "YES"),
        ("wd_pivot", "wiki_ja", "text", "YES"),
    ]
    assert lu == ("ジョジョの奇妙な冒険", "ja")


def test_wd_auteurs_formes_une_forme_par_auteur(base_migree):
    """UNIQUE (auteur_qid, forme_norm) : la même forme normalisée ne s'empile pas
    sur un auteur, mais deux auteurs homonymes la portent chacun."""
    with psycopg.connect(base_migree) as connexion:
        for auteur_qid in ("Q2", "Q3"):
            connexion.execute(
                "INSERT INTO manga.wd_auteurs_formes"
                " (auteur_qid, forme, forme_norm, forme_type, langue)"
                " VALUES (%s, 'Hirohiko Araki', 'hirohiko araki', 'label', 'en')",
                (auteur_qid,),
            )
        connexion.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            connexion.execute(
                "INSERT INTO manga.wd_auteurs_formes"
                " (auteur_qid, forme, forme_norm, forme_type)"
                " VALUES ('Q2', 'HIROHIKO ARAKI', 'hirohiko araki', 'alias')"
            )


def test_wd_auteurs_formes_refuse_un_type_de_forme_inconnu(base_migree):
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.wd_auteurs_formes"
                " (auteur_qid, forme, forme_norm, forme_type)"
                " VALUES ('Q2', 'Araki', 'araki', 'pseudonyme')"
            )


def test_wd_auteurs_formes_n_a_aucune_cle_vers_wd_pivot(base_migree):
    """auteur_qid n'est pas un qid d'œuvre : un auteur absent de wd_pivot est
    accepté, et la table ne porte aucune clé étrangère."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.wd_auteurs_formes"
            " (auteur_qid, forme, forme_norm, forme_type, langue)"
            " VALUES ('Q404', '荒木飛呂彦', '荒木飛呂彦', 'label', 'ja')"
        )
        connexion.commit()
        cles = connexion.execute(
            "SELECT count(*) FROM pg_constraint"
            " WHERE conrelid = 'manga.wd_auteurs_formes'::regclass AND contype = 'f'"
        ).fetchone()
        pivot = connexion.execute("SELECT count(*) FROM manga.wd_pivot").fetchone()
    assert cles == (0,)
    assert pivot == (0,), "l'auteur est entré sans qid d'œuvre"


# --------------------------------------------------------------------------- #
#  Le SQL livré par 010 — les avis du juge, hors du journal des décisions
# --------------------------------------------------------------------------- #

AVIS = (
    "INSERT INTO manga.llm_avis (series_id, run_ts, phase, candidat_type,"
    " candidat_id, verdict, confiance, modele, prompt_version)"
    " VALUES (%(series_id)s, %(run_ts)s, %(phase)s, %(candidat_type)s,"
    " %(candidat_id)s, %(verdict)s, %(confiance)s, 'juge', 'p1')"
)
UN_AVIS = {
    "series_id": 1,
    "run_ts": "2026-09-20T10:00:00Z",
    "phase": "file",
    "candidat_type": "qid",
    "candidat_id": "Q1",
    "verdict": "same_work",
    "confiance": "haute",
}


def _serie(connexion, series_id: int = 1) -> None:
    connexion.execute(
        "INSERT INTO manga.ms_series_enriched (series_id) VALUES (%s)", (series_id,)
    )


def test_llm_avis_accepte_un_avis_et_pose_les_marqueurs_a_faux(base_migree):
    """Les deux marqueurs de mesure valent faux par défaut : un avis n'est
    « partiel » ou « de pré-validation » que si l'écriture le dit."""
    with psycopg.connect(base_migree) as connexion:
        _serie(connexion)
        connexion.execute(AVIS, UN_AVIS)
        connexion.commit()
        ligne = connexion.execute(
            "SELECT pre_validation_bandes, dossier_partiel, created_at IS NOT NULL,"
            " justification FROM manga.llm_avis"
        ).fetchone()
    assert ligne == (False, False, True, None)


@pytest.mark.parametrize(
    "colonne, valeur",
    [
        ("phase", "production"),
        ("candidat_type", "mal_id"),
        ("verdict", "probably_same"),
        ("confiance", "basse"),
    ],
)
def test_llm_avis_refuse_une_valeur_hors_contrat(base_migree, colonne, valeur):
    """Trois phases, deux référentiels candidats, trois verdicts, deux niveaux de
    confiance : rien d'autre ne s'écrit."""
    with psycopg.connect(base_migree) as connexion:
        _serie(connexion)
        connexion.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(AVIS, UN_AVIS | {colonne: valeur})


def test_llm_avis_rejouer_un_run_n_empile_pas(base_migree):
    """L'unicité porte sur (série, candidat, run, phase) : un rejeu est refusé ;
    un nouveau run, une autre phase ou un autre référentiel sur le même couple
    est une autre mesure."""
    with psycopg.connect(base_migree, autocommit=True) as connexion:
        _serie(connexion)
        connexion.execute(AVIS, UN_AVIS)
        with pytest.raises(psycopg.errors.UniqueViolation):
            connexion.execute(AVIS, UN_AVIS | {"verdict": "different_work"})
        connexion.execute(AVIS, UN_AVIS | {"run_ts": "2026-09-21T10:00:00Z"})
        connexion.execute(AVIS, UN_AVIS | {"phase": "echantillon"})
        connexion.execute(AVIS, UN_AVIS | {"candidat_type": "kitsu_id"})
        total = connexion.execute("SELECT count(*) FROM manga.llm_avis").fetchone()
    assert total == (4,)


def test_llm_avis_suit_la_serie_du_socle(base_migree):
    """Un avis sur une série inexistante est refusé ; une série retirée du socle
    emporte ses avis (ON DELETE CASCADE)."""
    with psycopg.connect(base_migree, autocommit=True) as connexion:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connexion.execute(AVIS, UN_AVIS | {"series_id": 404})
        _serie(connexion, 1)
        _serie(connexion, 2)
        connexion.execute(AVIS, UN_AVIS)
        connexion.execute(AVIS, UN_AVIS | {"series_id": 2})
        connexion.execute("DELETE FROM manga.ms_series_enriched WHERE series_id = 1")
        restants = connexion.execute("SELECT series_id FROM manga.llm_avis").fetchall()
    assert restants == [(2,)]


# --------------------------------------------------------------------------- #
#  Le SQL livré par 013 — le référentiel des genres
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "code, label_fr",
    [
        ("Science_fiction", "Science-fiction"),
        ("tranche de vie", "Tranche de vie"),
        ("1_shonen", "Shōnen"),
        ("école", "École"),
        ("_romance", "Romance"),
        ("romance", ""),
    ],
)
def test_genre_ref_refuse_un_code_ou_un_libelle_mal_forme(base_migree, code, label_fr):
    """Le code est un identifiant ASCII minuscule en snake_case, jamais du
    français ; un code sans libellé français serait invisible à l'écran."""
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.genre_ref (code, label_fr) VALUES (%s, %s)",
                (code, label_fr),
            )


def test_genre_mapping_mappe_si_et_seulement_si_un_code(base_migree):
    """L'invariant central : un `mappe` sans code (décision annoncée mais pas
    prise) et un `exclu` / `inconnu` avec code (décision prise en douce) sont
    tous deux refusés."""
    with psycopg.connect(base_migree, autocommit=True) as connexion:
        connexion.execute(
            "INSERT INTO manga.genre_ref (code, label_fr) VALUES ('romance', 'Romance')"
        )
        insertion = (
            "INSERT INTO manga.genre_mapping (source, libelle_brut, code, statut)"
            " VALUES ('ms', %s, %s, %s)"
        )
        for libelle, code, statut in (
            ("romance", None, "mappe"),
            ("Manga", "romance", "exclu"),
            ("Ecole", "romance", "inconnu"),
        ):
            with pytest.raises(psycopg.errors.CheckViolation):
                connexion.execute(insertion, (libelle, code, statut))
        connexion.execute(insertion, ("romance", "romance", "mappe"))
        connexion.execute(insertion, ("Manga", None, "exclu"))
        connexion.execute(insertion, ("Ecole", None, "inconnu"))
        total = connexion.execute("SELECT count(*) FROM manga.genre_mapping").fetchone()
    assert total == (3,)


def test_genre_mapping_une_ligne_par_libelle_observe_et_par_source(base_migree):
    """Le libellé est gardé TEL QU'OBSERVÉ : « Ecole » n'est pas « École », et le
    même libellé chez deux sources fait deux lignes. Une troisième source, un
    doublon ou un code absent du référentiel sont refusés."""
    with psycopg.connect(base_migree, autocommit=True) as connexion:
        connexion.execute(
            "INSERT INTO manga.genre_ref (code, label_fr) VALUES ('fantasy', 'Fantasy')"
        )
        insertion = (
            "INSERT INTO manga.genre_mapping (source, libelle_brut, code, statut)"
            " VALUES (%s, %s, %s, %s)"
        )
        for source in ("ms", "kitsu"):
            connexion.execute(insertion, (source, "Fantasy", "fantasy", "mappe"))
        for libelle in ("Ecole", "École"):
            connexion.execute(insertion, ("ms", libelle, None, "inconnu"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            connexion.execute(insertion, ("ms", "Fantasy", None, "exclu"))
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(insertion, ("anilist", "Fantasy", "fantasy", "mappe"))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connexion.execute(insertion, ("ms", "Isekai", "isekai", "mappe"))
        total = connexion.execute("SELECT count(*) FROM manga.genre_mapping").fetchone()
    assert total == (4,)


def test_013_accorde_la_seule_lecture_a_manga_ro(base_migree):
    with psycopg.connect(base_migree) as connexion:
        droits = connexion.execute(
            "SELECT t, p, has_table_privilege('manga_ro', t, p)"
            " FROM unnest(ARRAY['manga.genre_ref', 'manga.genre_mapping']) t,"
            " unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) p"
        ).fetchall()
    assert {(t, p) for t, p, accorde in droits if accorde} == {
        ("manga.genre_mapping", "SELECT"),
        ("manga.genre_ref", "SELECT"),
    }


# --------------------------------------------------------------------------- #
#  Le SQL livré par 014 — la hiérarchie et la nature des codes
# --------------------------------------------------------------------------- #


def test_014_type_vaut_genre_par_defaut_et_ne_connait_que_deux_natures(base_migree):
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.genre_ref (code, label_fr) VALUES ('romance', 'Romance')"
        )
        connexion.commit()
        (nature,) = connexion.execute(
            "SELECT type FROM manga.genre_ref WHERE code = 'romance'"
        ).fetchone()
        assert nature == "genre"
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.genre_ref (code, label_fr, type)"
                " VALUES ('isekai', 'Isekai', 'theme')"
            )


def test_014_le_parent_se_verifie_a_la_validation_pas_a_l_ecriture(base_migree):
    """FK DEFERRABLE INITIALLY DEFERRED : dans une transaction, l'enfant peut
    précéder son parent (un `unnest` n'ordonne rien) ; c'est l'état à la
    validation qui compte, et un parent qui n'arrive jamais fait échouer le
    COMMIT."""
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.genre_ref (code, label_fr, parent)"
            " VALUES ('yaoi', 'Yaoi', 'lgbt')"
        )
        connexion.execute(
            "INSERT INTO manga.genre_ref (code, label_fr) VALUES ('lgbt', 'LGBT')"
        )
        connexion.commit()
        connexion.execute(
            "INSERT INTO manga.genre_ref (code, label_fr, parent)"
            " VALUES ('yuri', 'Yuri', 'inexistant')"
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connexion.commit()
        codes = connexion.execute(
            "SELECT code, parent FROM manga.genre_ref ORDER BY code"
        ).fetchall()
    assert codes == [("lgbt", None), ("yaoi", "lgbt")]


def test_014_un_code_n_est_pas_son_propre_parent(base_migree):
    """Le seul cycle qu'une contrainte de ligne sait voir ; il est refusé dès
    l'écriture, sans attendre la validation."""
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.genre_ref (code, label_fr, parent)"
                " VALUES ('adulte', 'Adulte', 'adulte')"
            )


def test_014_rejouee_sur_des_codes_charges_bascule_les_formats(base_migree):
    """Le chemin d'`apimanga` : les codes de 013 sont déjà là quand 014 passe. Le
    rattrapage les range par leur préfixe, et la migration se rejoue sans
    erreur (IF NOT EXISTS, contraintes reposées)."""
    sql = (migrate.MIGRATIONS_DIR / "014_hierarchie_genres.sql").read_text(
        encoding="utf-8"
    )
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.genre_ref (code, label_fr) VALUES"
            " ('format_doujinshi', 'Doujinshi'), ('romance', 'Romance'),"
            " ('formation', 'Récit de formation')"
        )
        connexion.commit()
        connexion.execute(sql)
        connexion.commit()
        natures = connexion.execute(
            "SELECT code, type FROM manga.genre_ref ORDER BY code"
        ).fetchall()
    assert natures == [
        ("format_doujinshi", "format"),
        ("formation", "genre"),
        ("romance", "genre"),
    ], "seul le préfixe « format_ » (souligné compris) fait un format"
