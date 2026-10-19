"""Harnais partagé des bases PostgreSQL JETABLES de test : database, 05, 09, 10.

- **Un conteneur par session**, lancé ici, sans durabilité (une base jetable n'a que
  faire de `fsync`) ; arrêté à la fin.
- **Une base gabarit par session**, migrée une seule fois par le vrai runner
  (`database/migrate.py`, dans le processus) : la base de test est celle du dépôt.
- **Une base neuve par test**, copiée du gabarit (`CREATE DATABASE … TEMPLATE`), et
  les droits de niveau base que la copie ne reporte pas (les `GRANT CONNECT` de 012
  et 022) reportés depuis le gabarit : la copie vaut une base migrée.
- **Une base vide**, pour les tests qui jouent les migrations eux-mêmes, pas à pas.

Garde-fou central : le DSN est toujours fabriqué ici, à partir du conteneur lancé
ici. Une DATABASE_URL de l'environnement n'est jamais lue ; sans Docker ou sans
l'image, les tests sautent avec un message explicite, jamais un repli sur une base
réelle.

L'empreinte de l'image n'est écrite qu'ici. `outils/fidelite.sh` et
`verification/verifier.sh` la lisent dans ce fichier.

Chargé par son chemin (`charger_harnais`, en tête des conftest), comme le module 10
charge `atteignabilite.py` du 05 : aucun module de test n'installe `database/`.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

# PostgreSQL 16 avec pgvector 0.6.0 — la version d'`apimanga` —, épinglée par digest :
# la migration 020 crée l'extension `vector`, que `postgres:16-alpine` n'embarque pas.
IMAGE_POSTGRES = (
    "pgvector/pgvector:0.6.0-pg16"
    "@sha256:b740286128ce8e232fe0de3c8db2267d91aedc598dfbeaefb7ffb0b79ceef1b3"
)

# Base jetable : la durabilité n'y sert à rien et coûte cher.
SANS_DURABILITE = [
    "-c",
    "fsync=off",
    "-c",
    "synchronous_commit=off",
    "-c",
    "full_page_writes=off",
]

MOT_DE_PASSE = "postgres"
DELAI_DEMARRAGE = 60
RACINE_DATABASE = Path(__file__).resolve().parents[1]


def charger_migrate() -> ModuleType:
    """`database/migrate.py`, chargé par son chemin (une seule fois)."""
    if "migrate" in sys.modules:
        return sys.modules["migrate"]
    spec = importlib.util.spec_from_file_location(
        "migrate", RACINE_DATABASE / "migrate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Enregistré avant exec_module : @dataclass résout ses annotations via
    # sys.modules[cls.__module__].
    sys.modules["migrate"] = module
    spec.loader.exec_module(module)
    return module


def raison_de_sauter() -> str | None:
    """Pourquoi les tests à base ne peuvent pas tourner ici ; None s'ils le peuvent."""
    if shutil.which("docker") is None or (
        subprocess.run(["docker", "info"], capture_output=True, check=False).returncode
        != 0
    ):
        return (
            "Docker est indisponible : ces tests ont besoin d'une base jetable. Ils ne "
            "se rabattront jamais sur une base réelle."
        )
    if (
        subprocess.run(
            ["docker", "image", "inspect", IMAGE_POSTGRES],
            capture_output=True,
            check=False,
        ).returncode
        != 0
    ):
        return (
            f"Image {IMAGE_POSTGRES} absente en local. La récupérer avec "
            f"`docker pull {IMAGE_POSTGRES}` (accès réseau requis)."
        )
    return None


