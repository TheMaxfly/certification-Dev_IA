"""Tests de `identity.wikidata_dump.normaliser`.

Cette fonction est le composant central de la cascade de matching : c'est elle qui
produit la clé de jointure exacte inter-sources. Une régression ici propage des
faux positifs dans un étage qui décide en `auto` — d'où ce test en priorité.
"""

from identity.wikidata_dump import normaliser


class TestLatin:
    """Les diacritiques latins doivent être retirés (comportement voulu)."""

    def test_accents_retires(self):
        assert normaliser("Élégante") == "elegante"
        assert normaliser("Café") == "cafe"
        assert normaliser("Ōoku") == "ooku"

    def test_article_initial_retire(self):
        assert normaliser("L'Attaque des Titans") == "attaque des titans"
        assert normaliser("The Promised Neverland") == "promised neverland"

    def test_casse_ponctuation_espaces(self):
        assert normaliser("  DRAGON   Ball!!  ") == "dragon ball"


class TestJaponais:
    """Régression : les marques de sonorisation ne doivent PAS être retirées.

    NFKD décompose デ en テ + dakuten (U+3099). Filtrer tous les `combining()`
    supprimait le dakuten et transformait ドラゴンボール en トラコンホール.
    """

    def test_dakuten_preserve(self):
        assert normaliser("ドラゴンボール") == "ドラゴンボール"
        assert normaliser("デミアン症候群") == "デミアン症候群"
        assert normaliser("バガボンド") == "バガボンド"

    def test_handakuten_preserve(self):
        assert normaliser("パラダイス") == "パラダイス"

    def test_titres_distincts_ne_collisionnent_pas(self):
        # パ (pa), バ (ba), ハ (ha) : trois sons distincts -> trois clés distinctes.
        assert normaliser("パラダイス") != normaliser("ハラダイス")
        assert normaliser("バラ") != normaliser("ハラ")
        assert normaliser("ドラゴン") != normaliser("トラゴン")


class TestProprietes:
    """Propriétés attendues de toute clé de jointure."""

    def test_idempotence(self):
        # Normaliser une forme déjà normalisée ne doit rien changer :
        # sans quoi la clé dépendrait du nombre d'applications.
        titres = [
            "Élégante",
            "ドラゴンボール",
            "L'Attaque des Titans",
            "パラダイス",
        ]
        for titre in titres:
            once = normaliser(titre)
            assert normaliser(once) == once, titre

    def test_vide_et_ponctuation_seule(self):
        assert normaliser("") == ""
        assert normaliser("!!!") == ""
