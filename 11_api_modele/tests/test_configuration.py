"""La configuration : un seul lecteur, tout validé, un écart et l'API ne démarre pas."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import pytest
from conftest import CLE_APPLICATION, CLE_DEMONSTRATION, RACINE_MODULE

from api_modele.configuration import FICHIER_REGLAGES, ConfigurationInvalide, charger

DSN = "postgresql://modele_api@localhost:5432/apimanga"
VALIDE = {
    "API_MODELE_CLES": (
        f"application:{CLE_APPLICATION},demonstration:{CLE_DEMONSTRATION}"
    ),
    "API_MODELE_DSN": DSN,
}


def ecarts(environ: dict[str, str], fichier: Path = FICHIER_REGLAGES) -> list[str]:
    with pytest.raises(ConfigurationInvalide) as exc:
        charger(environ, fichier)
    return exc.value.ecarts


def test_le_fichier_versionne_donne_les_valeurs_du_contrat():
    r = charger(VALIDE)

    assert [c.nom for c in r.cles] == ["application", "demonstration"]
    assert (r.nombre_series.defaut, r.nombre_series.min, r.nombre_series.max) == (
        10,
        1,
        50,
    )
    assert (
        r.passages_par_serie.defaut,
        r.passages_par_serie.min,
        r.passages_par_serie.max,
    ) == (1, 1, 5)
    assert r.catalogue_seul is True
    assert r.longueur_question_max == 500
    assert r.corps_octets_max == 16384
    assert r.debit_par_minute == 60
    assert r.version == "v1"
    assert r.instance == "embeddinggemma"
    assert r.client_hote is None
    assert (r.hote, r.port) == ("127.0.0.1", 8010)
    assert r.journal_question is False
    assert r.documentation is False
    assert ipaddress.ip_address("127.0.0.1") in r.metriques_reseaux[0]
    assert r.pool_max <= 10, "sous la borne du rôle modele_api (CONNECTION LIMIT 10)"


def test_chaque_table_du_fichier_porte_son_motif():
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    tables = re.findall(r"^\[([a-z_.]+)\]$", texte, flags=re.MULTILINE)

    assert len(tables) >= 12
    blocs = re.split(r"^\[[a-z_.]+\]$", texte, flags=re.MULTILINE)[1:]
    assert all(re.search(r'^motif = ".+"$', b, flags=re.MULTILINE) for b in blocs)


def test_les_secrets_ne_s_affichent_pas():
    r = charger(VALIDE)

    assert CLE_APPLICATION not in repr(r)
    assert DSN not in repr(r)


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        (None, "absente ou vide"),
        ("", "absente ou vide"),
        ("application", "sans « nom:secret »"),
        (f"Application:{CLE_APPLICATION}", "nom invalide"),
        ("application:court", "moins de 32 caractères"),
        (f"application:{CLE_APPLICATION},application:{CLE_DEMONSTRATION}", "en double"),
        (
            f"application:{CLE_APPLICATION},demonstration:{CLE_APPLICATION}",
            "même secret",
        ),
    ],
)
def test_le_trousseau_est_a_defaillance_fermee(valeur, attendu):
    environ = dict(VALIDE)
    if valeur is None:
        del environ["API_MODELE_CLES"]
    else:
        environ["API_MODELE_CLES"] = valeur

    assert any(attendu in e for e in ecarts(environ))


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        (None, "absente"),
        ("postgresql://modele_api:secret@localhost/apimanga", "~/.pgpass"),
        ("host=localhost port", "illisible"),
    ],
)
def test_le_dsn_est_exige_et_sans_mot_de_passe(valeur, attendu):
    environ = dict(VALIDE)
    if valeur is None:
        del environ["API_MODELE_DSN"]
    else:
        environ["API_MODELE_DSN"] = valeur

    assert any(attendu in e for e in ecarts(environ))


@pytest.mark.parametrize(
    ("nom", "valeur", "attendu"),
    [
        ("API_MODELE_VERSION", "v2", "n'est pas servie"),
        ("API_MODELE_DEBIT_PAR_MINUTE", "soixante", "entier attendu"),
        ("API_MODELE_DEBIT_PAR_MINUTE", "0", "entre 1"),
        ("API_MODELE_METRIQUES_RESEAUX", "10.0.0.0/33", "réseau illisible"),
        ("API_MODELE_JOURNAL_QUESTION", "peut-être", "vrai ou faux"),
        ("API_MODELE_DOCUMENTATION", "oui-non", "vrai ou faux"),
        ("CLIENT_HOTE", "hote;rm", "hôte invalide"),
        ("API_MODELE_PORT", "70000", "entre 1 et 65535"),
    ],
)
def test_une_surcharge_fautive_est_refusee(nom, valeur, attendu):
    assert any(attendu in e for e in ecarts(VALIDE | {nom: valeur}))


def test_les_surcharges_d_exploitation_sont_lues():
    r = charger(
        VALIDE
        | {
            "API_MODELE_DEBIT_PAR_MINUTE": "120",
            "API_MODELE_METRIQUES_RESEAUX": "10.0.0.0/8, 127.0.0.1/32",
            "API_MODELE_JOURNAL_QUESTION": "vrai",
            "API_MODELE_DOCUMENTATION": "vrai",
            "CLIENT_HOTE": "tei.interne",
            "API_MODELE_HOTE": "0.0.0.0",
            "API_MODELE_PORT": "9010",
        }
    )

    assert r.debit_par_minute == 120
    assert len(r.metriques_reseaux) == 2
    assert r.journal_question is True
    assert r.documentation is True
    assert r.client_hote == "tei.interne"
    assert (r.hote, r.port) == ("0.0.0.0", 9010)


def test_tous_les_ecarts_sont_nommes_ensemble():
    trouves = ecarts({"API_MODELE_VERSION": "v9"})

    assert len(trouves) == 3  # clés, DSN, version


def ecrire(tmp_path: Path, remplacement: tuple[str, str]) -> Path:
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    assert texte.count(remplacement[0]) == 1
    fichier = tmp_path / "reglages.toml"
    fichier.write_text(texte.replace(*remplacement), encoding="utf-8")
    return fichier


@pytest.mark.parametrize(
    ("remplacement", "attendu"),
    [
        (("defaut = 10\n", "defaut = 51\n"), "hors de [1, 50]"),
        (("defaut = 10\n", "defaut = true\n"), "entier ≥ 1"),
        (
            (
                "[question]\nlongueur_max = 500\nmotif",
                "[question]\nlongueur_max = 500\nnote",
            ),
            "sans motif",
        ),
        (("[corps]\n", "[corps_absent]\n"), "table [corps] absente"),
        (("min = 1\nmax = 5\nattente_s", "min = 6\nmax = 5\nattente_s"), "min > max"),
        (("service_s = 10.0", "service_s = 0"), "nombre > 0"),
        (("defaut = true\n", 'defaut = "oui"\n'), "vrai ou faux"),
        (('instance = "embeddinggemma"', 'instance = ""'), "texte non vide"),
        (('reseaux = ["127.0.0.1/32", "::1/128"]', "reseaux = []"), "aucun réseau"),
    ],
)
def test_un_fichier_de_reglages_fautif_est_refuse(tmp_path, remplacement, attendu):
    assert any(attendu in e for e in ecarts(VALIDE, ecrire(tmp_path, remplacement)))


def test_un_fichier_illisible_est_refuse(tmp_path):
    fichier = tmp_path / "reglages.toml"
    fichier.write_text("[recherche\n", encoding="utf-8")

    assert "illisible" in ecarts(VALIDE, fichier)[0]


def test_le_fichier_lu_est_celui_du_module():
    assert FICHIER_REGLAGES == RACINE_MODULE / "config" / "reglages.toml"
