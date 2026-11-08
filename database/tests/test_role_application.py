"""L'outil `outils/creer_role_application.sh` : le rôle de connexion `app_conseiller`.

Il crée `app_conseiller`, membre de `app_rw` (migration 027), sur `apimanga` ou
`apimanga_preprod` seulement ; le mot de passe est saisi au clavier, deux fois, sans
écho, par `\\password` de psql (aucun argument, aucune variable) ; un rôle existant est
refusé, jamais réinitialisé.

Le clavier est un pseudo-terminal (`pty`) : l'outil exige un terminal et psql lit le
mot de passe sur lui ; chaque saisie attend une invite NEUVE, écrite après la saisie
précédente. Les bases visées sont des bases du conteneur jetable qui portent le nom de
la production ou de la préproduction — le nom seul est gardé, et un conteneur de test
n'est pas le serveur du poste. L'outil refuse un DSN qui porte un mot de passe : celui
du conteneur passe par un `.pgpass` jetable, comme sur le poste. Un rôle est un objet
du serveur : il est supprimé après chaque test qui le crée.
"""

from __future__ import annotations

import os
import pty
import secrets
import select
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator

import psycopg
import pytest
from conftest import RACINE, harnais
from psycopg import conninfo, errors

SCRIPT = RACINE / "outils" / "creer_role_application.sh"
ROLE = "app_conseiller"


@pytest.fixture(autouse=True)
def pgpass_du_conteneur(tmp_path, monkeypatch) -> None:
    """Le mot de passe d'administration du conteneur, hors du DSN et des arguments."""
    chemin = tmp_path / "pgpass"
    chemin.write_text(f"*:*:*:postgres:{harnais.MOT_DE_PASSE}\n", encoding="utf-8")
    chemin.chmod(0o600)
    monkeypatch.setenv("PGPASSFILE", str(chemin))


def environnement() -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/tmp"),
        "LC_ALL": "C",
        "PGPASSFILE": os.environ["PGPASSFILE"],
    }


def sans_mot_de_passe(dsn: str) -> str:
    parametres = conninfo.conninfo_to_dict(dsn)
    parametres.pop("password", None)
    return conninfo.make_conninfo(**parametres)


def lancer_au_clavier(
    dsn: str, saisies: list[str], delai: float = 30.0
) -> tuple[int, str]:
    """L'outil dans un pseudo-terminal ; chaque saisie attend une invite (« : »)
    apparue APRÈS la saisie précédente : jamais d'avance sur psql, qui couperait
    l'écho trop tard."""
    if shutil.which("psql") is None:
        pytest.fail("le client psql est introuvable : l'outil ne peut pas être éprouvé")
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover — le processus fils
        os.execve(
            "/bin/sh", ["sh", str(SCRIPT), sans_mot_de_passe(dsn)], environnement()
        )
    sortie, reste, fin = b"", list(saisies), time.monotonic() + delai
    depuis = 0  # la sortie lue depuis la dernière saisie commence ici
    try:
        while time.monotonic() < fin:
            prets, _, _ = select.select([fd], [], [], 1.0)
            if not prets:
                continue
            try:
                morceau = os.read(fd, 4096)
            except OSError:
                break
            if not morceau:
                break
            sortie += morceau
            neuve = sortie[depuis:].strip()
            if reste and neuve.endswith(b":"):
                os.write(fd, reste.pop(0).encode() + b"\n")
                depuis = len(sortie)
        else:
            os.kill(pid, signal.SIGKILL)
    finally:
        os.close(fd)
    _, statut = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(statut), sortie.decode("utf-8", errors="replace")


@pytest.fixture
def base_nommee(conteneur_postgres, gabarit, request) -> Iterator[str]:
    """Une copie du gabarit qui porte le nom demandé (`apimanga_preprod` par défaut)."""
    nom = getattr(request, "param", "apimanga_preprod")
    with psycopg.connect(conteneur_postgres, autocommit=True) as cx:
        cx.execute(f'CREATE DATABASE "{nom}" TEMPLATE "{gabarit}"')
    try:
        yield conninfo.make_conninfo(conteneur_postgres, dbname=nom)
    finally:
        with psycopg.connect(conteneur_postgres, autocommit=True) as cx:
            cx.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
            cx.execute(f"DROP ROLE IF EXISTS {ROLE}")


def role_existe(dsn: str) -> bool:
    with psycopg.connect(dsn) as cx:
        return cx.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)", (ROLE,)
        ).fetchone()[0]


