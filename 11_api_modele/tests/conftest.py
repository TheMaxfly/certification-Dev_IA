"""Harnais : une base PostgreSQL + pgvector JETABLE, migrée par le runner du dépôt,
et une doublure HTTP du service d'embedding.

Le harnais partagé (`database/harnais_tests/`) est chargé par son chemin, comme dans
les modules 05, 08, 09 et 10. Le DSN est fabriqué par lui, à partir du conteneur
qu'il lance ; une variable d'environnement n'est jamais lue, et l'absence de Docker
fait sauter les tests à base — jamais de repli sur `apimanga`.

L'API y tourne sous un rôle de connexion de test, membre de `modele_ro` seul
(migration 023), sans mot de passe dans le DSN (un `.pgpass` jetable) : ce que
l'API lit, elle doit pouvoir le lire sous ce rôle. Le service d'embedding est une
doublure (`/health`, `/info`, `/embed`, `/tokenize`) ; aucun test ne demande le
service réel.

Le contrat est gardé sur toute la suite : une réponse de l'API, dans n'importe quel
test, sur une opération du contrat versionné, doit avoir un statut que le contrat
déclare pour elle, et, quand la description de ce statut énumère ses codes d'erreur
(entre accents graves), l'un de ces codes (`aucun_statut_hors_contrat`).

Les fournisseurs du modèle de langage sont des doublures sur le bouclage (deux
serveurs, deux profils factices) ; sur toute la suite, une requête de la couche des
fournisseurs vers un hôte hors du bouclage n'est pas envoyée et fait échouer le test
(`aucun_appel_hors_du_bouclage`) : aucun test n'atteint un fournisseur réel.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import secrets
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import conninfo
from service_embedding.configuration import Instance
from service_embedding.configuration import charger as charger_instance

from api_modele.application import creer_application
from api_modele.configuration import FICHIER_REGLAGES, Reglages, charger
from api_modele.contrat import DESTINATION

RACINE_MODULE = Path(__file__).resolve().parents[1]
RACINE_DEPOT = RACINE_MODULE.parent

CLE_APPLICATION = "a" * 40
CLE_DEMONSTRATION = "d" * 40
#: La clé du profil actif, factice : aucun test n'appelle un fournisseur réel.
CLE_FOURNISSEUR = "factice-cle-du-harnais"
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


# --------------------------------------------------------------------------- #
#  Le contrat, gardé sur toute la suite : aucun statut qu'il ne déclare pas
# --------------------------------------------------------------------------- #
CONTRAT_VERSIONNE = json.loads(DESTINATION.read_text(encoding="utf-8"))
#: (gabarit de route, méthode) → statuts déclarés, lus dans le contrat versionné.
OPERATIONS: dict[tuple[str, str], set[int]] = {
    (chemin, methode.upper()): {int(statut) for statut in operation["responses"]}
    for chemin, operations in CONTRAT_VERSIONNE["paths"].items()
    for methode, operation in operations.items()
}


def codes_declares(description: str) -> set[str]:
    """Les codes d'erreur qu'une description de réponse énumère (accents graves)."""
    return set(re.findall(r"`([a-z_]+)`", description))


#: (route, méthode, statut) → codes d'erreur énumérés par le contrat (vide : aucun).
CODES: dict[tuple[str, str, int], set[str]] = {
    (chemin, methode.upper(), int(statut)): codes_declares(
        reponse.get("description", "")
    )
    for chemin, operations in CONTRAT_VERSIONNE["paths"].items()
    for methode, operation in operations.items()
    for statut, reponse in operation["responses"].items()
}


type Vue = tuple[str, str, int, str | None]  # route, méthode, statut, code


class Reponses(logging.Handler):
    """Recueille (route, méthode, statut, code) de chaque ligne « requete » du
    journal."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.vues: list[Vue] = []

    def emit(self, record: logging.LogRecord) -> None:
        champs = getattr(record, "champs", None)
        if record.getMessage() == "requete" and isinstance(champs, dict):
            vue = (
                champs.get("route"),
                champs.get("methode"),
                champs.get("statut"),
                champs.get("code"),
            )
            self.vues.append(vue)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def aucun_statut_hors_contrat() -> Iterator[Reponses]:
    """Spec 04, A.2 : aucun code non documenté n'est produit. Les routes d'essai
    ajoutées par un test, les routes inconnues (404) et les méthodes non permises
    (405) ne sont pas des opérations du contrat."""
    journal = logging.getLogger("api_modele.requetes")
    niveau = journal.level
    capteur = Reponses()
    journal.addHandler(capteur)
    journal.setLevel(logging.INFO)
    try:
        yield capteur
    finally:
        journal.removeHandler(capteur)
        journal.setLevel(niveau)
    hors = statuts_hors_contrat(capteur.vues)
    assert not hors, f"statuts ou codes que le contrat ne déclare pas : {hors}"


