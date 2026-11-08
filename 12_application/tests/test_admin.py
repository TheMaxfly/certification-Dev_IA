"""La commande du premier administrateur (E4, lot 1, spec 03, § 4), par entrée simulée :
la garde d'environnement, les règles de l'inscription, le mot de passe deux fois et
sans écho (`getpass`), jamais affiché ; le compte `admin` sans mot de passe à changer,
sa ligne de journal sans auteur."""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import warnings
from pathlib import Path

import psycopg
import pytest
from conftest import HACHEUR_RAPIDE, RACINE_DEPOT

from application import admin

MDP = "mot de passe factice de l'admin"


class Clavier:
    """Les réponses aux invites, dans l'ordre ; ce que la commande écrit, gardé."""

    def __init__(self, lignes: list[str], secrets: list[str]) -> None:
        self.lignes, self.secrets, self.sortie = list(lignes), list(secrets), []
        self.invites_secretes: list[str] = []

    def lire(self, invite: str) -> str:
        return self.lignes.pop(0)

    def lire_secret(self, invite: str) -> str:
        self.invites_secretes.append(invite)
        return self.secrets.pop(0)

    def ecrire(self, texte: str) -> None:
        self.sortie.append(texte)


def lancer(environ: dict[str, str], clavier: Clavier) -> int:
    return admin.creer_admin(
        environ, clavier.lire, clavier.lire_secret, clavier.ecrire, HACHEUR_RAPIDE
    )


@pytest.fixture
def environ(base_app) -> dict[str, str]:
    return {"APPLICATION_BASE_DSN": base_app}


def test_le_premier_admin_est_cree_sans_mot_de_passe_a_changer(environ, base):
    clavier = Clavier(["Admin_Essai ", "Administrateur"], [MDP, MDP])

    assert lancer(environ, clavier) == 0
    with psycopg.connect(base) as cx:
        compte = cx.execute(
            "SELECT pseudo, nom_affiche, role, doit_changer_mdp, hachage"
            " FROM application.comptes"
        ).fetchone()
        journal = cx.execute(
            "SELECT par_compte_id, action FROM application.journal_actions"
        ).fetchone()
    assert compte[:4] == ("admin_essai", "Administrateur", "admin", False)
    assert compte[4].startswith("$argon2id$")
    assert journal == (None, "compte_cree")
    assert len(clavier.invites_secretes) == 2, "le mot de passe est demandé deux fois"
    assert MDP not in " ".join(clavier.sortie)
    assert "Compte administrateur créé" in clavier.sortie[-1]


@pytest.mark.parametrize(
    ("lignes", "secrets", "attendu"),
    [
        (["ab", "Nom"], [MDP, MDP], "Pseudo refusé"),
        (["admin_essai", ""], [MDP, MDP], "Nom affiché refusé"),
        (["admin_essai", "Nom\x00nul"], [MDP, MDP], "Nom affiché refusé"),
        (["admin_essai", "Nom"], ["court", "court"], "Mot de passe refusé"),
        (["admin_essai", "Nom"], [MDP, MDP + "x"], "ne correspondent pas"),
    ],
    ids=["pseudo", "nom", "nom à octet nul", "mot de passe court", "confirmation"],
)
def test_une_regle_non_tenue_ne_cree_rien(environ, base, lignes, secrets, attendu):
    clavier = Clavier(lignes, secrets)

    assert lancer(environ, clavier) == 1
    assert attendu in clavier.sortie[-1]
    with psycopg.connect(base) as cx:
        assert cx.execute("SELECT count(*) FROM application.comptes").fetchone() == (0,)


def test_un_pseudo_pris_ne_cree_rien(environ):
    assert lancer(environ, Clavier(["admin_essai", "Nom"], [MDP, MDP])) == 0
    clavier = Clavier(["admin_essai", "Autre"], [MDP, MDP])

    assert lancer(environ, clavier) == 1
    assert "déjà pris" in clavier.sortie[-1]


def test_une_base_injoignable_ne_cree_rien(port_ferme):
    dsn = f"postgresql://x@127.0.0.1:{port_ferme.rsplit(':', 1)[1]}/apimanga_essai"
    clavier = Clavier(["admin_essai", "Nom"], [MDP, MDP])

    assert lancer({"APPLICATION_BASE_DSN": dsn}, clavier) == 1
    assert "La base ne répond pas" in clavier.sortie[-1]


def test_une_contrainte_de_la_base_ne_cree_rien(environ, monkeypatch):
    async def contrainte(*_args: object, **_kwargs: object) -> None:
        raise admin.CompteInvalide

    monkeypatch.setattr(admin.comptes, "creer", contrainte)
    clavier = Clavier(["admin_essai", "Nom"], [MDP, MDP])

    assert lancer(environ, clavier) == 1
    assert "une contrainte" in clavier.sortie[-1]


