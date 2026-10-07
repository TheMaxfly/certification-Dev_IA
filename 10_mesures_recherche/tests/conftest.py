"""Harnais : une base PostgreSQL + pgvector JETABLE, migrée par le runner du dépôt.

Garde-fou repris de `database/` et du module 05 : le DSN est fabriqué ici, à
partir du conteneur lancé par le harnais ; une DATABASE_URL de l'environnement
est ignorée, et l'absence de Docker fait sauter les tests qui ont besoin d'une
base — jamais de repli sur `apimanga`.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import numpy as np
import psycopg
import pytest

from mesures_recherche import configuration, executer, jeu

RACINE_DEPOT = Path(__file__).resolve().parents[2]
MIGRATIONS = RACINE_DEPOT / "database"
# Celle de database/tests : PostgreSQL 16 + pgvector 0.6.0, par digest.
IMAGE_POSTGRES = (
    "pgvector/pgvector:0.6.0-pg16"
    "@sha256:b740286128ce8e232fe0de3c8db2267d91aedc598dfbeaefb7ffb0b79ceef1b3"
)
DELAI_DEMARRAGE = 60


def docker_utilisable() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.fixture(scope="session")
def conteneur() -> str:
    if not docker_utilisable():
        pytest.skip("Docker indisponible : pas de base jetable, pas de repli réel.")
    nom = f"mesures-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            nom,
            "-e",
            "POSTGRES_PASSWORD=postgres",
            "-p",
            "0:5432",
            IMAGE_POSTGRES,
        ],
        capture_output=True,
        check=True,
    )
    try:
        liaison = json.loads(
            subprocess.run(
                ["docker", "inspect", nom], capture_output=True, text=True, check=True
            ).stdout
        )[0]["NetworkSettings"]["Ports"]["5432/tcp"][0]
        dsn = f"postgresql://postgres:postgres@127.0.0.1:{liaison['HostPort']}/postgres"

        import psycopg

        limite = time.monotonic() + DELAI_DEMARRAGE
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


@pytest.fixture
def base(conteneur) -> str:
    """Une base neuve par test, migrée (000 → dernière) par le runner du dépôt."""
    import psycopg

    nom = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(conteneur, autocommit=True) as cx:
        cx.execute(f'CREATE DATABASE "{nom}"')
    dsn = conteneur.rsplit("/", 1)[0] + f"/{nom}"
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    sortie = subprocess.run(
        ["uv", "run", "python", "migrate.py", "up"],
        cwd=MIGRATIONS,
        capture_output=True,
        text=True,
        env=env | {"DATABASE_URL": dsn},
    )
    assert sortie.returncode == 0, f"migrations en échec : {sortie.stderr}"
    try:
        yield dsn
    finally:
        with psycopg.connect(conteneur, autocommit=True) as cx:
            cx.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')


# --------------------------------------------------------------------------- #
#  Le banc de bout en bout : mini-catalogue, mini-corpus, mini-jeu gelé
# --------------------------------------------------------------------------- #

FRAGMENTS = {
    # chunk_id : (doc_key, texte)
    101: ("ms:1", "Un équipage de pirates part à l'aventure en haute mer."),
    102: ("ms:2", "Des pirates du ciel affrontent la marine dans une aventure."),
    103: ("ms:3", "Un carnet mortel tombe entre les mains d'un lycéen."),
    104: ("kitsu:50", "A notebook that kills whoever is named in it."),
    105: ("kitsu:60", "Space pirates on a long adventure across the stars."),
}
QUESTIONS = [
    # id, texte, mode, famille, issue, attendus
    (
        "Q001",
        "un manga de pirates et d'aventure",
        "proposition",
        "F1",
        "au_catalogue",
        {1: 2, 2: 1},
    ),
    ("Q002", "le carnet du lycéen", "reconnaissance", "F4", "au_catalogue", {3: 2}),
    ("Q003", "Le grimoire des brumes de Zolthar", "refus", "F7", "inconnue", {}),
]


def unitaire(axe: int, dim: int = 1024) -> str:
    v = [0.0] * dim
    v[axe] = 1.0
    return "[" + ",".join(map(str, v)) + "]"


@pytest.fixture
def banc(base, tmp_path):
    """Base garnie, et une configuration qui pointe sur le mini-jeu gelé."""
    dossier = tmp_path / "jeu"
    dossier.mkdir()
    (dossier / "questions.csv").write_text("le mini-jeu\n", encoding="utf-8")
    empreinte = jeu.empreinte_fichiers(dossier)
    with psycopg.connect(base) as cx:
        for sid in (1, 2, 3, 4):
            cx.execute(
                "INSERT INTO manga.ms_series_enriched (series_id, series_title)"
                " VALUES (%s, %s)",
                (sid, f"Série {sid}"),
            )
        cx.execute(
            "INSERT INTO manga.work_identity (series_id, kitsu_id) VALUES (3, '50')"
        )
        for doc_key in {d for d, _ in FRAGMENTS.values()}:
            source, ident = doc_key.split(":")
            if source == "ms":
                cx.execute(
                    "INSERT INTO bench.corpus_docs (doc_key, source, series_id,"
                    " doc_text) VALUES (%s, 'ms_review', %s, 'x')",
                    (doc_key, int(ident)),
                )
            else:
                cx.execute(
                    "INSERT INTO bench.corpus_docs (doc_key, source, kitsu_id,"
                    " doc_text, title) VALUES (%s, 'kitsu_synopsis', %s, 'x', %s)",
                    (doc_key, int(ident), f"Kitsu {ident}"),
                )
        for chunk_id, (doc_key, texte) in FRAGMENTS.items():
            cx.execute(
                "INSERT INTO bench.corpus_chunks (chunk_id, doc_key, chunk_index,"
                " chunk_text) VALUES (%s, %s, 0, %s)",
                (chunk_id, doc_key, texte),
            )
        eid = cx.execute(
            "INSERT INTO bench.encodages (modele, revision, dimension,"
            " precision_calcul, prefixe_document, prefixe_requete, outil,"
            " outil_version, image, image_digest, taille_lot, nb_fragments,"
            " termine_le) VALUES"
            " ('BAAI/bge-m3', %s, 1024, 'float32', '', '', 'text-embeddings-inference',"
            " '1.9.4', 'img', %s, 16, 5, now()) RETURNING encodage_id",
            ("a" * 40, "sha256:" + "b" * 64),
        ).fetchone()[0]
        for axe, chunk_id in enumerate(FRAGMENTS):
            cx.execute(
                "INSERT INTO bench.vecteurs_bge_m3 (chunk_id, encodage_id, embedding)"
                " VALUES (%s, %s, %s)",
                (chunk_id, eid, unitaire(axe)),
            )
        cx.execute(
            "INSERT INTO bench.eval_jeux VALUES ('v9', %s, now(), 'mini-jeu de test')",
            (empreinte,),
        )
        for qid, texte, mode, famille, issue, attendus in QUESTIONS:
            cx.execute(
                "INSERT INTO bench.eval_questions (jeu_version, question_id, texte,"
                " mode, famille, issue_attendue, origine, note) VALUES"
                " ('v9', %s, %s, %s, %s, %s, 'nouvelle', 'test')",
                (qid, texte, mode, famille, issue),
            )
            for sid, grade in attendus.items():
                cx.execute(
                    "INSERT INTO bench.eval_attendus VALUES ('v9', %s, %s, %s)",
                    (qid, sid, grade),
                )
    config = copy.deepcopy(configuration.charger())
    config["jeu"] |= {"version": "v9", "empreinte": empreinte, "dossier": str(dossier)}
    return base, config, dossier


@pytest.fixture
def encodeur_doublure(monkeypatch):
    """Q001 « vise » le fragment 102, Q002 le fragment 104 (axes 1 et 3)."""
    axes = {"un manga de pirates et d'aventure": 1, "le carnet du lycéen": 3}

    def fabrique(instance_nom):
        def encoder(texte):
            v = np.zeros(1024)
            v[axes.get(texte, 4)] = 1.0
            return v

        return encoder, {"service_version": "doublure"}

    monkeypatch.setattr(executer, "encodeur_du_service", fabrique)