#: Les hôtes qu'un test peut joindre par la couche des fournisseurs.
BOUCLAGE = ("127.0.0.1", "localhost", "::1")


@pytest.fixture(autouse=True)
def aucun_appel_hors_du_bouclage(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[str]]:
    """Règle du cadre : aucun test n'appelle un fournisseur réel. Une requête du
    client asynchrone de httpx (celui de la couche des fournisseurs) vers un hôte
    hors du bouclage n'est pas envoyée, et fait échouer le test."""
    hors: list[str] = []
    envoyer = httpx.AsyncClient.send

    async def garder(
        client: httpx.AsyncClient, requete: httpx.Request, **options: Any
    ) -> httpx.Response:
        if requete.url.host not in BOUCLAGE:
            hors.append(requete.url.host)
            raise AssertionError(f"appel hors du bouclage : {requete.url.host}")
        return await envoyer(client, requete, **options)

    monkeypatch.setattr(httpx.AsyncClient, "send", garder)
    yield hors
    assert not hors, f"appels hors du bouclage : {hors}"


def statuts_hors_contrat(vues: list[Vue]) -> list[Vue]:
    """Les réponses d'une opération du contrat dont le statut n'y est pas déclaré, ou
    dont le code n'est pas parmi ceux que le contrat énumère pour ce statut."""
    hors: list[Vue] = []
    for route, methode, statut, code in vues:
        if (route, methode) not in OPERATIONS:
            continue
        codes = CODES.get((route, methode, statut), set())
        if statut not in OPERATIONS[route, methode] or (codes and code not in codes):
            hors.append((route, methode, statut, code))
    return hors


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
DIMENSION = 768


def vecteur(*poids: float) -> list[float]:
    """Un vecteur unitaire de 768 composantes, `poids` sur les premiers axes."""
    v = [0.0] * DIMENSION
    for axe, p in enumerate(poids):
        v[axe] = p
    norme = sum(x * x for x in v) ** 0.5
    return [x / norme for x in v]


#: La question d'essai : fragments 701 (0,1), 702 (0,7), 703 (0,3), 704 (0,9).
QUESTION = "une aventure de pirates"
VECTEURS_QUESTIONS = {QUESTION: vecteur(0.1, 0.7, 0.3, 0.9)}


class Doublure(BaseHTTPRequestHandler):
    """Une instance « embeddinggemma » simulée : `/health`, `/info`, `/embed`,
    `/tokenize` ; retard, refus et nombre de jetons réglables."""

    info: ClassVar[dict[str, Any]] = {}
    sante: ClassVar[int] = 200
    retard_s: ClassVar[float] = 0.0
    refus_embed: ClassVar[int | None] = None
    jetons: ClassVar[int | None] = None
    recus: ClassVar[list[dict[str, Any]]] = []

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

    def do_POST(self) -> None:  # noqa: N802
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Doublure.recus.append({"route": self.path, **corps})
        prefixe = Doublure.info.get("prefixe_requete", "")
        textes = [t.removeprefix(prefixe) for t in corps["inputs"]]
        if self.path == "/embed":
            if Doublure.retard_s:
                time.sleep(Doublure.retard_s)
            if Doublure.refus_embed is not None:
                self._repondre(Doublure.refus_embed, {"error": "refus"})
                return
            self._repondre(
                200, [VECTEURS_QUESTIONS.get(t, vecteur(1.0)) for t in textes]
            )
        elif self.path == "/tokenize":
            self._repondre(
                200, [[0] * (Doublure.jetons or len(t)) for t in corps["inputs"]]
            )
        else:
            self._repondre(404, {"error": "route inconnue"})