@contextmanager
def conteneur(prefixe: str) -> Iterator[str]:
    """Lance le PostgreSQL jetable ; rend le DSN de la base d'administration."""
    import psycopg

    nom = f"{prefixe}-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            nom,
            "-e",
            f"POSTGRES_PASSWORD={MOT_DE_PASSE}",
            "-p",
            "0:5432",
            IMAGE_POSTGRES,
            *SANS_DURABILITE,
        ],
        capture_output=True,
        check=True,
    )
    try:
        brut = subprocess.run(
            ["docker", "inspect", nom], capture_output=True, text=True, check=True
        ).stdout
        port = json.loads(brut)[0]["NetworkSettings"]["Ports"]["5432/tcp"][0][
            "HostPort"
        ]
        dsn = f"postgresql://postgres:{MOT_DE_PASSE}@127.0.0.1:{port}/postgres"
        limite = time.monotonic() + DELAI_DEMARRAGE
        while (
            subprocess.run(
                ["docker", "exec", nom, "pg_isready", "-U", "postgres"],
                capture_output=True,
                check=False,
            ).returncode
            != 0
        ):
            if time.monotonic() > limite:
                raise RuntimeError("PostgreSQL n'a pas démarré à temps")
            time.sleep(0.3)
        # pg_isready répond avant que les connexions soient réellement servies.
        while True:
            try:
                with psycopg.connect(dsn, connect_timeout=2):
                    break
            except psycopg.OperationalError:
                if time.monotonic() > limite:
                    raise
                time.sleep(0.3)
        yield dsn
    finally:
        subprocess.run(["docker", "rm", "-f", nom], capture_output=True, check=False)


def dsn_de(admin: str, nom: str) -> str:
    return admin.rsplit("/", 1)[0] + f"/{nom}"


def migrer(dsn: str, cible: str | None = None) -> None:
    """Joue les migrations du dépôt jusqu'à `cible` (toutes sinon), par le runner."""
    migrate = charger_migrate()
    migrations = migrate.decouvrir()
    with migrate.connecter(dsn) as connexion:
        appliquees = migrate.deja_appliquees(connexion)
        migrate.verifier_checksums(migrations, appliquees)
        for migration in migrations:
            if cible is not None and migration.version > cible:
                break
            if migration.version not in appliquees:
                migrate.appliquer(connexion, migration)


def creer_gabarit(admin: str, nom: str = "gabarit") -> str:
    """La base gabarit de la session : créée, migrée une fois ; rend son nom."""
    import psycopg

    with psycopg.connect(admin, autocommit=True) as connexion:
        connexion.execute(f'CREATE DATABASE "{nom}"')
    migrer(dsn_de(admin, nom))
    return nom


def _reporter_droits(connexion, gabarit: str, nom: str) -> None:
    """Les privilèges de niveau base du gabarit, que `TEMPLATE` ne recopie pas."""
    droits = connexion.execute(
        """
        SELECT a.privilege_type,
               CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END
          FROM pg_database d
          CROSS JOIN LATERAL aclexplode(d.datacl) a
          LEFT JOIN pg_roles r ON r.oid = a.grantee
         WHERE d.datname = %s AND a.grantee <> d.datdba
        """,
        (gabarit,),
    ).fetchall()
    for privilege, beneficiaire in droits:
        connexion.execute(f'GRANT {privilege} ON DATABASE "{nom}" TO {beneficiaire}')


@contextmanager
def base_depuis_gabarit(admin: str, gabarit: str) -> Iterator[str]:
    """Une base neuve, copiée du gabarit migré ; détruite à la sortie."""
    import psycopg

    nom = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin, autocommit=True) as connexion:
        connexion.execute(f'CREATE DATABASE "{nom}" TEMPLATE "{gabarit}"')
        _reporter_droits(connexion, gabarit, nom)
    try:
        yield dsn_de(admin, nom)
    finally:
        with psycopg.connect(admin, autocommit=True) as connexion:
            connexion.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')


@contextmanager
def base_vide(admin: str) -> Iterator[str]:
    """Une base neuve et vide, pour les tests qui jouent les migrations eux-mêmes."""
    import psycopg

    nom = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin, autocommit=True) as connexion:
        connexion.execute(f'CREATE DATABASE "{nom}"')
    try:
        yield dsn_de(admin, nom)
    finally:
        with psycopg.connect(admin, autocommit=True) as connexion:
            connexion.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
