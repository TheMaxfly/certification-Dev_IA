"""Le chargeur Wikipédia — et le test du pari multi-sources.

Si une deuxième source entre dans les mêmes tables par simple insertion, le
choix des noms neutres et de la colonne `source` était juste.
"""

import os

import psycopg
import pytest

import chargeur_wikipedia_fr as cw

DSN = os.environ.get("APIMANGA_DSN")
besoin_base = pytest.mark.skipif(not DSN, reason="APIMANGA_DSN non defini")


@pytest.fixture(scope="module")
def connexion():
    with psycopg.connect(DSN) as conn:
        yield conn


class TestArticleEffectif:
    """L'attribution doit nommer l'article d'où le texte vient réellement."""

    def test_sans_renvoi_c_est_l_article_de_serie(self) -> None:
        charge = {
            "forme_retenue": "in_situ",
            "titre_servi": "Bleach",
            "revid": 42,
            "renvoi_titre_servi": None,
            "renvoi_revid": None,
        }
        assert cw.article_effectif(charge) == ("Bleach", 42)

    def test_avec_renvoi_suivi_c_est_l_article_dedie(self) -> None:
        """Nommer l'article de série alors que le texte vient de la liste
        dédiée produirait une attribution CC BY-SA fausse."""
        charge = {
            "forme_retenue": "dedie",
            "titre_servi": "Naruto",
            "revid": 42,
            "renvoi_titre_servi": "Liste des personnages de Naruto",
            "renvoi_revid": 99,
        }
        assert cw.article_effectif(charge) == (
            "Liste des personnages de Naruto",
            99,
        )


@besoin_base
class TestChargement:
    def test_la_langue_est_affirmee_jamais_inferee(self, connexion) -> None:
        """`fr` vient de la provenance, pas d'un classifieur.

        La règle B, appliquée ici, rendrait 98,5 % de NULL sur un corpus à
        97,0 % de marqueurs français : on n'infère que ce que la source ne dit
        pas.
        """
        langues = {
            lang
            for (lang,) in connexion.execute(
                "SELECT DISTINCT lang FROM manga.character_descriptions "
                "WHERE source = 'wikipedia_fr'"
            ).fetchall()
        }
        assert langues == {"fr"}

    def test_la_licence_est_cc_by_sa_partout(self, connexion) -> None:
        licences = {
            lic
            for (lic,) in connexion.execute(
                "SELECT DISTINCT licence FROM manga.character_descriptions "
                "WHERE source = 'wikipedia_fr'"
            ).fetchall()
        }
        assert licences == {"CC BY-SA"}

    def test_le_role_est_nul_partout_et_c_est_legitime(self, connexion) -> None:
        """Wikipédia ne porte aucun vocabulaire de rôles, seulement une
        position. Ce n'est pas un chargement incomplet."""
        (non_nuls,) = connexion.execute(
            "SELECT count(*) FROM manga.character_work "
            "WHERE source = 'wikipedia_fr' "
            "  AND (role_source IS NOT NULL OR role_normalise IS NOT NULL)"
        ).fetchone()
        assert non_nuls == 0

    def test_la_graphie_japonaise_est_name_lang_jamais_alias(self, connexion) -> None:
        types = {
            t
            for (t,) in connexion.execute(
                "SELECT DISTINCT forme_type FROM manga.character_forms "
                "WHERE source = 'wikipedia_fr'"
            ).fetchall()
        }
        assert types == {"canonical", "name_lang"}
        (ja,) = connexion.execute(
            "SELECT count(*) FROM manga.character_forms "
            "WHERE source = 'wikipedia_fr' AND forme_lang = 'ja' "
            "  AND forme_type <> 'name_lang'"
        ).fetchone()
        assert ja == 0

    def test_le_double_compte_est_absorbe_par_la_separation(self, connexion) -> None:
        """Trois séries résolvant vers un même article donnent UN personnage et
        TROIS liens. Les deux grains sont donc lisibles en base."""
        (personnages,) = connexion.execute(
            "SELECT count(*) FROM manga.characters WHERE source = 'wikipedia_fr'"
        ).fetchone()
        (liens,) = connexion.execute(
            "SELECT count(*) FROM manga.character_work WHERE source = 'wikipedia_fr'"
        ).fetchone()
        assert liens > personnages, "le double compte a disparu, or il existe"
        (multi,) = connexion.execute(
            "SELECT count(*) FROM (SELECT character_uid FROM manga.character_work "
            "WHERE source = 'wikipedia_fr' GROUP BY 1 HAVING count(*) > 1) x"
        ).fetchone()
        assert multi > 0

    def test_oeuvre_source_distingue_les_deux_referentiels(self, connexion) -> None:
        """Wikipédia rattache à des `series_id` MS, Kitsu à des `kitsu_id`."""
        couples = dict(
            connexion.execute(
                "SELECT source, oeuvre_source FROM manga.character_work GROUP BY 1, 2"
            ).fetchall()
        )
        assert couples["wikipedia_fr"] == "manga_sanctuary"
        assert couples["kitsu"] == "kitsu"


@besoin_base
class TestPariMultiSources:
    """Ce que cette deuxième source devait éprouver."""

    def test_les_deux_sources_cohabitent_dans_les_memes_tables(self, connexion) -> None:
        for table in (
            "characters",
            "character_forms",
            "character_descriptions",
            "character_work",
        ):
            sources = {
                s
                for (s,) in connexion.execute(
                    f"SELECT DISTINCT source FROM manga.{table}"
                ).fetchall()
            }
            assert sources == {"kitsu", "wikipedia_fr"}, (
                f"{table} ne porte pas les deux sources"
            )

    def test_aucune_migration_n_a_ete_necessaire(self, connexion) -> None:
        """Le pari de la v2 : une source de plus est une insertion.

        015 reste la dernière migration. Si une 016 était apparue pour
        accueillir Wikipédia, le pari aurait échoué.
        """
        (derniere,) = connexion.execute(
            "SELECT max(version) FROM schema_migrations"
        ).fetchone()
        assert derniere == "015"

    def test_aucune_deduplication_entre_sources(self, connexion) -> None:
        """Un personnage Kitsu et son homologue Wikipédia restent deux lignes."""
        (communs,) = connexion.execute(
            "SELECT count(*) FROM ("
            "  SELECT lower(canonical_name) n FROM manga.characters "
            "  WHERE source = 'kitsu'"
            "  INTERSECT"
            "  SELECT lower(canonical_name) FROM manga.characters "
            "  WHERE source = 'wikipedia_fr') x"
        ).fetchone()
        assert communs > 0, "aucun nom commun : le jeu de test serait vide"
        (fusionnes,) = connexion.execute(
            "SELECT count(*) FROM manga.characters c1 "
            "JOIN manga.characters c2 ON lower(c1.canonical_name) = "
            "     lower(c2.canonical_name) AND c1.source <> c2.source "
            "WHERE c1.character_uid = c2.character_uid"
        ).fetchone()
        assert fusionnes == 0, "des personnages de deux sources ont fusionné"