class ServeurDoublure(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        pass  # un client parti avant la réponse (délai dépassé) : voulu


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
    Doublure.retard_s = 0.0
    Doublure.refus_embed = None
    Doublure.jetons = None
    Doublure.recus = []
    serveur = ServeurDoublure(("127.0.0.1", 0), Doublure)
    # Interrogation courte : `shutdown()` attend la fin d'un cycle (0,5 s par défaut).
    threading.Thread(
        target=serveur.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    instance = replace(vraie, hote="127.0.0.1", port=serveur.server_address[1])
    Doublure.info["prefixe_requete"] = instance.prefixe_requete
    doublure = ServiceDoublure(serveur, instance)
    yield doublure
    doublure.arreter()


# --------------------------------------------------------------------------- #
#  Une base garnie : un corpus clos, son encodage EmbeddingGemma, promu
# --------------------------------------------------------------------------- #
CORPUS = "v7"
#: Les auteurs de critiques de la base de test, factices : la vue des pseudonymes (024)
#: les rend à l'API, qui refuse de démarrer si elle est vide.
AUTEURS = ("Lecteurfactice", "Membrefictif")
DOCUMENTS = {
    "ms_review:1": ("ms_review", 1, None, "Série 1"),
    "kitsu:50": ("kitsu_synopsis", None, 50, "Kitsu Cinquante"),
    "kitsu:60": ("kitsu_synopsis", None, 60, "Kitsu Soixante"),
}
#: chunk_id → (doc_key, texte). La série 2 n'est atteinte que par le synopsis Kitsu 50,
#: rattaché par le moyeu ; l'œuvre Kitsu 60 n'est pas au catalogue.
FRAGMENTS = {
    701: ("ms_review:1", "Une critique, premier fragment."),
    702: ("ms_review:1", "Une critique, second fragment."),
    703: ("kitsu:50", "A synopsis attached to series two."),
    704: ("kitsu:60", "A synopsis outside the catalogue."),
}
#: Les vecteurs de l'encodage en service : un axe par fragment.
AXES = {701: 0, 702: 1, 703: 2, 704: 3}
#: Un encodage leurre, non promu, du même corpus : d'autres vecteurs.
AXES_LEURRE = {701: 0, 702: 0, 703: 1, 704: 3}


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


def vectoriser(cx: psycopg.Connection, encodage_id: int, axes: dict[int, int]) -> None:
    for chunk_id, axe in axes.items():
        v = [0.0] * DIMENSION
        v[axe] = 1.0
        cx.execute(
            "INSERT INTO bench.vecteurs_embeddinggemma (chunk_id, encodage_id,"
            " corpus_id, embedding) VALUES (%s, %s, %s, %s)",
            (chunk_id, encodage_id, CORPUS, "[" + ",".join(map(str, v)) + "]"),
        )


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
    """Un corpus `v7` (clos par défaut), un encodage leurre et l'encodage à promouvoir,
    vectorisés ; rend ce dernier."""
    with psycopg.connect(dsn) as cx:
        for sid in (1, 2):
            cx.execute(
                "INSERT INTO manga.ms_series_enriched (series_id, series_title)"
                " VALUES (%s, %s)",
                (sid, f"Série {sid}"),
            )
        cx.execute(
            "INSERT INTO manga.work_identity (series_id, kitsu_id) VALUES (2, '50')"
        )
        for auteur in AUTEURS:
            cx.execute(
                "INSERT INTO manga.ms_reviews_all (series_id, review_author)"
                " VALUES (1, %s)",
                (auteur,),
            )
        cx.execute(
            "INSERT INTO bench.corpus (corpus_id, regle, chunking_id)"
            " SELECT %s, 'essai', chunking_id FROM bench.chunking_strategies"
            " WHERE name = 'char_1200_overlap_200'",
            (CORPUS,),
        )
        for doc_key, (source, series_id, kitsu_id, titre) in DOCUMENTS.items():
            cx.execute(
                "INSERT INTO bench.corpus_docs (corpus_id, doc_key, source, series_id,"
                " kitsu_id, doc_text, title) VALUES (%s, %s, %s, %s, %s, 'x', %s)",
                (CORPUS, doc_key, source, series_id, kitsu_id, titre),
            )
        for index, (chunk_id, (doc_key, texte)) in enumerate(FRAGMENTS.items()):
            cx.execute(
                "INSERT INTO bench.corpus_chunks (corpus_id, chunk_id, doc_key,"
                " chunk_index, chunk_text) VALUES (%s, %s, %s, %s, %s)",
                (CORPUS, chunk_id, doc_key, index, texte),
            )
        if clore:
            cx.execute(
                "UPDATE bench.corpus SET clos_le = now(), nb_documents = %s,"
                " nb_fragments = %s WHERE corpus_id = %s",
                (len(DOCUMENTS), len(FRAGMENTS), CORPUS),
            )
        leurre = inscrire_encodage(cx, instance, outil_version="1.9.3")
        vectoriser(cx, leurre, AXES_LEURRE)
        encodage = inscrire_encodage(cx, instance)
        vectoriser(cx, encodage, AXES)
        return encodage


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
        "MISTRAL_API_KEY": CLE_FOURNISSEUR,
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
        reglages,
        instance=service_doublure.instance,
        horloge=horloge,
        environ={"MISTRAL_API_KEY": CLE_FOURNISSEUR},
    )
    with TestClient(application, client=("127.0.0.1", 50000)) as client:
        yield client


def entetes(cle: str = CLE_APPLICATION) -> dict[str, str]:
    return {"X-API-Key": cle}


# --------------------------------------------------------------------------- #
#  Les doublures des fournisseurs du modèle de langage, et deux profils factices
# --------------------------------------------------------------------------- #
#: Les clés factices des deux profils : aucune n'a la forme d'une clé réelle.
CLE_A = "factice-cle-a-0f1e2d3c4b5a6978"
CLE_B = "factice-cle-b-8796a5b4c3d2e1f0"
CLES_FACTICES = {"CLE_FACTICE_A": CLE_A, "CLE_FACTICE_B": CLE_B}
#: Délais des profils factices : courts, pour éprouver les dépassements vite.
CONNEXION_FACTICE_S = 0.2
DELAI_FACTICE_S = 0.5


#: Une sortie conforme au schéma de la route de réponse : la série 1, son fragment 702.
SORTIE_VALIDE = json.dumps(
    {
        "refus": False,
        "motif_refus": None,
        "reponse": "Une aventure de pirates, d'après une critique [S:1].",
        "series_citees": [{"serie_id": 1, "fragment_ids": [702]}],
    }
)
#: La sortie de la reformulation : la question d'essai, autonome.
REFORMULATION_VALIDE = json.dumps({"question": QUESTION})
#: Une ligne d'en-tête de passage : la série, et l'identifiant du passage s'il est
#: montré.
EN_TETE_DE_PASSAGE = re.compile(r"^\[S:(\d+)\] .*?(?: \[F:(\d+)\])?$", re.MULTILINE)


def sortie_lue_du_contexte(message: str) -> str:
    """La sortie d'un modèle qui suit l'invite : la première série du contexte, avec
    les identifiants de passages qu'il y lit — aucun s'il n'en montre pas."""
    lus: dict[int, list[int]] = {}
    for serie, fragment in EN_TETE_DE_PASSAGE.findall(message):
        lus.setdefault(int(serie), []).extend([int(fragment)] if fragment else [])
    serie_id, fragment_ids = next(iter(lus.items()))
    return json.dumps(
        {
            "refus": False,
            "motif_refus": None,
            "reponse": f"Une aventure de pirates [S:{serie_id}].",
            "series_citees": [{"serie_id": serie_id, "fragment_ids": fragment_ids}],
        }
    )


class DoublureFournisseur(BaseHTTPRequestHandler):
    """Un fournisseur simulé, sur le bouclage : `POST /v1/chat/completions` et
    `GET /v1/models`. Statut, corps, `usage`, modèle rendu, retard et envoi goutte à
    goutte réglables ; le contenu rendu dépend du schéma demandé (reformulation ou
    réponse), et la réponse peut être lue dans le contexte reçu
    (`lire_le_contexte`) ; chaque requête reçue est gardée (port, chemin, en-tête
    d'autorisation, corps)."""

    statut: ClassVar[int] = 200
    corps_brut: ClassVar[bytes | None] = None
    contenu: ClassVar[str] = SORTIE_VALIDE
    lire_le_contexte: ClassVar[bool] = False
    contenu_reformulation: ClassVar[str] = REFORMULATION_VALIDE
    usage: ClassVar[dict[str, Any] | None] = None
    modele_rendu: ClassVar[str | None] = None
    retard_s: ClassVar[float] = 0.0
    goutte_s: ClassVar[float] = 0.0
    statut_modeles: ClassVar[int] = 200
    modeles: ClassVar[list[str]] = []
    recus: ClassVar[list[dict[str, Any]]] = []

    def log_message(self, *args: object) -> None:
        pass

    def _noter(self, corps: object) -> None:
        DoublureFournisseur.recus.append(
            {
                "port": self.server.server_address[1],
                "chemin": self.path,
                "autorisation": self.headers.get("Authorization"),
                "corps": corps,
            }
        )

    def _envoyer(self, statut: int, donnees: bytes) -> None:
        self.send_response(statut)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        if not DoublureFournisseur.goutte_s:
            self.wfile.write(donnees)
            return
        quart = max(len(donnees) // 4, 1)
        for debut in range(0, len(donnees), quart):
            self.wfile.write(donnees[debut : debut + quart])
            self.wfile.flush()
            time.sleep(DoublureFournisseur.goutte_s)

    def do_GET(self) -> None:  # noqa: N802
        self._noter(None)
        if self.path != "/v1/models":
            self._envoyer(404, b"{}")
            return
        corps = {"object": "list", "data": [{"id": m} for m in self.modeles]}
        self._envoyer(DoublureFournisseur.statut_modeles, json.dumps(corps).encode())

    def do_POST(self) -> None:  # noqa: N802
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self._noter(corps)
        if DoublureFournisseur.retard_s:
            time.sleep(DoublureFournisseur.retard_s)
        if DoublureFournisseur.corps_brut is not None:
            self._envoyer(DoublureFournisseur.statut, DoublureFournisseur.corps_brut)
            return
        schema = corps.get("response_format", {}).get("json_schema", {}).get("schema")
        if isinstance(schema, dict) and schema.get("required") == ["question"]:
            contenu = DoublureFournisseur.contenu_reformulation
        elif DoublureFournisseur.lire_le_contexte:
            contenu = sortie_lue_du_contexte(corps["messages"][-1]["content"])
        else:
            contenu = DoublureFournisseur.contenu
        reponse: dict[str, Any] = {
            "id": "cmpl-essai",
            "model": DoublureFournisseur.modele_rendu or corps["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": contenu},
                    "finish_reason": "stop",
                }
            ],
        }
        if DoublureFournisseur.usage is not None:
            reponse["usage"] = DoublureFournisseur.usage
        self._envoyer(DoublureFournisseur.statut, json.dumps(reponse).encode())


