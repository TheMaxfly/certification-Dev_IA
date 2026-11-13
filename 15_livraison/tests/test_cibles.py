"""Les deux cibles : lues, contrôlées, sans secret ; la commande Compose composée."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import MODULE
from outils_essai import DEPOT, relecture_reelle

from livraison import cibles
from livraison.codes import Arret, Code
from livraison.outils import Outils

MOTIF_SECRET = re.compile(r"CLE|KEY|PASSWORD|MOT_DE_PASSE|TOKEN|SECRET|SESSION|PGPASS")


def ecrire(dossier: Path, nom: str, texte: str) -> Path:
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / f"{nom}.env").write_text(texte, encoding="utf-8")
    return dossier


VALIDE = (MODULE / "cibles" / "staging.env").read_text(encoding="utf-8")


def test_les_deux_cibles_se_lisent():
    s, p = cibles.lire("staging"), cibles.lire("production")
    assert (s.projet, s.base, s.environnement) == (
        "senpai-staging",
        "apimanga_preprod",
        "preproduction",
    )
    # 0.1.3 : le monitorage est levé dans les deux cibles.
    assert (s.port_application, s.port_catalogue, s.avec_monitorage) == (
        8021,
        8001,
        True,
    )
    assert s.compose == (cibles.COMPOSE_11, cibles.COMPOSE_12, cibles.COMPOSE_13)
    assert (p.projet, p.base, p.port_application, p.port_catalogue) == (
        "api-modele",
        "apimanga",
        8020,
        8000,
    )
    assert p.avec_monitorage and p.compose[-1] == cibles.COMPOSE_13
    assert (s.autre, p.autre) == ("production", "staging")


def test_le_staging_coupe_le_controle_des_modeles_au_demarrage():
    v = cibles.valeurs(VALIDE)
    assert v["API_MODELE_VERIFIER_MODELES"] == "false"


def test_la_sonde_du_staging_ne_lit_pas_la_base_ni_n_ecrit_le_journal_de_production():
    """La sonde du 13, en staging : aucune lecture de base, journal à /dev/null ; la
    production ne pose aucune de ces variables (les défauts du 13 valent)."""
    v = cibles.valeurs(VALIDE)
    assert v["MONITORAGE_LECTURE_BASE"] == "0"
    assert v["MONITORAGE_DSN_LECTURE"] == ""
    assert v["MONITORAGE_JOURNAL_SONDE"] == "/dev/null"
    production = (MODULE / "cibles" / "production.env").read_text(encoding="utf-8")
    assert not set(cibles.valeurs(production)) & (
        cibles.FACULTATIVES - {"API_MODELE_VERIFIER_MODELES"}
    )
    assert cibles.lire("production").avec_monitorage


def test_un_dsn_de_lecture_de_la_sonde_vise_la_base_de_la_cible(tmp_path):
    texte = VALIDE.replace(
        "MONITORAGE_DSN_LECTURE=\n",
        "MONITORAGE_DSN_LECTURE=postgresql://verif_lecture@host.docker.internal:5432/apimanga\n",
    )
    with pytest.raises(
        Arret, match="MONITORAGE_DSN_LECTURE doit viser apimanga_preprod"
    ):
        cibles.lire("staging", ecrire(tmp_path, "staging", texte))
    juste = texte.replace("/apimanga\n", "/apimanga_preprod\n")
    assert cibles.lire("staging", ecrire(tmp_path, "staging", juste))


@pytest.mark.parametrize(
    ("texte", "motif"),
    [
        (VALIDE + "API_KEYS=x\n", "non admises"),
        (VALIDE.replace("DB_NAME=apimanga_preprod\n", ""), "manquent DB_NAME"),
        (VALIDE.replace("DB_NAME=apimanga_preprod", "DB_NAME=apimanga"), "n'admet pas"),
        (
            VALIDE.replace("modele_api@", "modele_api:secret@"),
            "sans mot de passe",
        ),
        (
            VALIDE.replace(
                "/apimanga_preprod\nAPPLICATION_ENV", "/apimanga\nAPPLICATION_ENV"
            ),
            "doit viser",
        ),
        (VALIDE.replace("8021", "huit"), "port illisible"),
        (VALIDE + "ligne sans egal\n", "ligne illisible"),
    ],
)
def test_un_fichier_de_cible_fautif_est_refuse(tmp_path, texte, motif):
    with pytest.raises(Arret, match=motif) as e:
        cibles.lire("staging", ecrire(tmp_path, "staging", texte))
    assert e.value.code == Code.PREREQUIS


def test_une_cible_inconnue_ou_absente_est_refusee(tmp_path):
    with pytest.raises(Arret, match="staging ou production"):
        cibles.lire("serveur")
    with pytest.raises(Arret, match="absent"):
        cibles.lire("staging", tmp_path)


def noms_des_exemples() -> set[str]:
    noms: set[str] = set()
    for m in ("02_api_manga", "11_api_modele", "12_application", "13_monitorage"):
        texte = (DEPOT / m / ".env.example").read_text(encoding="utf-8")
        noms |= set(re.findall(r"^\s*#?\s*([A-Z][A-Z0-9_]+)=", texte, re.M))
    return noms


@pytest.mark.parametrize("nom", cibles.NOMS)
def test_aucune_variable_secrete_dans_les_fichiers_de_cible(nom):
    """Les noms des secrets sont lus dans les fichiers d'exemple des modules."""
    secrets = {n for n in noms_des_exemples() if MOTIF_SECRET.search(n)}
    assert secrets, "aucun nom de secret lu : les fichiers d'exemple ont changé ?"
    v = cibles.valeurs((MODULE / "cibles" / f"{nom}.env").read_text(encoding="utf-8"))
    assert not set(v) & secrets
    assert not [n for n in v if MOTIF_SECRET.search(n)]


