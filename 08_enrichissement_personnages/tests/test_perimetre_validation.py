"""Controle de rattachement du perimetre, sans base.

`_valider` est le verrou d'entree : toute serie sans cle source ou sans rang de
popularite arrete la derivation. Chaque branche de refus est provoquee ici sur
des lignes fabriquees ; la requete elle-meme est eprouvee contre une base jetable
dans `test_perimetre_integration.py`.
"""

import pytest

from commun.perimetre import (
    DERIVATIONS,
    PerimetreInexploitable,
    Serie,
    _valider,
    deriver,
)

WIKI = DERIVATIONS["wikipedia_fr"]
ANILIST = DERIVATIONS["anilist"]


def test_cas_nominal_rend_les_series_dans_l_ordre_recu() -> None:
    """La cle est debarrassee de ses blancs, les nombres sont des entiers, et
    l'ordre est celui de la requete : `_valider` ne retrie pas."""
    lignes = [(2, " Bleach ", 1), (1, "Naruto", 3), ("7", 104, "5")]
    assert _valider(lignes, WIKI) == [
        Serie(series_id=2, cle_source="Bleach", rang_popularite=1),
        Serie(series_id=1, cle_source="Naruto", rang_popularite=3),
        Serie(series_id=7, cle_source="104", rang_popularite=5),
    ]


def test_un_perimetre_vide_n_est_pas_une_erreur() -> None:
    assert _valider([], ANILIST) == []


def test_une_ligne_sans_identifiant_catalogue_arrete_tout() -> None:
    lignes = [(1, "Naruto", 3), (None, "Bleach", 1)]
    with pytest.raises(PerimetreInexploitable, match="sans identifiant catalogue"):
        _valider(lignes, WIKI)


@pytest.mark.parametrize("cle", [None, "", "   "])
def test_une_serie_sans_cle_source_arrete_la_derivation(cle) -> None:
    """Une cle absente ou blanche n'est pas une cle : pas de trou silencieux."""
    with pytest.raises(PerimetreInexploitable) as refus:
        _valider([(1, "Naruto", 3), (9, cle, 4)], WIKI)
    message = str(refus.value)
    assert "1 series sans cle source" in message
    assert WIKI.colonne_source in message, "le message nomme la colonne fautive"
    assert "[9]" in message


def test_le_refus_cite_au_plus_cinq_exemples() -> None:
    lignes = [(i, None, i) for i in range(1, 9)]
    with pytest.raises(PerimetreInexploitable) as refus:
        _valider(lignes, ANILIST)
    message = str(refus.value)
    assert message.startswith("anilist : 8 series sans cle source")
    assert message.endswith("ex. [1, 2, 3, 4, 5]")


def test_une_serie_sans_rang_rendrait_le_parcours_non_rejouable() -> None:
    with pytest.raises(PerimetreInexploitable, match="sans rang de popularite") as r:
        _valider([(1, "Naruto", 3), (5, "One Piece", None)], WIKI)
    assert "1 series" in str(r.value) and "[5]" in str(r.value)


def test_l_absence_de_cle_est_signalee_avant_l_absence_de_rang() -> None:
    """Les deux defauts sont comptes sur tout le perimetre ; la cle, condition
    d'entree, est rapportee la premiere."""
    with pytest.raises(PerimetreInexploitable, match="sans cle source"):
        _valider([(1, "Naruto", None), (2, None, 2)], WIKI)


def test_une_source_inconnue_est_refusee_sans_connexion() -> None:
    """Le DSN n'est jamais lu : le refus precede toute ouverture de connexion."""
    with pytest.raises(KeyError, match="source inconnue"):
        deriver("postgresql://inexistant.invalid/nulle_part", "mangadex")