@dataclass
class Doublures:
    """Deux fournisseurs simulés, à deux adresses distinctes."""

    serveurs: tuple[HTTPServer, HTTPServer]

    @property
    def port_a(self) -> int:
        return self.serveurs[0].server_address[1]

    @property
    def port_b(self) -> int:
        return self.serveurs[1].server_address[1]

    def recus(self, port: int | None = None) -> list[dict[str, Any]]:
        return [
            r for r in DoublureFournisseur.recus if port is None or r["port"] == port
        ]


@pytest.fixture
def doublures() -> Iterator[Doublures]:
    DoublureFournisseur.statut = 200
    DoublureFournisseur.corps_brut = None
    DoublureFournisseur.contenu = SORTIE_VALIDE
    DoublureFournisseur.lire_le_contexte = False
    DoublureFournisseur.contenu_reformulation = REFORMULATION_VALIDE
    DoublureFournisseur.usage = None
    DoublureFournisseur.modele_rendu = None
    DoublureFournisseur.retard_s = 0.0
    DoublureFournisseur.goutte_s = 0.0
    DoublureFournisseur.statut_modeles = 200
    DoublureFournisseur.modeles = []
    DoublureFournisseur.recus = []
    serveurs = (
        ServeurDoublure(("127.0.0.1", 0), DoublureFournisseur),
        ServeurDoublure(("127.0.0.1", 0), DoublureFournisseur),
    )
    for serveur in serveurs:
        threading.Thread(
            target=serveur.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()
    yield Doublures(serveurs)
    for serveur in serveurs:
        serveur.shutdown()
        serveur.server_close()


def profils_factices(port_a: int, port_b: int) -> str:
    """Deux profils factices : `factice-a` (forme Mistral) et `factice-b` (forme
    OpenAI, `temperature` déclarée absente), chacun sur sa doublure."""
    return f"""
[modele_langage.profils.factice-a]
fournisseur = "mistral"
modele = "modele-a"
adresse = "http://127.0.0.1:{port_a}/v1/chat/completions"
variable_cle = "CLE_FACTICE_A"
reglages_fixes = {{ top_p = 1.0 }}
reglages_absents = []
prix_usd_par_million = {{ entree = 2.0, sortie = 8.0 }}
delai_s = {DELAI_FACTICE_S}
statut = "stable"
motif = "Profil factice des tests, sur une doublure du bouclage."

[modele_langage.profils.factice-b]
fournisseur = "openai"
modele = "modele-b"
adresse = "http://127.0.0.1:{port_b}/v1/chat/completions"
variable_cle = "CLE_FACTICE_B"
reglages_fixes = {{ top_p = 1.0, reasoning_effort = "none" }}
reglages_absents = ["temperature"]
prix_usd_par_million = {{ entree = 1.0, sortie = 4.0 }}
delai_s = {DELAI_FACTICE_S}
statut = "apercu"
motif = "Profil factice des tests, sur une doublure du bouclage."
"""


def reglages_factices(tmp_path: Path, doublures: Doublures) -> Path:
    """Le fichier versionné, ses quatre profils remplacés par les deux factices
    (`factice-a` actif) et un délai de connexion court."""
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    texte = texte[: texte.index("[modele_langage.profils.")]
    for avant, apres in (
        ('profil_actif = "mistral-large-2512"', 'profil_actif = "factice-a"'),
        ("connexion_s = 5.0", f"connexion_s = {CONNEXION_FACTICE_S}"),
    ):
        assert texte.count(avant) == 1
        texte = texte.replace(avant, apres)
    fichier = tmp_path / "reglages_factices.toml"
    fichier.write_text(
        texte + profils_factices(doublures.port_a, doublures.port_b), encoding="utf-8"
    )
    return fichier


#: Un environnement complet pour `charger`, sans base : les tests de la couche.
ENVIRONNEMENT_FACTICE = {
    "API_MODELE_CLES": f"application:{CLE_APPLICATION}",
    "API_MODELE_DSN": "postgresql://modele_api@localhost/apimanga",
    **CLES_FACTICES,
}


@pytest.fixture
def api_reponse(
    base_api: BaseApi,
    pgpass: Path,
    service_doublure: ServiceDoublure,
    doublures: Doublures,
    horloge: Horloge,
    tmp_path: Path,
) -> Iterator[TestClient]:
    """L'API démarrée sur les deux profils factices (`factice-a` actif), chacun sur sa
    doublure : la route de réponse ne joint que le bouclage."""
    env = environnement(dsn_du_role(base_api.dsn_admin)) | CLES_FACTICES
    reglages = replace(
        charger(env, reglages_factices(tmp_path, doublures)), pool_attente_s=2.0
    )
    application = creer_application(
        reglages, instance=service_doublure.instance, horloge=horloge, environ=env
    )
    with TestClient(application, client=("127.0.0.1", 50000)) as client:
        yield client
