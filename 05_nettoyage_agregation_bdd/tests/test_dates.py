"""Tests de `identity.dates.parser_date_fr`.

Ce parseur décide de `volume_publication_date` et `review_date_iso`, donc de
tout tri chronologique en aval. Les cas qui comptent ne sont pas les dates
bien formées : ce sont les valeurs que la source produit et qui n'en sont pas.
"""

from datetime import date

from identity.dates import iso_ou_none, parser_date_fr


class TestFormesReelles:
    """Les formes effectivement rencontrées dans le snapshot 2026-07."""

    def test_date_complete_avec_prefixe_de_jour(self):
        assert parser_date_fr("mar. 27 nov. 2012") == date(2012, 11, 27)
        assert parser_date_fr("jeu. 10 oct. 2024") == date(2024, 10, 10)
        assert parser_date_fr("sam. 28 févr. 2009") == date(2009, 2, 28)

    def test_mois_complets_et_abreges(self):
        assert parser_date_fr("10 mai 2023") == date(2023, 5, 10)
        assert parser_date_fr("24 janv. 2024") == date(2024, 1, 24)
        assert parser_date_fr("1 décembre 2020") == date(2020, 12, 1)
        assert parser_date_fr("15 août 2018") == date(2018, 8, 15)

    def test_accents_optionnels(self):
        """Le scraper n'est pas constant sur les accents."""
        assert parser_date_fr("15 aout 2018") == date(2018, 8, 15)
        assert parser_date_fr("1 decembre 2020") == date(2020, 12, 1)

    def test_premier_du_mois(self):
        assert parser_date_fr("1er avril 2015") == date(2015, 4, 1)

    def test_forme_numerique(self):
        assert parser_date_fr("27/11/2012") == date(2012, 11, 27)
        assert parser_date_fr("27-11-2012") == date(2012, 11, 27)
        assert parser_date_fr("27/11/12") == date(2012, 11, 27)


class TestCeQuiNEstPasUneDate:
    """Le cœur du parseur : ne jamais inventer une date."""

    def test_date_tronquee_au_jour_de_semaine(self):
        """29,65 % des review_date du snapshot : la source n'a que le jour."""
        for tronquee in ("jeu.", "dim.", "lun.", "mer."):
            assert parser_date_fr(tronquee) is None

    def test_sentinelles_du_champ_volume(self):
        """« Date inconnue » (1 875) et « A paraître » (230) : pas des dates."""
        assert parser_date_fr("Date inconnue") is None
        assert parser_date_fr("A paraître") is None

    def test_vide_et_none(self):
        assert parser_date_fr(None) is None
        assert parser_date_fr("") is None
        assert parser_date_fr("   ") is None

    def test_date_impossible_refusee(self):
        """« 31 nov. » n'existe pas : une donnée fausse, pas une date."""
        assert parser_date_fr("31 nov. 2012") is None
        assert parser_date_fr("30 févr. 2020") is None

    def test_mois_inconnu_refuse(self):
        assert parser_date_fr("10 brumaire 1799") is None

    def test_pas_de_repli_approximatif(self):
        """Une année seule ne doit pas devenir le 1er janvier."""
        assert parser_date_fr("2012") is None
        assert parser_date_fr("nov. 2012") is None


class TestIso:
    def test_forme_iso(self):
        assert iso_ou_none("mar. 27 nov. 2012") == "2012-11-27"

    def test_iso_none_si_non_parsable(self):
        assert iso_ou_none("jeu.") is None
        assert iso_ou_none("Date inconnue") is None
