from __future__ import annotations

from argparse import Namespace

import psycopg
import pytest
from conftest import migrate

UP = Namespace(commande="up", target=None)
STATUS = Namespace(commande="status")


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

    assert versions_appliquees(base) == [
        "000",
        "001",
        "002",
        "003",
        "004",
        "005",
        "006",
        "007",
        "008",
    ]
    assert "001 appliquée" in capsys.readouterr().out


def test_up_rejoue_est_un_noop(base, capsys):
    migrate.commande_up(UP)
    capsys.readouterr()

    assert migrate.commande_up(UP) == 0

    assert "Rien à appliquer" in capsys.readouterr().out
    assert versions_appliquees(base) == [
        "000",
        "001",
        "002",
        "003",
        "004",
        "005",
        "006",
        "007",
        "008",
    ]


def test_status_avant_et_apres(base, capsys):
    assert migrate.commande_status(STATUS) == 0
    avant = capsys.readouterr().out
    assert "0 appliquée(s), 9 en attente" in avant
    assert "en attente" in avant

    migrate.commande_up(UP)
    capsys.readouterr()

    assert migrate.commande_status(STATUS) == 0
    assert "9 appliquée(s), 0 en attente" in capsys.readouterr().out


def test_target_s_arrete_a_la_version_demandee(base):
    assert migrate.commande_up(Namespace(commande="up", target="001")) == 0

    assert versions_appliquees(base) == ["000", "001"]

    assert migrate.commande_up(UP) == 0
    assert versions_appliquees(base) == [
        "000",
        "001",
        "002",
        "003",
        "004",
        "005",
        "006",
        "007",
        "008",
    ]


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


@pytest.fixture
def base_migree(base):
    migrate.commande_up(UP)
    return base


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


@pytest.mark.parametrize("methode", ["kitsu_bridge", "trgm", "manual"])
def test_match_decision_accepte_les_methodes_de_la_cascade(base_migree, methode):
    with psycopg.connect(base_migree) as connexion:
        connexion.execute(
            "INSERT INTO manga.match_decision (series_id, method, status) "
            "VALUES (1, %s, 'auto')",
            (methode,),
        )
        connexion.commit()


def test_match_decision_refuse_une_methode_hors_cascade(base_migree):
    with psycopg.connect(base_migree) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.match_decision (series_id, method, status) "
                "VALUES (1, 'au_pif', 'auto')"
            )


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
    with psycopg.connect(base_migree) as connexion:
        lignes = connexion.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'staging' AND column_name = 'source_file'"
        ).fetchall()
        loaded = connexion.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'staging' AND column_name = 'loaded_at' "
            "AND column_default LIKE 'now()%'"
        ).fetchone()
    assert len(lignes) == 10, "7 tables de 002 + 2 de 004 + 1 de 006"
    assert loaded[0] == 10


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


def test_006_pose_les_index_trigram_des_deux_cotes(base_migree):
    """La cascade compare ms_formes à wd_formes et kitsu_formes : les trois
    colonnes forme_norm doivent porter le même outillage."""
    with psycopg.connect(base_migree) as connexion:
        lignes = connexion.execute(
            "SELECT tablename FROM pg_indexes WHERE schemaname = 'manga' "
            "AND indexdef LIKE '%gin_trgm_ops%' ORDER BY tablename"
        ).fetchall()
    assert [t for (t,) in lignes] == [
        "kitsu_formes",
        "ms_formes",
        "wd_auteurs_formes",
        "wd_formes",
    ]


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
    """La vue distingue l'erreur source (deux œuvres) de la simple réédition."""
    with psycopg.connect(base_migree) as connexion:
        for titre in ("Berserk of Gluttony Vol.12", "Martial Universe Vol.10"):
            connexion.execute(
                "INSERT INTO manga.mi_sorties (ean, titre) "
                "VALUES ('9782487369641', %s)",
                (titre,),
            )
        connexion.execute(
            "INSERT INTO manga.mi_sorties (ean, titre) VALUES ('9782360810284', 'Seul')"
        )
        connexion.commit()
        lignes = connexion.execute(
            "SELECT ean, nb_sorties, titres_divergents FROM manga.v_mi_ean_multiples"
        ).fetchall()
    assert lignes == [("9782487369641", 2, True)], "un EAN seul n'est pas multiple"
