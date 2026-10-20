"""Harnais : une base PostgreSQL + pgvector JETABLE, migrée par le runner du dépôt,
et une doublure HTTP du service d'embedding.

Le harnais partagé (`database/harnais_tests/`) est chargé par son chemin, comme dans
les modules 05, 08, 09 et 10. Le DSN est fabriqué par lui, à partir du conteneur
qu'il lance ; une variable d'environnement n'est jamais lue, et l'absence de Docker
fait sauter les tests à base — jamais de repli sur `apimanga`.

L'API y tourne sous un rôle de connexion de test, membre de `modele_ro` seul
(migration 023), sans mot de passe dans le DSN (un `.pgpass` jetable) : ce que
l'API lit, elle doit pouvoir le lire sous ce rôle. Le service d'embedding est une
doublure (`/health`, `/info`) ; aucun test ne demande le service réel.
"""

from __future__ import annotations

import importlib.util
import json
import secrets
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import conninfo
from service_embedding.configuration import Instance
from service_embedding.configuration import charger as charger_instance

from api_modele.application import creer_application
from api_modele.configuration import Reglages, charger

RACINE_MODULE = Path(__file__).resolve().parents[1]
RACINE_DEPOT = RACINE_MODULE.parent

CLE_APPLICATION = "a" * 40
CLE_DEMONSTRATION = "d" * 40
ROLE_API = "essai_api_modele"


def _charger_harnais():
    """Le harnais partagé des bases jetables (`database/harnais_tests/`), par chemin."""
    spec = importlib.util.spec_from_file_location(
        "harnais_tests", RACINE_DEPOT / "database" / "harnais_tests" / "__init__.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["harnais_tests"] = module
    spec.loader.exec_module(module)
    return module


harnais = _charger_harnais()


@pytest.fixture(scope="session")
def conteneur() -> Iterator[str]:
    """Un PostgreSQL jetable pour toute la session."""
    raison = harnais.raison_de_sauter()
    if raison:
        pytest.skip(raison)
    with harnais.conteneur("api-modele-test") as dsn:
        yield dsn


@pytest.fixture(scope="session")
def gabarit(conteneur: str) -> str:
    """La base gabarit, migrée une fois par session par le runner du dépôt."""
    return harnais.creer_gabarit(conteneur)


@pytest.fixture
def base(conteneur: str, gabarit: str) -> Iterator[str]:
    """Une base neuve par test, copiée du gabarit migré."""
    with harnais.base_depuis_gabarit(conteneur, gabarit) as dsn:
        yield dsn


# --------------------------------------------------------------------------- #
#  Le rôle de l'API : membre de modele_ro seul, sessions en lecture seule
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def fichier_pgpass(
    conteneur: str, gabarit: str, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """Le rôle de connexion de test (objet de cluster), et son `.pgpass` jetable."""
    mot_de_passe = secrets.token_urlsafe(24)
    with psycopg.connect(conteneur, autocommit=True) as cx:
        cx.execute(
            psycopg.sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} IN ROLE modele_ro"
            ).format(
                psycopg.sql.Identifier(ROLE_API), psycopg.sql.Literal(mot_de_passe)
            )
        )
        cx.execute(
            psycopg.sql.SQL(
                "ALTER ROLE {} SET default_transaction_read_only = on"
            ).format(psycopg.sql.Identifier(ROLE_API))
        )
    chemin = tmp_path_factory.mktemp("pgpass") / "pgpass"
    chemin.write_text(f"*:*:*:{ROLE_API}:{mot_de_passe}\n", encoding="utf-8")
    chemin.chmod(0o600)
    return chemin


