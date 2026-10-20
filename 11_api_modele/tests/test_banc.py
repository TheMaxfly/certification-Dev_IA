"""Le classement du banc (module 10), chargé par son chemin — procédé P2 de l'étape 2.

L'API réutilise le code du module 10 ; elle ne le recopie pas et ne l'installe pas.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import types
from pathlib import Path

import psycopg
import pytest
from conftest import RACINE_DEPOT

from api_modele import banc

ATTEIGNABILITE = (
    RACINE_DEPOT
    / "05_nettoyage_agregation_bdd"
    / "src"
    / "evaluation"
    / "atteignabilite.py"
)
FRAGMENTS = ((11, "ms_review:1"), (12, "kitsu:50"), (13, "kitsu:60"))


def source(objet) -> Path:
    fichier = inspect.getsourcefile(objet)
    assert fichier is not None
    return Path(fichier).resolve()


def test_le_paquet_du_10_vient_de_son_dossier():
    paquet = banc.charger_paquet()

    assert paquet.__file__ is not None
    assert Path(paquet.__file__).resolve() == (banc.PAQUET / "__init__.py").resolve()
    assert banc.charger_paquet() is paquet


def test_les_fonctions_du_classement_sont_celles_du_10():
    entites = banc.module("entites")
    recherche = banc.module("recherche")

    for fonction in (
        entites.charger,
        entites.construire,
        entites.Entites.agreger,
        entites.Entites.classer,
        recherche.classer,
        recherche.Semantique.charger,
        recherche.Semantique.scores,
    ):
        assert source(fonction).is_relative_to(banc.PAQUET.resolve())


def test_le_rattachement_est_celui_du_05():
    module = banc.module("entites").charger_atteignabilite()

    assert Path(module.__file__).resolve() == ATTEIGNABILITE.resolve()


def test_le_client_du_service_est_celui_du_09():
    import service_embedding.client

    attendu = RACINE_DEPOT / "09_service_embedding" / "src"
    assert source(service_embedding.client).is_relative_to(attendu.resolve())


def test_l_environnement_n_installe_ni_mlflow_ni_scikit_learn():
    """Le bénéfice du chargement par chemin : seul ce qui s'exécute est installé."""
    for nom in ("mlflow", "sklearn"):
        assert importlib.util.find_spec(nom) is None, f"{nom} est installé"


def test_un_autre_paquet_du_meme_nom_est_refuse(monkeypatch):
    faux = types.ModuleType(banc.NOM)
    faux.__file__ = "/ailleurs/mesures_recherche/__init__.py"
    monkeypatch.setitem(sys.modules, banc.NOM, faux)

    with pytest.raises(ImportError, match="déjà chargé"):
        banc.charger_paquet()


def test_le_rattachement_s_execute_sur_une_base_migree(base):
    """Une critique, un synopsis Kitsu rattaché par le moyeu, un autre non."""
    with psycopg.connect(base) as cx:
        for sid in (1, 2):
            cx.execute(
                "INSERT INTO manga.ms_series_enriched (series_id, series_title)"
                " VALUES (%s, %s)",
                (sid, f"Série {sid}"),
            )
        cx.execute(
            "INSERT INTO manga.work_identity (series_id, kitsu_id) VALUES (2, '50')"
        )
        cx.execute(
            "INSERT INTO bench.corpus_docs (doc_key, source, series_id, doc_text)"
            " VALUES ('ms_review:1', 'ms_review', 1, 'x')"
        )
        for kitsu in (50, 60):
            cx.execute(
                "INSERT INTO bench.corpus_docs (doc_key, source, kitsu_id, doc_text,"
                " title) VALUES (%s, 'kitsu_synopsis', %s, 'x', %s)",
                (f"kitsu:{kitsu}", kitsu, f"Kitsu {kitsu}"),
            )
        for chunk_id, doc_key in FRAGMENTS:
            cx.execute(
                "INSERT INTO bench.corpus_chunks (chunk_id, doc_key, chunk_index,"
                " chunk_text) VALUES (%s, %s, 0, 'texte')",
                (chunk_id, doc_key),
            )
        entites = banc.module("entites").charger(cx, "v1")

    assert [entites.cle(e) for e in range(len(entites))] == [
        "serie:1",
        "serie:2",
        "kitsu:60",
    ]
    assert entites.atteignables == frozenset({1, 2})
