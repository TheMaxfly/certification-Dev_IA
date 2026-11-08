"""La configuration d'E4, lot 1 : l'environnement, la base, la clé de session, la garde
d'environnement (décision 10) et la table `[comptes]` (spec 02, § 4)."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import ENVIRONNEMENT, HACHEUR_RAPIDE
from fastapi.testclient import TestClient

from application.app import creer_application
from application.config import ConfigurationInvalide, charger

CLE = "s" * 40
MIGRATION_027 = (
    Path(__file__).resolve().parents[2]
    / "database"
    / "migrations"
    / "027_schema_application.sql"
)


def environ(**variables: str) -> dict[str, str]:
    return {**ENVIRONNEMENT, **variables}


def ecarts(**variables: str) -> list[str]:
    with pytest.raises(ConfigurationInvalide) as refus:
        charger(environ(**variables))
    return refus.value.ecarts


def test_par_defaut_le_developpement_sans_base_avec_une_cle_ephemere():
    reglages = charger(environ())

    assert reglages.environnement == "developpement"
    assert reglages.base_dsn is None
    assert reglages.cle_session_ephemere
    assert len(reglages.cle_session.encode()) >= 32
    assert reglages.cle_session != charger(environ()).cle_session, "tirée à chaque fois"


def test_un_environnement_inconnu_empeche_de_demarrer():
    assert ecarts(APPLICATION_ENVIRONNEMENT="essai") == [
        "APPLICATION_ENVIRONNEMENT : attendu developpement, preproduction, production"
    ]


@pytest.mark.parametrize("environnement", ["preproduction", "production"])
def test_hors_developpement_la_cle_de_session_est_obligatoire(environnement):
    base = {"preproduction": "apimanga_preprod", "production": "apimanga"}[
        environnement
    ]
    assert ecarts(
        APPLICATION_ENVIRONNEMENT=environnement,
        APPLICATION_BASE_DSN=f"postgresql://app_conseiller@localhost:5432/{base}",
    ) == [f"APPLICATION_CLE_SESSION : absente — obligatoire en {environnement}"]


def test_une_cle_de_session_trop_courte_est_refusee_sans_etre_montree():
    courte = "c" * 31
    refus = ecarts(APPLICATION_CLE_SESSION=courte)
    assert refus == ["APPLICATION_CLE_SESSION : trop courte (32 octets au moins)"]
    assert courte not in " ".join(refus)


def test_une_cle_de_session_fournie_n_est_pas_ephemere_ni_montree():
    reglages = charger(environ(APPLICATION_CLE_SESSION=CLE))

    assert reglages.cle_session == CLE and not reglages.cle_session_ephemere
    assert CLE not in repr(reglages)


@pytest.mark.parametrize(
    ("environnement", "base"),
    [
        ("preproduction", "apimanga_preprod"),
        ("production", "apimanga"),
        ("developpement", "apimanga_preprod"),
        ("developpement", "t_base_jetable"),
    ],
)
def test_la_garde_admet_la_base_de_son_environnement(environnement, base):
    dsn = f"postgresql://app_conseiller@localhost:5432/{base}"
    reglages = charger(
        environ(
            APPLICATION_ENVIRONNEMENT=environnement,
            APPLICATION_BASE_DSN=dsn,
            APPLICATION_CLE_SESSION=CLE,
        )
    )
    assert reglages.base_dsn == dsn
    assert dsn not in repr(reglages)


@pytest.mark.parametrize(
    ("environnement", "base", "attendu"),
    [
        (
            "preproduction",
            "apimanga",
            "garde d'environnement : preproduction exige la base apimanga_preprod",
        ),
        (
            "production",
            "apimanga_preprod",
            "garde d'environnement : production exige la base apimanga",
        ),
        (
            "developpement",
            "apimanga",
            "garde d'environnement : la base de production est refusée"
            " en développement",
        ),
    ],
)
def test_la_garde_refuse_une_autre_base_sans_montrer_le_dsn(
    environnement, base, attendu
):
    dsn = f"host=localhost port=5432 dbname={base} user=app_conseiller"
    refus = ecarts(
        APPLICATION_ENVIRONNEMENT=environnement,
        APPLICATION_BASE_DSN=dsn,
        APPLICATION_CLE_SESSION=CLE,
    )
    assert refus == [attendu]
    assert "app_conseiller" not in " ".join(refus)


def test_un_dsn_qui_porte_un_mot_de_passe_est_refuse_sans_le_montrer():
    secret = "secret-factice-du-dsn"
    refus = ecarts(
        APPLICATION_BASE_DSN="host=localhost dbname=apimanga_preprod user=x"
        f" password={secret}"
    )
    assert len(refus) == 1 and refus[0].startswith(
        "APPLICATION_BASE_DSN : porte un mot de passe"
    )
    assert secret not in refus[0]


@pytest.mark.parametrize(
    ("dsn", "attendu"),
    [
        ("host=localhost user=x", "APPLICATION_BASE_DSN : la base n'est pas nommée"),
        ("host=localhost = = =", "APPLICATION_BASE_DSN : illisible"),
    ],
)
def test_un_dsn_sans_base_ou_illisible_est_refuse(dsn, attendu):
    assert ecarts(APPLICATION_BASE_DSN=dsn) == [attendu]


def test_les_bornes_des_comptes_sont_celles_de_la_migration_027():
    comptes = charger(environ()).comptes
    sql = MIGRATION_027.read_text(encoding="utf-8")

    motif = re.search(r"pseudo ~ '\^\[a-z0-9_-\]\{(\d+),(\d+)\}\$'", sql)
    nom = re.search(r"char_length\(nom_affiche\) BETWEEN 1 AND (\d+)", sql)
    assert motif is not None and nom is not None
    assert (comptes.pseudo_min, comptes.pseudo_max) == (
        int(motif.group(1)),
        int(motif.group(2)),
    )
    assert comptes.nom_affiche_max == int(nom.group(1))
    assert (comptes.mot_de_passe_min, comptes.mot_de_passe_max) == (12, 128)
    assert (comptes.echecs_avant_verrou, comptes.duree_verrou_s) == (5, 900)


def test_le_demarrage_dit_la_cle_ephemere_par_un_avertissement(caplog, port_ferme):
    reglages = replace(charger(environ()), adresse_api=port_ferme)
    application = creer_application(reglages, hacheur=HACHEUR_RAPIDE)
    with caplog.at_level(logging.INFO, logger="application"), TestClient(application):
        pass

    avertissements = [
        r for r in caplog.records if r.getMessage() == "cle_session_ephemere"
    ]
    assert len(avertissements) == 1 and avertissements[0].levelno == logging.WARNING


def test_une_cle_fournie_ne_donne_aucun_avertissement(caplog, port_ferme):
    reglages = replace(
        charger(environ(APPLICATION_CLE_SESSION=CLE)), adresse_api=port_ferme
    )
    application = creer_application(reglages, hacheur=HACHEUR_RAPIDE)
    with caplog.at_level(logging.INFO, logger="application"), TestClient(application):
        pass

    assert "cle_session_ephemere" not in caplog.messages
