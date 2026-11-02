"""Migration 025 — les métadonnées d'une série, `manga.v_metadonnees_serie`, lisibles
par `modele_ro`.

En une phrase : `modele_ro` gagne un seul droit, `SELECT` sur la vue, qui rend six
valeurs par série et rien d'autre ; les tables qu'elle lit lui restent illisibles (les
refus précédents tiennent : ils sont éprouvés par `test_role_modele.py` et
`test_vue_pseudonymes.py` sur la même base migrée jusqu'au bout).

Les séries d'essai sont factices ; aucun test ne lit la base réelle.
"""

from __future__ import annotations

import psycopg
import pytest
from conftest import harnais, migrate
from psycopg import errors
from psycopg.types.json import Jsonb
from test_role_modele import DROITS_DU_ROLE, modele_api  # noqa: F401 (fixture)

FICHIER = migrate.MIGRATIONS_DIR / "025_vue_metadonnees_serie.sql"
VUE = "manga.v_metadonnees_serie"
COLONNES = [
    "series_id",
    "disponibilite",
    "nb_volumes",
    "nb_volumes_source",
    "statut",
    "demographie",
    "annee_fr",
]
EAN_FR = "9782000000001"
EAN_FR_RECENT = "9791000000001"
EAN_JP = "9784000000001"
EAN_COREE = "9791100000001"
BADGE_FR = {"pays": "français", "tomes": None, "statut": "Complète"}
BADGE_VO = {"pays": None, "tomes": "3 tomes", "statut": "Complète"}


def droits(dsn: str, role: str) -> list[tuple[str, str, str]]:
    with psycopg.connect(dsn) as connexion:
        return connexion.execute(DROITS_DU_ROLE, {"role": role}).fetchall()


def serie(
    admin: psycopg.Connection,
    series_id: int = 1,
    *,
    type_: str | None = "Manga",
    badges: list | None = None,
    categorie: str | None = None,
    categorie_propre: str | None = None,
) -> int:
    admin.execute(
        "INSERT INTO manga.ms_series_enriched (series_id, series_type,"
        " series_statuses, series_category, series_category_clean)"
        " VALUES (%s, %s, %s, %s, %s)",
        (series_id, type_, Jsonb(badges or []), categorie, categorie_propre),
    )
    return series_id


def volume(
    admin: psycopg.Connection,
    series_id: int = 1,
    url: str = "v1",
    *,
    numero: int | None = 1,
    ean: str | None = None,
    tomes: int | None = None,
    date: str | None = None,
) -> None:
    admin.execute(
        "INSERT INTO manga.ms_volumes_enriched (volume_url, series_id, volume_number,"
        " volume_ean, volume_tomes_published, volume_publication_date)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (f"{series_id}-{url}", series_id, numero, ean, tomes, date),
    )


def kitsu(admin: psycopg.Connection, series_id: int, statut: str | None) -> None:
    """Une œuvre Kitsu au statut donné, rattachée à la série par le moyeu."""
    kitsu_id = 1000 + series_id
    admin.execute(
        "INSERT INTO manga.kitsu_series_core (kitsu_id, status) VALUES (%s, %s)",
        (kitsu_id, statut),
    )
    admin.execute(
        "INSERT INTO manga.work_identity (series_id, kitsu_id) VALUES (%s, %s)",
        (series_id, str(kitsu_id)),
    )


def metadonnees(admin: psycopg.Connection, series_id: int = 1) -> dict[str, object]:
    ligne = admin.execute(
        "SELECT series_id, disponibilite, nb_volumes, nb_volumes_source, statut,"
        " demographie, annee_fr FROM manga.v_metadonnees_serie WHERE series_id = %s",
        (series_id,),
    ).fetchone()
    assert ligne is not None
    return dict(zip(COLONNES, ligne, strict=True))


# --------------------------------------------------------------------------- #
#  Le droit accordé, et ce qui reste refusé
# --------------------------------------------------------------------------- #
def test_025_n_accorde_a_modele_ro_que_la_vue(base):
    harnais.migrer(base, "024")
    avant = droits(base, "modele_ro")
    harnais.migrer(base, "025")
    apres = droits(base, "modele_ro")

    assert avant, "modele_ro sans droit à 024 : le test ne prouverait rien"
    assert sorted(set(apres) - set(avant)) == [("relation", VUE, "SELECT")]
    assert set(avant) <= set(apres)


def test_la_vue_se_lit_sous_le_role_de_l_api(base_migree, modele_api):  # noqa: F811
    with psycopg.connect(base_migree) as admin:
        serie(admin, categorie_propre="Seinen", badges=[BADGE_FR])
        volume(admin, ean=EAN_FR, tomes=3, date="2015-06-01")
    with psycopg.connect(modele_api) as connexion:
        assert metadonnees(connexion) == {
            "series_id": 1,
            "disponibilite": "edite_en_france",
            "nb_volumes": 3,
            "nb_volumes_source": "champ",
            "statut": "termine",
            "demographie": "Seinen",
            "annee_fr": 2015,
        }