@pytest.mark.parametrize("nom", cibles.NOMS)
def test_les_fichiers_de_cible_passent_les_motifs_du_hook(tmp_path, nom):
    texte = (MODULE / "cibles" / f"{nom}.env").read_text(encoding="utf-8")
    assert relecture_reelle(tmp_path)(texte) == []


def test_l_ordre_des_fichiers_d_environnement(o: Outils):
    s = cibles.lire("staging")
    (o.racine / "12_application/.env.staging").write_text("", encoding="utf-8")
    (o.racine / "13_monitorage").mkdir()
    (o.racine / "13_monitorage/.env").write_text("", encoding="utf-8")
    assert [
        f.relative_to(o.racine).as_posix()
        for f in cibles.fichiers_env(s, o.racine)[:-1]
    ] == [
        "11_api_modele/.env",
        "12_application/.env",
        "02_api_manga/.env",
        "13_monitorage/.env",
        "12_application/.env.staging",
    ]
    p = cibles.lire("production")
    fichiers = cibles.fichiers_env(p, o.racine)
    assert fichiers[3] == o.racine / "13_monitorage/.env"
    assert fichiers[-1] == p.fichier_env


def test_la_commande_compose_passe_le_fichier_de_cible_en_dernier(o: Outils):
    o.environ = {"PREPROD_PGPASS_CONTENEUR": "/ailleurs/pgpass"}
    c = cibles.commande_compose(cibles.lire("staging"), o, "up", "-d")
    a = list(c.argv)
    fichiers_env = [a[i + 1] for i, x in enumerate(a) if x == "--env-file"]
    assert fichiers_env[-1].endswith("cibles/staging.env")
    assert a[a.index("-p") + 1] == "senpai-staging"
    assert [a[i + 1] for i, x in enumerate(a) if x == "-f"] == [
        str(o.racine / cibles.COMPOSE_11),
        str(o.racine / cibles.COMPOSE_12),
        str(o.racine / cibles.COMPOSE_13),
    ]
    assert a[-2:] == ["up", "-d"]
    assert a.index("-p") > a.index("--env-file")
    assert c.env == {"API_MODELE_PGPASS": "/ailleurs/pgpass"}
    assert "--project-directory" not in a


def test_la_commande_compose_d_une_etiquette_garde_les_chemins_du_depot(
    o: Outils, tmp_path
):
    f = (tmp_path / "x" / "11_api_modele/docker-compose.yml",)
    c = cibles.commande_compose(
        cibles.lire("production"), o, "down", fichiers_compose=f
    )
    a = list(c.argv)
    assert a[a.index("--project-directory") + 1] == str(o.racine / "11_api_modele")
    assert a[a.index("-f") + 1] == str(f[0])
    assert c.env == {}
