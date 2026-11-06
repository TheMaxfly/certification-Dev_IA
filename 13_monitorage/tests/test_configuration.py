"""La configuration : des valeurs par défaut, et aucun secret qui passe."""

from __future__ import annotations

from pathlib import Path

import pytest

from monitorage.configuration import ConfigurationInvalide, charger

SECRET = "motdepassefactice"
AVEC_MOT_DE_PASSE = f"postgresql://verif_lecture:{SECRET}@hote:5432/apimanga"
SANS_MOT_DE_PASSE = "postgresql://verif_lecture@hote:5432/apimanga"


def test_les_valeurs_par_defaut():
    c = charger({})
    assert (c.hote, c.port) == ("127.0.0.1", 9108)
    assert (c.periode_s, c.delai_initial_s, c.periode_base_s) == (900.0, 60.0, 300.0)
    assert (c.encodage, c.hote_service) == (3, "embedding")
    assert c.lecture_base is False
    assert c.dsn is None
    assert c.journal is None
    assert c.echantillon is None


def test_les_valeurs_lues():
    c = charger(
        {
            "SONDE_ECOUTE": "0.0.0.0:9200",
            "SONDE_PERIODE_S": "60",
            "SONDE_ENCODAGE": "2",
            "SONDE_JOURNAL": "/tmp/j",
            "SONDE_ECHANTILLON": "/tmp/e",
            "MONITORAGE_LECTURE_BASE": "1",
            "MONITORAGE_DSN_LECTURE": SANS_MOT_DE_PASSE,
        }
    )
    assert (c.hote, c.port, c.periode_s, c.encodage) == ("0.0.0.0", 9200, 60.0, 2)
    assert (c.journal, c.echantillon) == (Path("/tmp/j"), Path("/tmp/e"))
    assert c.lecture_base is True
    assert c.dsn == SANS_MOT_DE_PASSE


def test_un_dsn_avec_mot_de_passe_refuse_de_demarrer_sans_le_montrer():
    with pytest.raises(ConfigurationInvalide) as exc:
        charger({"MONITORAGE_DSN_LECTURE": AVEC_MOT_DE_PASSE})
    assert SECRET not in str(exc.value)
    assert "verif_lecture" not in str(exc.value)


def test_un_dsn_illisible_refuse_de_demarrer_sans_le_montrer():
    with pytest.raises(ConfigurationInvalide) as exc:
        charger({"MONITORAGE_DSN_LECTURE": f"pas-un-dsn {SECRET}"})
    assert SECRET not in str(exc.value)
    assert exc.value.__cause__ is None


def test_la_lecture_de_la_base_demande_un_dsn():
    with pytest.raises(ConfigurationInvalide, match="MONITORAGE_DSN_LECTURE"):
        charger({"MONITORAGE_LECTURE_BASE": "1"})


def test_le_repr_ne_montre_pas_le_dsn():
    c = charger({"MONITORAGE_DSN_LECTURE": SANS_MOT_DE_PASSE})
    assert "verif_lecture" not in repr(c)


@pytest.mark.parametrize(
    ("variable", "valeur"),
    [
        ("SONDE_ECOUTE", "sans-port"),
        ("SONDE_ECOUTE", "hote:abc"),
        ("SONDE_ENCODAGE", "trois"),
        ("SONDE_PERIODE_S", "souvent"),
        ("SONDE_PERIODE_S", "-1"),
        ("MONITORAGE_LECTURE_BASE", "oui"),
    ],
)
def test_une_valeur_fausse_est_refusee(variable: str, valeur: str):
    with pytest.raises(ConfigurationInvalide, match=variable):
        charger({variable: valeur})


def test_une_periode_vide_prend_le_defaut():
    assert charger({"SONDE_PERIODE_S": ""}).periode_s == 900.0
