"""Harnais : une base PostgreSQL + pgvector JETABLE, migrée par le runner du dépôt.

Garde-fou repris de `database/` et du module 05 : le DSN est fabriqué ici, à
partir du conteneur lancé par le harnais ; une DATABASE_URL de l'environnement
est ignorée, et l'absence de Docker fait sauter les tests qui ont besoin d'une
base — jamais de repli sur `apimanga`.
"""

from __future__ import annotations

import copy
import importlib
import importlib.util
import inspect
import pkgutil
import shutil
import sys
from pathlib import Path

import numpy as np
import psycopg
import pytest

import mesures_recherche
from mesures_recherche import configuration, enregistrement, executer, jeu

RACINE_DEPOT = Path(__file__).resolve().parents[2]
MIGRATIONS = RACINE_DEPOT / "database"


def _charger_harnais():
    """Le harnais partagé des bases jetables (`database/harnais_tests/`), par chemin."""
    spec = importlib.util.spec_from_file_location(
        "harnais_tests", MIGRATIONS / "harnais_tests" / "__init__.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["harnais_tests"] = module
    spec.loader.exec_module(module)
    return module


harnais = _charger_harnais()


@pytest.fixture(scope="session")
def conteneur() -> str:
    """Un PostgreSQL jetable pour toute la session."""
    raison = harnais.raison_de_sauter()
    if raison:
        pytest.skip(raison)
    with harnais.conteneur("mesures-test") as dsn:
        yield dsn


@pytest.fixture(scope="session")
def gabarit(conteneur) -> str:
    """La base gabarit, migrée une fois par session par le runner du dépôt."""
    return harnais.creer_gabarit(conteneur)


@pytest.fixture
def base(conteneur, gabarit) -> str:
    """Une base neuve par test, copiée du gabarit migré par le runner du dépôt."""
    with harnais.base_depuis_gabarit(conteneur, gabarit) as dsn:
        yield dsn


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


# --------------------------------------------------------------------------- #
#  Le magasin MLflow : initialisé une fois par session, copié pour chaque test
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def gabarit_mlflow(tmp_path_factory) -> Path:
    """Un magasin MLflow initialisé, une fois par session.

    Créer le schéma SQLite d'un magasin coûte près de 3 s ; l'utiliser ensuite, quelques
    centièmes. Chaque test en reçoit une copie (`stockage_mlflow`) : isolé, mais sans
    payer l'initialisation.
    """
    from mlflow.tracking import MlflowClient

    from mesures_recherche.enregistrement import uri_suivi

    dossier = tmp_path_factory.mktemp("gabarit_mlflow")
    MlflowClient(tracking_uri=uri_suivi(dossier)).search_experiments()
    return dossier / "mlflow.db"


@pytest.fixture
def stockage_mlflow(gabarit_mlflow, tmp_path) -> Path:
    """Un magasin MLflow propre au test, copié du gabarit de la session."""
    dossier = tmp_path / "mlflow"
    dossier.mkdir()
    shutil.copyfile(gabarit_mlflow, dossier / "mlflow.db")
    return dossier


# --------------------------------------------------------------------------- #
#  Les commandes (`main`) : ce qu'elles prennent par défaut, remplacé
# --------------------------------------------------------------------------- #

#: L'état git que relève l'enregistrement, dans les tests des commandes : un arbre
#: propre, à un commit fixé — le test ne dépend pas de l'état du dépôt où il tourne.
ARBRE_PROPRE = {"commit": "c" * 40, "arbre_module_propre": "True"}


def modules_du_paquet() -> list:
    """Tous les modules de `mesures_recherche`, importés."""
    return [
        importlib.import_module(f"mesures_recherche.{m.name}")
        for m in pkgutil.iter_modules(mesures_recherche.__path__)
    ]


@pytest.fixture
def magasin_par_defaut(stockage_mlflow, monkeypatch) -> Path:
    """Le magasin MLflow du test, à la place de celui du module (`mlflow/`), partout où
    une commande le prend par défaut : la constante `STOCKAGE` de chaque module, et
    les valeurs par défaut des fonctions, figées à leur définition (`uri_suivi()`,
    `enregistrer`, `promotion.comparer`, `journal_promotions.decider`…). Une commande
    lancée dans le test n'écrit ni ne lit jamais le magasin du module."""
    reel = enregistrement.STOCKAGE

    def remplacer(valeur):
        return stockage_mlflow if valeur == reel else valeur

    for module in modules_du_paquet():
        if getattr(module, "STOCKAGE", None) == reel:
            monkeypatch.setattr(module, "STOCKAGE", stockage_mlflow)
        for f in vars(module).values():
            if not inspect.isfunction(f) or f.__module__ != module.__name__:
                continue
            if f.__defaults__ and reel in f.__defaults__:
                monkeypatch.setattr(
                    f, "__defaults__", tuple(map(remplacer, f.__defaults__))
                )
            if f.__kwdefaults__ and reel in f.__kwdefaults__.values():
                monkeypatch.setattr(
                    f,
                    "__kwdefaults__",
                    {k: remplacer(v) for k, v in f.__kwdefaults__.items()},
                )
    return stockage_mlflow


@pytest.fixture
def arbre_propre(monkeypatch) -> dict[str, str]:
    """L'état git relevé par l'enregistrement : `ARBRE_PROPRE`."""
    monkeypatch.setattr(enregistrement, "etat_git", lambda: dict(ARBRE_PROPRE))
    return ARBRE_PROPRE


@pytest.fixture
def commande(banc, magasin_par_defaut, arbre_propre, monkeypatch):
    """Ce que voit une commande (`main`) lancée dans le test : DATABASE_URL = la base
    jetable, la configuration du mini-jeu à la place de la versionnée, le magasin
    MLflow du test, un arbre propre. Rend (dsn, config, magasin)."""
    dsn, config, _ = banc
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setattr(
        configuration, "charger", lambda chemin=None: copy.deepcopy(config)
    )
    return dsn, config, magasin_par_defaut