@pytest.mark.parametrize(
    "requete",
    (
        "SELECT * FROM manga.ms_volumes_enriched",
        "SELECT volume_ean FROM manga.ms_volumes_enriched",
        "SELECT count(*) FROM manga.ms_volumes_enriched",
        "SELECT status FROM manga.kitsu_series_core",
        "SELECT series_statuses FROM manga.ms_series_enriched",
        "SELECT series_category_clean FROM manga.ms_series_enriched",
    ),
)
def test_refus_les_tables_lues_par_la_vue_restent_illisibles(
    modele_api,  # noqa: F811
    requete,
):
    """Par la vue, six valeurs ; jamais les volumes ni le reste de la fiche."""
    with psycopg.connect(modele_api) as connexion:
        with pytest.raises(errors.InsufficientPrivilege):
            connexion.execute(requete)


def test_la_vue_a_ses_sept_colonnes_et_lit_avec_les_droits_de_son_proprietaire(
    base_migree,
):
    with psycopg.connect(base_migree) as connexion:
        lues = [
            ligne[0]
            for ligne in connexion.execute(
                "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass"
                " AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
                (VUE,),
            ).fetchall()
        ]
        (options,) = connexion.execute(
            "SELECT reloptions FROM pg_class WHERE oid = %s::regclass", (VUE,)
        ).fetchone()
        (commentaire,) = connexion.execute(
            "SELECT obj_description(%s::regclass, 'pg_class')", (VUE,)
        ).fetchone()

    assert lues == COLONNES
    # Élévation déclarée (comme 024) : pas `security_invoker`.
    assert not any("security_invoker" in o for o in options or [])
    for regle in ("edite_en_france", "seulement_au_japon", "en stock", "Migration 025"):
        assert regle in commentaire, regle


@pytest.mark.parametrize("role", ("manga_ro", "verif_ro"))
def test_025_ne_donne_rien_de_neuf_aux_autres_roles_de_lecture(base, role):
    """Par leurs droits par défaut (012, 022), la vue leur est lisible dès sa
    création ; ils lisaient déjà les tables qu'elle résume."""
    harnais.migrer(base, "024")
    avant = droits(base, role)
    harnais.migrer(base, "025")
    apres = droits(base, role)
    with psycopg.connect(base) as connexion:
        lues = [
            connexion.execute(
                "SELECT has_table_privilege(%s, %s, 'SELECT')", (role, table)
            ).fetchone()[0]
            for table in (
                "manga.ms_series_enriched",
                "manga.ms_volumes_enriched",
                "manga.work_identity",
                "manga.kitsu_series_core",
            )
        ]

    assert all(lues)
    assert sorted(set(apres) - set(avant)) == [("relation", VUE, "SELECT")]
    assert set(avant) <= set(apres)


def test_la_migration_ne_porte_qu_une_vue_et_un_droit():
    code = "\n".join(
        ligne
        for ligne in FICHIER.read_text(encoding="utf-8").splitlines()
        if not ligne.lstrip().startswith("--")
    )

    assert code.count("CREATE VIEW") == 1
    assert code.count("GRANT") == 1
    assert "GRANT SELECT ON manga.v_metadonnees_serie TO modele_ro;" in code
    for interdit in (
        "CREATE TABLE",
        "INSERT",
        "ROLE",
        "LOGIN",
        "DEFAULT PRIVILEGES",
        "ALL TABLES",
        "REVOKE",
    ):
        assert interdit not in code, interdit


# --------------------------------------------------------------------------- #
#  Les règles
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("ean", "badges", "attendu"),
    (
        (EAN_FR, [], "edite_en_france"),
        (EAN_FR_RECENT, [], "edite_en_france"),
        (None, [BADGE_FR], "edite_en_france"),
        (EAN_JP, [BADGE_FR], "edite_en_france"),
        (EAN_JP, [], "seulement_au_japon"),
        (None, [], "seulement_au_japon"),
        (None, [BADGE_VO], "seulement_au_japon"),
        (EAN_COREE, [], "seulement_au_japon"),
    ),
)
def test_disponibilite_d_un_manga_a_volumes(base_migree, ean, badges, attendu):
    with psycopg.connect(base_migree) as admin:
        serie(admin, badges=badges)
        volume(admin, ean=ean)

        assert metadonnees(admin)["disponibilite"] == attendu


def test_une_seule_ligne_francaise_suffit_parmi_les_japonaises(base_migree):
    with psycopg.connect(base_migree) as admin:
        serie(admin)
        volume(admin, url="jp", ean=EAN_JP)
        volume(admin, url="fr", ean=EAN_FR)

        assert metadonnees(admin)["disponibilite"] == "edite_en_france"


def test_sans_volume_la_disponibilite_est_inconnue_et_les_tomes_absents(base_migree):
    with psycopg.connect(base_migree) as admin:
        serie(admin)

        lu = metadonnees(admin)

    assert lu["disponibilite"] == "inconnue"
    assert lu["nb_volumes"] is None
    assert lu["nb_volumes_source"] is None
    assert lu["annee_fr"] is None