def test_une_ecriture_refusee_par_la_base_se_nomme_sans_son_message(environ, base):
    with psycopg.connect(base, autocommit=True) as cx:
        cx.execute("REVOKE INSERT ON application.comptes FROM app_rw")
    clavier = Clavier(["admin_essai", "Nom"], [MDP, MDP])

    assert lancer(environ, clavier) == 1
    assert clavier.sortie[-1] == (
        "La base refuse l'écriture (InsufficientPrivilege) : rien n'est créé."
    )


@pytest.mark.parametrize(
    ("environ", "attendu"),
    [
        ({}, "APPLICATION_BASE_DSN : absente"),
        (
            {"APPLICATION_BASE_DSN": "postgresql://app_conseiller@localhost/apimanga"},
            "la base de production est refusée en développement",
        ),
        (
            {
                "APPLICATION_ENVIRONNEMENT": "preproduction",
                "APPLICATION_BASE_DSN": "postgresql://app_conseiller@localhost/apimanga",
            },
            "preproduction exige la base apimanga_preprod",
        ),
    ],
    ids=["sans DSN", "production en développement", "préproduction sur la production"],
)
def test_la_garde_d_environnement_arrete_la_commande(environ, attendu):
    clavier = Clavier([], [])

    assert lancer(environ, clavier) == 2
    assert attendu in clavier.sortie[0]
    assert clavier.invites_secretes == [], "rien n'a été demandé"


class Terminal:
    """Une entrée standard qui se dit terminal."""

    def isatty(self) -> bool:
        return True


def test_la_ligne_de_commande_lit_le_clavier_sans_echo(monkeypatch, base_app, base):
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setenv("APPLICATION_BASE_DSN", base_app)
    monkeypatch.delenv("APPLICATION_ENVIRONNEMENT", raising=False)
    lignes = iter(["admin_essai", "Administrateur"])
    secrets = iter([MDP, MDP])
    monkeypatch.setattr("builtins.input", lambda invite="": next(lignes))
    monkeypatch.setattr(getpass, "getpass", lambda invite="": next(secrets))

    assert admin.principal(["creer-admin"]) == 0
    with psycopg.connect(base) as cx:
        assert cx.execute("SELECT role FROM application.comptes").fetchone() == (
            "admin",
        )


def test_la_ligne_de_commande_exige_sa_sous_commande():
    with pytest.raises(SystemExit) as fin:
        admin.principal([])
    assert fin.value.code == 2


def test_sans_terminal_la_commande_refuse_avant_de_rien_demander(monkeypatch, capsys):
    demande: list[str] = []
    monkeypatch.setattr("builtins.input", lambda invite="": demande.append(invite))
    # L'entrée standard de pytest n'est pas un terminal.

    assert admin.principal(["creer-admin"]) == 2
    assert demande == []
    assert "exige un terminal" in capsys.readouterr().err


def test_un_terminal_qui_ne_coupe_pas_l_echo_ne_cree_rien(monkeypatch, base_app, base):
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setenv("APPLICATION_BASE_DSN", base_app)
    monkeypatch.delenv("APPLICATION_ENVIRONNEMENT", raising=False)
    lignes = iter(["admin_essai", "Administrateur"])
    monkeypatch.setattr("builtins.input", lambda invite="": next(lignes))

    def repli_en_echo(invite: str = "") -> str:
        # Ce que fait `getpass` sans terminal de contrôle : un avertissement, puis
        # une lecture en écho.
        warnings.warn("écho", getpass.GetPassWarning, stacklevel=2)
        return MDP  # pragma: no cover — l'avertissement est devenu une erreur

    monkeypatch.setattr(getpass, "getpass", repli_en_echo)

    assert admin.principal(["creer-admin"]) == 2
    with psycopg.connect(base) as cx:
        assert cx.execute("SELECT count(*) FROM application.comptes").fetchone() == (0,)


def test_la_cible_make_lance_la_commande_et_le_module_s_execute(tmp_path: Path):
    recette = subprocess.run(  # noqa: S603
        ["make", "-n", "app-admin-creer"],  # noqa: S607
        cwd=RACINE_DEPOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "cd 12_application" in recette
    assert "python -m application.admin creer-admin" in recette
    # Le module lui-même, comme la cible le lance — sans terminal : il refuse.
    environ = {n: v for n, v in os.environ.items() if not n.startswith("APPLICATION_")}
    fini = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "application.admin", "creer-admin"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=environ,
        cwd=tmp_path,
        check=False,
    )
    assert fini.returncode == 2
    assert "exige un terminal" in fini.stderr