@pytest.fixture
def pgpass(fichier_pgpass: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PGPASSFILE", str(fichier_pgpass))
    return fichier_pgpass


def dsn_du_role(dsn_admin: str) -> str:
    """Le DSN de la base du test, sous le rôle de l'API, sans mot de passe."""
    parametres = conninfo.conninfo_to_dict(dsn_admin)
    parametres.pop("password", None)
    parametres["user"] = ROLE_API
    return conninfo.make_conninfo(**parametres)


# --------------------------------------------------------------------------- #
#  La doublure du service d'embedding
# --------------------------------------------------------------------------- #
class Doublure(BaseHTTPRequestHandler):
    """Une instance « embeddinggemma » simulée : `/health` et `/info`."""

    info: ClassVar[dict[str, Any]] = {}
    sante: ClassVar[int] = 200

    def log_message(self, *args: object) -> None:
        pass

    def _repondre(self, statut: int, corps: object) -> None:
        donnees = json.dumps(corps).encode()
        self.send_response(statut)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._repondre(Doublure.sante, {})
        elif self.path == "/info":
            self._repondre(200, Doublure.info)
        else:
            self._repondre(404, {"error": "route inconnue"})


@dataclass
class ServiceDoublure:
    serveur: HTTPServer
    instance: Instance
    arrete: bool = False

    def arreter(self) -> None:
        if not self.arrete:
            self.arrete = True
            self.serveur.shutdown()
            self.serveur.server_close()


@pytest.fixture
def service_doublure() -> Iterator[ServiceDoublure]:
    vraie = charger_instance("embeddinggemma")
    Doublure.info = {
        "model_id": vraie.model_id,
        "model_sha": vraie.revision,
        "model_dtype": vraie.dtype,
        "version": "1.9.4",
        "max_input_length": 2048,
    }
    Doublure.sante = 200
    serveur = HTTPServer(("127.0.0.1", 0), Doublure)
    # Interrogation courte : `shutdown()` attend la fin d'un cycle (0,5 s par défaut).
    threading.Thread(
        target=serveur.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    doublure = ServiceDoublure(
        serveur, replace(vraie, hote="127.0.0.1", port=serveur.server_address[1])
    )
    yield doublure
    doublure.arreter()


# --------------------------------------------------------------------------- #
#  Une base garnie : un corpus clos, son encodage EmbeddingGemma, promu
# --------------------------------------------------------------------------- #
CORPUS = "v7"
DOCUMENTS = {
    "ms_review:1": ("ms_review", 1, None),
    "kitsu:50": ("kitsu_synopsis", None, 50),
}
FRAGMENTS = {701: "ms_review:1", 702: "ms_review:1", 703: "kitsu:50"}


def inscrire_encodage(
    cx: psycopg.Connection, instance: Instance, outil_version: str = "1.9.4"
) -> int:
    return cx.execute(
        "INSERT INTO bench.encodages (corpus_id, modele, revision, dimension,"
        " precision_calcul, prefixe_document, prefixe_requete, outil, outil_version,"
        " image, image_digest, taille_lot, nb_fragments, termine_le) VALUES"
        " (%s, %s, %s, %s, %s, %s, %s, 'text-embeddings-inference', %s, 'img', %s,"
        " 16, %s, now()) RETURNING encodage_id",
        (
            CORPUS,
            instance.model_id,
            instance.revision,
            instance.dimension,
            instance.dtype,
            instance.prefixe_document,
            instance.prefixe_requete,
            outil_version,
            "sha256:" + "b" * 64,
            len(FRAGMENTS),
        ),
    ).fetchone()[0]


def promouvoir(cx: psycopg.Connection, encodage_id: int) -> None:
    """Une promotion au journal ; l'encodage précédent est celui en service (021)."""
    cx.execute(
        "INSERT INTO bench.promotions (decision, encodage_id, encodage_precedent_id,"
        " motif, decide_par) SELECT 'promu', %s,"
        " (SELECT encodage_id FROM bench.v_encodage_en_service), 'essai', 'essai'",
        (encodage_id,),
    )


@dataclass
class BaseApi:
    dsn_admin: str
    encodage_id: int


def garnir(dsn: str, instance: Instance, *, clore: bool = True) -> int:
    """Un corpus `v7` (clos par défaut) et son encodage ; rend l'encodage."""
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO manga.ms_series_enriched (series_id, series_title)"
            " VALUES (1, 'Série 1')"
        )
        cx.execute(
            "INSERT INTO bench.corpus (corpus_id, regle, chunking_id)"
            " SELECT %s, 'essai', chunking_id FROM bench.chunking_strategies"
            " WHERE name = 'char_1200_overlap_200'",
            (CORPUS,),
        )
        for doc_key, (source, series_id, kitsu_id) in DOCUMENTS.items():
            cx.execute(
                "INSERT INTO bench.corpus_docs (corpus_id, doc_key, source, series_id,"
                " kitsu_id, doc_text, title) VALUES (%s, %s, %s, %s, %s, 'x', 't')",
                (CORPUS, doc_key, source, series_id, kitsu_id),
            )
        for index, (chunk_id, doc_key) in enumerate(FRAGMENTS.items()):
            cx.execute(
                "INSERT INTO bench.corpus_chunks (corpus_id, chunk_id, doc_key,"
                " chunk_index, chunk_text) VALUES (%s, %s, %s, %s, 'texte')",
                (CORPUS, chunk_id, doc_key, index),
            )
        if clore:
            cx.execute(
                "UPDATE bench.corpus SET clos_le = now(), nb_documents = %s,"
                " nb_fragments = %s WHERE corpus_id = %s",
                (len(DOCUMENTS), len(FRAGMENTS), CORPUS),
            )
        return inscrire_encodage(cx, instance)


@pytest.fixture
def base_api(base: str, service_doublure: ServiceDoublure) -> BaseApi:
    encodage_id = garnir(base, service_doublure.instance)
    with psycopg.connect(base) as cx:
        promouvoir(cx, encodage_id)
    return BaseApi(base, encodage_id)


# --------------------------------------------------------------------------- #
#  Les réglages et l'application
# --------------------------------------------------------------------------- #
def environnement(dsn: str) -> dict[str, str]:
    return {
        "API_MODELE_CLES": (
            f"application:{CLE_APPLICATION},demonstration:{CLE_DEMONSTRATION}"
        ),
        "API_MODELE_DSN": dsn,
    }


@dataclass
class Horloge:
    t: float = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def horloge() -> Horloge:
    return Horloge()


@pytest.fixture
def reglages(base_api: BaseApi, pgpass: Path) -> Reglages:
    return replace(
        charger(environnement(dsn_du_role(base_api.dsn_admin))), pool_attente_s=2.0
    )


@pytest.fixture
def api(
    reglages: Reglages, service_doublure: ServiceDoublure, horloge: Horloge
) -> Iterator[TestClient]:
    """L'API démarrée, vue depuis le bouclage (adresse admise pour `/metrics`)."""
    application = creer_application(
        reglages, instance=service_doublure.instance, horloge=horloge
    )
    with TestClient(application, client=("127.0.0.1", 50000)) as client:
        yield client


def entetes(cle: str = CLE_APPLICATION) -> dict[str, str]:
    return {"X-API-Key": cle}