# --------------------------------------------------------------------------- #
#  Les gardes, avant toute saisie
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "dsn",
    (
        "postgresql://postgres:secret-factice@127.0.0.1:5432/apimanga_preprod",
        "host=127.0.0.1 dbname=apimanga_preprod user=postgres password=secret-factice",
    ),
    ids=("adresse", "mots-clés"),
)
def test_l_outil_refuse_un_dsn_qui_porte_un_mot_de_passe(dsn):
    """Lisible par `ps`, gardé par l'historique : refus, avant toute connexion."""
    resultat = subprocess.run(
        ["sh", str(SCRIPT), dsn],
        env=environnement(),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert resultat.returncode != 0
    assert "le DSN porte un mot de passe" in resultat.stderr
    assert "secret-factice" not in resultat.stdout + resultat.stderr


def test_l_outil_refuse_sans_argument():
    resultat = subprocess.run(
        ["sh", str(SCRIPT)],
        env=environnement(),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert resultat.returncode != 0
    assert "un argument attendu" in resultat.stderr


def test_l_outil_refuse_sans_terminal(base_migree):
    """Un tube ou une redirection : le mot de passe ne viendrait pas du clavier."""
    resultat = subprocess.run(
        ["sh", str(SCRIPT), sans_mot_de_passe(base_migree)],
        env=environnement(),
        capture_output=True,
        text=True,
        input="secret\nsecret\n",
        check=False,
    )
    assert resultat.returncode != 0
    assert "n'est pas un\n  terminal" in resultat.stderr
    assert not role_existe(base_migree)


def test_l_outil_refuse_une_autre_base(base_migree):
    code, sortie = lancer_au_clavier(base_migree, [])
    assert code != 0
    assert "seules apimanga et apimanga_preprod" in sortie
    assert not role_existe(base_migree)


@pytest.mark.parametrize("base_nommee", ["apimanga"], indirect=True)
def test_l_outil_refuse_une_base_sans_le_schema(conteneur_postgres, base_nommee):
    """`apimanga` avant sa 027 (le cas de la production pendant le lot 1)."""
    with psycopg.connect(base_nommee, autocommit=True) as cx:
        cx.execute("DROP SCHEMA application CASCADE")
    code, sortie = lancer_au_clavier(base_nommee, [])
    assert code != 0
    assert "le schéma application est absent de la base apimanga" in sortie
    assert not role_existe(base_nommee)


def test_l_outil_refuse_sans_le_groupe():
    """Un serveur neuf, où la 027 n'a jamais tourné : app_rw n'existe pas."""
    raison = harnais.raison_de_sauter()
    if raison:
        pytest.skip(raison)
    with harnais.conteneur("outil-sans-groupe") as admin:
        with psycopg.connect(admin, autocommit=True) as cx:
            cx.execute("CREATE DATABASE apimanga_preprod")
        code, sortie = lancer_au_clavier(
            conninfo.make_conninfo(admin, dbname="apimanga_preprod"), []
        )
    assert code != 0
    assert "le rôle de groupe app_rw est absent" in sortie


# --------------------------------------------------------------------------- #
#  La création, la saisie, le refus d'un rôle existant
# --------------------------------------------------------------------------- #
def test_l_outil_cree_le_role_sans_montrer_le_mot_de_passe(base_nommee):
    mot_de_passe = secrets.token_urlsafe(18)
    code, sortie = lancer_au_clavier(base_nommee, [mot_de_passe, mot_de_passe])

    assert code == 0, sortie
    assert mot_de_passe not in sortie, "le mot de passe est apparu à l'écran"
    assert sortie.count("password") >= 1 and "again" in sortie  # deux invites
    with psycopg.connect(base_nommee) as cx:
        attributs = cx.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication,"
            "       rolbypassrls, rolconnlimit,"
            "       (SELECT array_agg(m.roleid::regrole::text) FROM pg_auth_members m"
            "         WHERE m.member = r.oid)"
            "  FROM pg_roles r WHERE rolname = %s",
            (ROLE,),
        ).fetchone()
        (empreinte,) = cx.execute(
            "SELECT rolpassword FROM pg_authid WHERE rolname = %s", (ROLE,)
        ).fetchone()
    assert attributs == (True, False, False, False, False, False, 10, ["app_rw"])
    assert empreinte.startswith("SCRAM-SHA-256$"), "le mot de passe n'est pas haché"

    with psycopg.connect(
        conninfo.make_conninfo(base_nommee, user=ROLE, password=mot_de_passe)
    ) as cx:
        cx.execute(
            "INSERT INTO application.comptes (pseudo, nom_affiche, role, hachage)"
            " VALUES ('client_essai', 'Client', 'client', 'h')"
        )
        with pytest.raises(errors.InsufficientPrivilege):
            cx.execute("SELECT 1 FROM manga.ms_series_enriched LIMIT 1")


def test_l_outil_ne_cree_rien_si_les_saisies_different(base_nommee):
    code, sortie = lancer_au_clavier(base_nommee, ["premiere-saisie", "seconde-saisie"])

    assert code != 0
    assert "le rôle n'a pas été créé" in sortie
    assert not role_existe(base_nommee)


def test_l_outil_refuse_un_role_existant_sans_le_toucher(base_nommee):
    ancien, nouveau = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
    with psycopg.connect(base_nommee, autocommit=True) as cx:
        cx.execute(
            psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE app_rw").format(
                psycopg.sql.Identifier(ROLE), psycopg.sql.Literal(ancien)
            )
        )
    code, sortie = lancer_au_clavier(base_nommee, [nouveau, nouveau])

    assert code != 0
    assert f"le rôle {ROLE} existe déjà" in sortie
    with psycopg.connect(
        conninfo.make_conninfo(base_nommee, user=ROLE, password=ancien)
    ):
        pass  # l'ancien mot de passe vaut toujours
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(
            conninfo.make_conninfo(base_nommee, user=ROLE, password=nouveau)
        )


def test_l_outil_n_ecrit_aucun_mot_de_passe_dans_un_ordre():
    contenu = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(
        ligne for ligne in contenu.splitlines() if not ligne.lstrip().startswith("#")
    )
    assert "PASSWORD" not in code, (
        "un mot de passe passerait en clair dans un ordre SQL"
    )
    assert "\\\\password $ROLE" in code
    assert "read " not in code and "stty" not in code, "le shell ne lit aucun secret"