@pytest.mark.parametrize("type_", ("Manhwa", "Manhua", "BD", "Comics", None))
def test_hors_manga_sans_edition_francaise_la_disponibilite_est_inconnue(
    base_migree, type_
):
    """Un manhwa sans édition française n'est pas « seulement au Japon »."""
    with psycopg.connect(base_migree) as admin:
        serie(admin, type_=type_)
        volume(admin)

        assert metadonnees(admin)["disponibilite"] == "inconnue"


@pytest.mark.parametrize("type_", ("Manhwa", None))
def test_hors_manga_avec_edition_francaise_la_disponibilite_reste_connue(
    base_migree, type_
):
    with psycopg.connect(base_migree) as admin:
        serie(admin, type_=type_)
        volume(admin, ean=EAN_FR)

        assert metadonnees(admin)["disponibilite"] == "edite_en_france"


def test_tomes_le_champ_prend_le_plus_grand_et_passe_avant_le_repli(base_migree):
    with psycopg.connect(base_migree) as admin:
        serie(admin)
        volume(admin, url="a", numero=1, tomes=16)
        volume(admin, url="b", numero=2, tomes=30)
        volume(admin, url="c", numero=3)

        lu = metadonnees(admin)

    assert (lu["nb_volumes"], lu["nb_volumes_source"]) == (30, "champ")


@pytest.mark.parametrize("tomes", (None, 0))
def test_tomes_le_repli_compte_les_numeros_distincts(base_migree, tomes):
    """Deux éditions du même numéro comptent une fois ; sans numéro, le volume 1."""
    with psycopg.connect(base_migree) as admin:
        serie(admin)
        volume(admin, url="a", numero=1, tomes=tomes)
        volume(admin, url="b", numero=1, tomes=tomes)
        volume(admin, url="c", numero=None, tomes=tomes)
        volume(admin, url="d", numero=2, tomes=tomes)

        lu = metadonnees(admin)

    assert (lu["nb_volumes"], lu["nb_volumes_source"]) == (
        2,
        "repli_volumes_distincts",
    )


@pytest.mark.parametrize(
    ("badges", "statut_kitsu", "attendu"),
    (
        ([BADGE_VO], None, "termine"),
        ([BADGE_FR], None, "termine"),
        ([BADGE_VO, BADGE_FR], None, "termine"),
        ([BADGE_VO], "current", "termine"),
        ([BADGE_VO], "finished", "termine"),
        ([], "finished", "termine"),
        ([], "current", "en_cours"),
        ([], "tba", None),
        ([], None, None),
    ),
)
def test_statut(base_migree, badges, statut_kitsu, attendu):
    with psycopg.connect(base_migree) as admin:
        serie(admin, badges=badges)
        volume(admin)
        kitsu(admin, 1, statut_kitsu)

        assert metadonnees(admin)["statut"] == attendu


def test_statut_sans_rattachement_au_moyeu_reste_absent(base_migree):
    with psycopg.connect(base_migree) as admin:
        serie(admin)
        volume(admin)

        assert metadonnees(admin)["statut"] is None


@pytest.mark.parametrize(
    ("propre", "brut", "attendu"),
    (
        ("Josei", "Josei", "Josei"),
        ("Shojo", "Josei", "Shojo"),
        (None, "Shojo", "Shojo"),
        (None, "Ecchi-Hentai", "Ecchi-Hentai"),
        (None, "Sonyun", "Shonen"),
        (None, "Sunjung", "Shojo"),
        (None, "Chungnyun", "Seinen"),
        (None, "2024", None),
        (None, None, None),
    ),
)
def test_demographie(base_migree, propre, brut, attendu):
    with psycopg.connect(base_migree) as admin:
        serie(admin, categorie_propre=propre, categorie=brut)
        volume(admin)

        assert metadonnees(admin)["demographie"] == attendu


def test_annee_fr_ne_lit_que_les_volumes_a_ean_francais(base_migree):
    """La série a paru au Japon en 2005 et en France en 2009."""
    with psycopg.connect(base_migree) as admin:
        serie(admin)
        volume(admin, url="jp", ean=EAN_JP, date="2005-09-29")
        volume(admin, url="sans", date="2006-01-01")
        volume(admin, url="fr-2", numero=2, ean=EAN_FR, date="2011-03-04")
        volume(admin, url="fr-1", numero=1, ean=EAN_FR_RECENT, date="2009-10-23")

        assert metadonnees(admin)["annee_fr"] == 2009


def test_annee_fr_absente_sans_ligne_francaise_datee(base_migree):
    with psycopg.connect(base_migree) as admin:
        serie(admin, badges=[BADGE_FR])
        volume(admin, url="jp", ean=EAN_JP, date="2005-09-29")
        volume(admin, url="fr", ean=EAN_FR)

        assert metadonnees(admin)["annee_fr"] is None


def test_une_ligne_par_serie(base_migree):
    with psycopg.connect(base_migree) as admin:
        serie(admin, 1)
        serie(admin, 2)
        volume(admin, 1, "a")
        volume(admin, 1, "b", numero=2)
        kitsu(admin, 1, "finished")
        lignes = admin.execute(
            "SELECT series_id FROM manga.v_metadonnees_serie ORDER BY 1"
        ).fetchall()

    assert lignes == [(1,), (2,)]
