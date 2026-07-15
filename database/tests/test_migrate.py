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
    assert migrate.commande_up(UP) == 0

    assert versions_appliquees(base) == ["001", "002"]
    assert "001 appliquée" in capsys.readouterr().out


def test_up_rejoue_est_un_noop(base, capsys):
    migrate.commande_up(UP)
    capsys.readouterr()

    assert migrate.commande_up(UP) == 0

    assert "Rien à appliquer" in capsys.readouterr().out
    assert versions_appliquees(base) == ["001", "002"]


def test_status_avant_et_apres(base, capsys):
    assert migrate.commande_status(STATUS) == 0
    avant = capsys.readouterr().out
    assert "0 appliquée(s), 2 en attente" in avant
    assert "en attente" in avant

    migrate.commande_up(UP)
    capsys.readouterr()

    assert migrate.commande_status(STATUS) == 0
    assert "2 appliquée(s), 0 en attente" in capsys.readouterr().out


def test_target_s_arrete_a_la_version_demandee(base):
    assert migrate.commande_up(Namespace(commande="up", target="001")) == 0

    assert versions_appliquees(base) == ["001"]

    assert migrate.commande_up(UP) == 0
    assert versions_appliquees(base) == ["001", "002"]


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
#  Le SQL livré par 001
# --------------------------------------------------------------------------- #


@pytest.fixture
def base_migree(base):
    migrate.commande_up(UP)
    return base


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
    assert len(lignes) == 7
    assert loaded[0] == 7


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
