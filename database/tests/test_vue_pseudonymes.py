"""Migration 024 — la vue des pseudonymes, `manga.v_pseudonymes`, lisible par
`modele_ro`.

En une phrase : `modele_ro` gagne un seul droit, `SELECT` sur la vue, qui rend les noms
distincts des auteurs de critiques et rien d'autre ; la table des critiques lui reste
illisible (septième refus, après les six de 023, qui tiennent toujours : ils sont
éprouvés par `test_role_modele.py` sur la même base migrée jusqu'au bout).

Les noms d'essai sont factices ; aucun test ne lit la base réelle.
"""

from __future__ import annotations

import psycopg
import pytest
from conftest import harnais, migrate
from psycopg import errors
from test_role_modele import DROITS_DU_ROLE, modele_api  # noqa: F401 (fixture)

FICHIER = migrate.MIGRATIONS_DIR / "024_vue_pseudonymes.sql"
VUE = "manga.v_pseudonymes"
#: Des auteurs factices : un doublon, une valeur absente.
AUTEURS = ("lecteur-factice-a", "lecteur-factice-b", "lecteur-factice-a", None)


def droits(dsn: str, role: str) -> list[tuple[str, str, str]]:
    with psycopg.connect(dsn) as connexion:
        return connexion.execute(DROITS_DU_ROLE, {"role": role}).fetchall()


def test_024_n_accorde_a_modele_ro_que_la_vue(base):
    harnais.migrer(base, "023")
    avant = droits(base, "modele_ro")
    harnais.migrer(base, "024")
    apres = droits(base, "modele_ro")

    assert avant, "modele_ro sans droit à 023 : le test ne prouverait rien"
    assert sorted(set(apres) - set(avant)) == [("relation", VUE, "SELECT")]
    assert set(avant) <= set(apres)


def test_la_vue_rend_les_noms_distincts_sous_le_role_de_l_api(
    base_migree,
    modele_api,  # noqa: F811
):
    with psycopg.connect(base_migree) as admin:
        for auteur in AUTEURS:
            admin.execute(
                "INSERT INTO manga.ms_reviews_all (review_author) VALUES (%s)",
                (auteur,),
            )
    with psycopg.connect(modele_api) as connexion:
        noms = [
            ligne[0]
            for ligne in connexion.execute(
                "SELECT review_author FROM manga.v_pseudonymes ORDER BY 1"
            ).fetchall()
        ]

    assert noms == ["lecteur-factice-a", "lecteur-factice-b"]


@pytest.mark.parametrize(
    "requete",
    (
        "SELECT review_author FROM manga.ms_reviews_all",
        "SELECT review_id, review_author FROM manga.ms_reviews_all",
        "SELECT count(*) FROM manga.ms_reviews_all",
    ),
)
def test_refus_7_la_table_des_critiques_reste_illisible(
    modele_api,  # noqa: F811
    requete,
):
    """Par la vue, les noms ; jamais qui a écrit quelle critique."""
    with psycopg.connect(modele_api) as connexion:
        with pytest.raises(errors.InsufficientPrivilege):
            connexion.execute(requete)


def test_la_vue_n_a_qu_une_colonne_et_lit_avec_les_droits_de_son_proprietaire(
    base_migree,
):
    with psycopg.connect(base_migree) as connexion:
        colonnes = [
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

    assert colonnes == ["review_author"]
    # Élévation déclarée (comme 021) : pas `security_invoker`.
    assert not any("security_invoker" in o for o in options or [])


@pytest.mark.parametrize("role", ("manga_ro", "verif_ro"))
def test_024_ne_donne_rien_de_neuf_aux_autres_roles_de_lecture(base, role):
    """Par leurs droits par défaut (012, 022), la vue leur est lisible dès sa
    création ; ils lisaient déjà la table qu'elle résume."""
    harnais.migrer(base, "023")
    avant = droits(base, role)
    harnais.migrer(base, "024")
    apres = droits(base, role)
    with psycopg.connect(base) as connexion:
        (table,) = connexion.execute(
            "SELECT has_table_privilege(%s, 'manga.ms_reviews_all', 'SELECT')",
            (role,),
        ).fetchone()

    assert table is True
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
    assert "GRANT SELECT ON manga.v_pseudonymes TO modele_ro;" in code
    for interdit in ("ROLE", "LOGIN", "DEFAULT PRIVILEGES", "ALL TABLES", "REVOKE"):
        assert interdit not in code, interdit
