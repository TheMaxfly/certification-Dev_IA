"""Tests de `identity.ean` : la clé de contrôle EAN-13, sans réparation.

La fonction décide de `isbn13_valide` et de ce que `isbn13_ou_none` laisse entrer
dans `manga.volume_identity`. Ce qui compte : qu'un EAN juste passe quelle que
soit sa clé (0 compris), qu'un EAN faux ou mal formé soit refusé, et que rien ne
soit jamais « corrigé » en silence — pas de complément d'un EAN à 12 chiffres, pas
de conversion d'un ISBN-10, pas de tirets retirés.
"""

import pytest

from identity.ean import ean13_valide, isbn13_ou_none

#: EAN-13 justes, de provenances variées : un volume du snapshot, l'exemple
#: d'ISBN-13 de la norme, un EAN hors livre, un UPC-A complété d'un zéro de tête,
#: et une clé de contrôle égale à 0 (le cas `(10 - total % 10) % 10`).
VALIDES = [
    "9782355929489",
    "9780306406157",
    "4006381333931",
    "0012345678905",
    "9782749300030",
]


class TestCleDeControle:
    @pytest.mark.parametrize("ean", VALIDES)
    def test_un_ean_juste_est_valide(self, ean):
        assert ean13_valide(ean)

    @pytest.mark.parametrize("ean", VALIDES)
    def test_une_seule_cle_est_juste_parmi_les_dix(self, ean):
        """Changer le 13e chiffre, c'est toujours fausser la clé."""
        justes = [c for c in "0123456789" if ean13_valide(ean[:12] + c)]
        assert justes == [ean[12]]

    def test_les_poids_alternent_un_et_trois(self):
        """Une transposition de deux chiffres voisins (écart ≠ 5) est détectée :
        c'est l'alternance des poids qui le permet, une somme simple ne le
        verrait pas."""
        assert ean13_valide("9782355929489")
        assert not ean13_valide("9783255929489"), "« 23 » devenu « 32 »"

    def test_le_premier_chiffre_pese_un(self):
        """Le chiffre de tête est de poids 1 : un zéro de tête ne change rien à la
        somme, d'où la validité de l'UPC-A complété."""
        assert ean13_valide("0012345678905")
        assert not ean13_valide("1012345678905")


class TestFormesRefusees:
    @pytest.mark.parametrize(
        "valeur",
        [
            None,
            "",
            "   ",
            "978235592948",  # 12 chiffres : jamais complété
            "97823559294890",  # 14 chiffres
            "978-2-35592-948-9",  # tirets : jamais retirés
            "978 2355929489",  # espace interne
            "978235592948X",  # clé « X » d'un ISBN-10
            "2355929489",  # ISBN-10 : jamais converti
            "-978235592948",
            "9782355929488",  # clé fausse
        ],
    )
    def test_refuse_sans_reparer(self, valeur):
        assert not ean13_valide(valeur)
        assert isbn13_ou_none(valeur) is None


class TestNormalisation:
    def test_les_blancs_de_bord_sont_retires(self):
        """Un EAN lu d'un CSV peut traîner un blanc ou un saut de ligne."""
        assert ean13_valide(" 9782355929489\n")
        assert isbn13_ou_none("\t9782355929489 ") == "9782355929489"

    def test_le_zero_de_tete_est_conserve(self):
        """Une chaîne, jamais un entier : le CHECK `^[0-9]{13}$` le demande."""
        assert isbn13_ou_none("0012345678905") == "0012345678905"

    def test_un_ean_valide_est_rendu_tel_quel(self):
        assert isbn13_ou_none("9782355929489") == "9782355929489"
