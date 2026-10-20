"""Les commandes du banc (`main`), lancées dans le processus du test.

Chaque commande lit DATABASE_URL, la configuration versionnée, le magasin MLflow du
module et l'état git : ici, la base jetable, la configuration du mini-jeu, le
magasin du test et un arbre propre (fixture `commande`, dans `conftest.py`). On
vérifie ce qu'elles laissent — code de sortie, fichier écrit, lignes en base, runs
MLflow — et ce qu'elles refusent. Le service d'embedding et le moteur local
(Ollama) sont des doublures : rien ne sort du processus.
"""

from __future__ import annotations

import copy
import importlib
import inspect
import json
import runpy
import sys
import time
import uuid
import warnings
from types import SimpleNamespace

import numpy as np
import psycopg
import pytest
from conftest import ARBRE_PROPRE, modules_du_paquet
from mlflow.tracking import MlflowClient
from service_embedding import mesures as mesures_service
from test_generation import passages_factices
from test_historique import mesurer as inscrire_des_mesures
from test_historique import run_mlflow
from test_promotion import HEAD, comparaison  # noqa: F401 — la fixture des deux runs

from mesures_recherche import (
    comparaisons,
    configuration,
    diagnostic,
    enregistrement,
    etudes,
    executer,
    generation,
    historique,
    journal_promotions,
    promotion,
)
from mesures_recherche.enregistrement import uri_suivi


def client(magasin) -> MlflowClient:
    return MlflowClient(tracking_uri=uri_suivi(magasin))


def lire(chemin) -> dict | list:
    return json.loads(chemin.read_text(encoding="utf-8"))


def runs_de_mesure(dsn) -> int:
    with psycopg.connect(dsn) as cx:
        return cx.execute("SELECT count(*) FROM bench.eval_runs").fetchone()[0]


# --------------------------------------------------------------------------- #
#  Ce que toutes les commandes partagent
# --------------------------------------------------------------------------- #


def test_aucun_defaut_ne_pointe_plus_sur_le_magasin_du_module(magasin_par_defaut):
    """Le garde-fou des tests eux-mêmes : sous `magasin_par_defaut`, aucune fonction
    du paquet ne prend plus le magasin du module par défaut."""
    reel = configuration.RACINE_MODULE / "mlflow"
    for module in modules_du_paquet():
        assert getattr(module, "STOCKAGE", None) != reel, module.__name__
        for f in vars(module).values():
            if inspect.isfunction(f):
                for p in inspect.signature(f).parameters.values():
                    assert p.default != reel, f"{module.__name__}.{f.__name__}"
    for f in (
        enregistrement.uri_suivi,
        enregistrement.enregistrer,
        historique.inscrire,
        promotion.parametres_du_run,
        promotion.resultat_du_run,
        promotion.comparer,
        journal_promotions.decider,
    ):
        defaut = inspect.signature(f).parameters["stockage"].default
        assert defaut == magasin_par_defaut, f.__name__
    assert enregistrement.uri_suivi() == uri_suivi(magasin_par_defaut)


SANS_BASE = [
    pytest.param(executer, ["3", "--sortie", "{sortie}"], id="executer"),
    pytest.param(
        executer,
        ["3", "--sortie", "{sortie}", "--enregistrer"],
        id="executer-enregistrer",
    ),
    pytest.param(diagnostic, ["3", "--sortie", "{sortie}"], id="diagnostic"),
    pytest.param(historique, ["--corpus", "v1"], id="historique"),
    pytest.param(
        promotion, ["--run", "0" * 32, "--sortie", "{sortie}"], id="promotion"
    ),
    pytest.param(journal_promotions, ["etat"], id="journal_promotions"),
    pytest.param(etudes, ["tfidf", "--sortie", "{sortie}"], id="etudes-tfidf"),
    pytest.param(
        etudes,
        ["tfidf-une", "--combinaison", "{{}}", "--sortie", "{sortie}"],
        id="etudes-tfidf-une",
    ),
    pytest.param(etudes, ["voisins", "--sortie", "{sortie}"], id="etudes-voisins"),
    pytest.param(
        etudes,
        ["intention-voisins", "--sortie", "{sortie}"],
        id="etudes-intention",
    ),
    pytest.param(
        generation, ["passages", "--sortie", "{sortie}"], id="generation-passages"
    ),
]


@pytest.mark.parametrize("module, argv", SANS_BASE)
def test_sans_database_url_la_commande_s_arrete(module, argv, monkeypatch, tmp_path):
    """Jamais de base par défaut : sans DATABASE_URL, arrêt, et rien d'écrit."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sortie = tmp_path / "sortie.json"
    with pytest.raises(SystemExit, match="DATABASE_URL absente"):
        module.main([a.format(sortie=sortie) for a in argv])
    assert not sortie.exists()


COMMANDES = [
    "comparaisons",
    "diagnostic",
    "etudes",
    "executer",
    "generation",
    "historique",
    "journal_promotions",
    "promotion",
]


@pytest.mark.parametrize("nom", COMMANDES)
def test_chaque_module_se_lance_en_commande(nom, monkeypatch, capsys):
    """`python -m mesures_recherche.<nom>` passe la main à `main` et sort de son
    code : ici, l'aide, qui porte la première ligne de la documentation."""
    monkeypatch.setattr(sys, "argv", [nom, "--help"])
    monkeypatch.setenv("COLUMNS", "200")
    with warnings.catch_warnings():
        # runpy prévient que le module est déjà importé : voulu ici.
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(SystemExit) as fin:
            runpy.run_module(f"mesures_recherche.{nom}", run_name="__main__")
    assert fin.value.code == 0
    doc = importlib.import_module(f"mesures_recherche.{nom}").__doc__
    assert doc.splitlines()[0] in capsys.readouterr().out


# --------------------------------------------------------------------------- #
#  executer — à blanc, rejeu, enregistrement, arbre sale
# --------------------------------------------------------------------------- #


def test_executer_a_blanc_n_ecrit_que_la_sortie(commande, tmp_path, capsys):
    dsn, config, magasin = commande
    sortie = tmp_path / "resultats" / "mesure_3.json"
    assert executer.main(["3", "--sortie", str(sortie)]) == 0
    r = lire(sortie)
    assert (r["mesure"], r["corpus_id"], r["encodage_id"]) == (3, "v1", None)
    assert r["experience"] == config["mlflow"]["experience"] and "run_id" not in r
    assert r["duree_totale_s"] >= 0
    affiche = json.loads(capsys.readouterr().out)
    assert affiche["global"] == {
        f"{a['metrique']}@{a['k']}/{a['perimetre']}": round(a["valeur"], 4)
        for a in r["agregats"]
        if a["portee"] == "global"
    }
    assert affiche["global"]["hit_rate@10/toutes"] == 1.0
    assert affiche["run_id"] is None and affiche["rejeu"] is None
    # À blanc : ni run MLflow, ni ligne en base.
    assert client(magasin).get_experiment_by_name(r["experience"]) is None
    assert runs_de_mesure(dsn) == 0


def test_executer_rejeu_identique_puis_different(commande, tmp_path):
    reference, rejeu = tmp_path / "reference.json", tmp_path / "rejeu.json"
    assert executer.main(["3", "--sortie", str(reference)]) == 0
    argv = ["3", "--sortie", str(rejeu), "--rejeu-de", str(reference)]
    assert executer.main(argv) == 0
    assert lire(rejeu)["rejeu"] == {"reference": str(reference), "identiques": True}
    modifiee = lire(reference)
    modifiee["agregats"][0]["valeur"] += 0.5
    reference.write_text(json.dumps(modifiee), encoding="utf-8")
    assert executer.main(argv) == 1, "des métriques différentes : code 1"
    assert lire(rejeu)["rejeu"]["identiques"] is False


def test_executer_enregistre_dans_le_magasin_et_la_base(
    commande, encodeur_doublure, tmp_path, capsys
):
    """Une autre expérience et une autre étiquette « spec » que celles de la
    configuration : la mesure, elle, ne change pas."""
    dsn, _, magasin = commande
    sortie = tmp_path / "mesure_1.json"
    argv = ["1", "--sortie", str(sortie), "--enregistrer"]
    assert executer.main([*argv, "--experience", "essai", "--spec", "E3 essai"]) == 0
    r = lire(sortie)
    assert json.loads(capsys.readouterr().out)["run_id"] == r["run_id"]
    run = client(magasin).get_run(r["run_id"])
    assert run.info.status == "FINISHED"
    assert client(magasin).get_experiment(run.info.experiment_id).name == "essai"
    assert (run.data.tags["mesure"], run.data.tags["spec"]) == ("1", "E3 essai")
    assert run.data.params["commit"] == ARBRE_PROPRE["commit"]
    assert run.data.params["encodage_id"] == str(r["encodage_id"])
    with psycopg.connect(dsn) as cx:
        assert cx.execute(
            "SELECT corpus_id, encodage_id FROM bench.eval_runs WHERE run_id = %s",
            (uuid.UUID(r["run_id"]),),
        ).fetchall() == [("v1", r["encodage_id"])]
        (n,) = cx.execute(
            "SELECT count(*) FROM bench.eval_mesures WHERE run_id = %s",
            (uuid.UUID(r["run_id"]),),
        ).fetchone()
    assert n == len(r["agregats"])


def test_executer_arbre_sale_refuse_avant_de_mesurer(commande, tmp_path, monkeypatch):
    """On commite d'abord, on mesure ensuite : `--enregistrer` sur un arbre sale est
    refusé AVANT d'ouvrir la base — aucune mesure, aucun fichier, aucun run."""
    dsn, config, magasin = commande
    monkeypatch.setattr(
        enregistrement,
        "etat_git",
        lambda: ARBRE_PROPRE | {"arbre_module_propre": "False"},
    )
    ouvertures = []
    monkeypatch.setattr(executer, "ouvrir", lambda *a, **k: ouvertures.append(a))
    sortie = tmp_path / "mesure_3.json"
    with pytest.raises(enregistrement.EnregistrementRefuse, match="non propre"):
        executer.main(["3", "--sortie", str(sortie), "--enregistrer"])
    assert ouvertures == [], "refusé avant toute lecture de la base"
    assert not sortie.exists()
    nom = config["mlflow"]["experience"]
    assert client(magasin).get_experiment_by_name(nom) is None
    assert runs_de_mesure(dsn) == 0


def test_executer_un_encodage_inconnu_arrete(commande, tmp_path):
    sortie = tmp_path / "mesure_1.json"
    with pytest.raises(RuntimeError, match="encodage 999 inconnu du registre"):
        executer.main(["1", "--sortie", str(sortie), "--encodage", "999"])
    assert not sortie.exists()


# --------------------------------------------------------------------------- #
#  diagnostic — à blanc, puis confronté au run enregistré
# --------------------------------------------------------------------------- #


def test_diagnostic_sans_verification_avec_lecture(
    commande, encodeur_doublure, tmp_path, capsys
):
    _, config, _ = commande
    sortie = tmp_path / "diagnostic" / "d.json"
    argv = ["3", "1", "--sortie", str(sortie), "--sans-verification"]
    assert diagnostic.main([*argv, "--lecture", "F4"]) == 0
    d = lire(sortie)
    assert d["jeu_empreinte"] == config["jeu"]["empreinte"]
    assert [m["mesure"] for m in d["mesures"]] == [3, 1]
    assert all(m["verifie_contre_run"] is None for m in d["mesures"])
    for m in d["mesures"]:
        assert {q["question_id"] for q in m["questions"]} == {"Q001", "Q002"}
        (lu,) = m["lecture"]
        assert lu["question_id"] == "Q002" and lu["top"][0]["entite"] == "serie:3"
    lignes = capsys.readouterr().out.splitlines()
    assert len(lignes) == 2 and all("(non vérifiée)" in x for x in lignes)


def test_diagnostic_confronte_au_run_de_la_commande_executer(
    commande, tmp_path, capsys
):
    """La mesure enregistrée par sa commande, puis le diagnostic vérifié contre ce
    run (pièce `resultat.json` du magasin)."""
    mesure = tmp_path / "mesure_3.json"
    assert executer.main(["3", "--sortie", str(mesure), "--enregistrer"]) == 0
    run_id = lire(mesure)["run_id"]
    sortie = tmp_path / "d.json"
    assert diagnostic.main(["3", "--sortie", str(sortie)]) == 0
    (m,) = lire(sortie)["mesures"]
    assert m["verifie_contre_run"] == run_id
    assert m["portees"]["global"]["n"] == 2
    assert f"mesure 3 ({run_id})" in capsys.readouterr().out


def test_diagnostic_sans_run_de_reference_arrete(commande, tmp_path):
    sortie = tmp_path / "d.json"
    with pytest.raises(RuntimeError, match="mesure 3 : 0 run"):
        diagnostic.main(["3", "--sortie", str(sortie)])
    assert not sortie.exists()


# --------------------------------------------------------------------------- #
#  comparaisons — les huit comparaisons déclarées, sur des résultats écrits
# --------------------------------------------------------------------------- #


def resultat_ecrit(numero: int, reussites: list[float]) -> dict:
    """Six questions de rang, et un refus (sans métriques, hors comparaison)."""
    questions = [
        {
            "question_id": f"Q{i:03d}",
            "metriques": {"hit_rate@10": v, "ndcg@10": v / 2},
        }
        for i, v in enumerate(reussites, start=1)
    ]
    return {
        "mesure": numero,
        "questions": questions + [{"question_id": "Q099", "metriques": None}],
    }


def test_comparaisons_les_comparaisons_declarees(tmp_path, capsys):
    """La mesure 1 réussit tout, la 2 rien, les autres une sur deux : seul « 1 contre
    2 » est une différence établie."""
    config = configuration.charger()
    declarees = config["bootstrap"]["comparaisons"]
    fichiers = []
    for n in sorted({n for paire in declarees for n in paire}):
        reussites = {1: [1.0] * 6, 2: [0.0] * 6}.get(n, [1.0, 0.0] * 3)
        f = tmp_path / f"mesure_{n}.json"
        f.write_text(json.dumps(resultat_ecrit(n, reussites)), encoding="utf-8")
        fichiers.append(str(f))
    sortie = tmp_path / "comparaisons.json"
    assert comparaisons.main([*fichiers, "--sortie", str(sortie)]) == 0
    lignes = lire(sortie)
    metriques = config["bootstrap"]["metriques"]
    assert [(x["comparaison"], x["metrique"]) for x in lignes] == [
        (f"{a} contre {b}", m) for a, b in declarees for m in metriques
    ]
    m = len(declarees)
    for x in lignes:
        assert x["n_questions"] == 6, "le refus n'entre pas dans la comparaison"
        assert x["niveau"] == pytest.approx(1 - config["bootstrap"]["alpha"] / m)
        assert x["seuil"] == pytest.approx(config["bootstrap"]["alpha"] / m)
    un_contre_deux = [x for x in lignes if x["comparaison"] == "1 contre 2"]
    assert {x["verdict"] for x in un_contre_deux} == {"différence"}
    assert un_contre_deux[0]["ecart"] == 1.0
    identiques = [x for x in lignes if x["comparaison"] == "6 contre 3"]
    assert all(x["ecart"] == 0.0 and x["verdict"] == "non établie" for x in identiques)
    assert len(capsys.readouterr().out.splitlines()) == len(lignes)


# --------------------------------------------------------------------------- #
#  historique, promotion, journal des promotions
# --------------------------------------------------------------------------- #


def test_historique_inscrit_par_la_commande_une_fois(commande, capsys):
    dsn, _, magasin = commande
    with psycopg.connect(dsn) as cx:
        (eid,) = cx.execute("SELECT encodage_id FROM bench.encodages").fetchone()
    run_id = run_mlflow(magasin, "8", {"encodage_id": str(eid)})
    inscrire_des_mesures(dsn, run_id)
    assert historique.main(["--corpus", "v1"]) == 0
    (ligne,) = json.loads(capsys.readouterr().out)
    assert ligne["run_id"] == str(uuid.UUID(run_id))
    assert (ligne["corpus_id"], ligne["encodage_id"]) == ("v1", eid)
    assert ligne["note"].startswith("mesure 8 — inscrit après coup")
    assert historique.main(["--corpus", "v1"]) == 0
    assert json.loads(capsys.readouterr().out) == [], "une fois seulement"
    assert runs_de_mesure(dsn) == 1


def test_promotion_la_commande_rend_le_verdict(
    comparaison,  # noqa: F811
    commande,
    monkeypatch,
    tmp_path,
    capsys,
):
    _, _, regle, _, nouveau, _, e2 = comparaison
    monkeypatch.setattr(promotion, "charger_regle", lambda chemin=None: regle)
    sortie = tmp_path / "promotion" / "comparaison.json"
    assert promotion.main(["--run", nouveau, "--sortie", str(sortie)]) == 0
    c = lire(sortie)
    assert c["verdict"] == "promotion proposée" and c["regle_commit"] == HEAD
    assert c["run"]["encodage_id"] == str(e2)
    affiche = json.loads(capsys.readouterr().out)
    assert set(affiche) == {"verdict", "nouveau", "hypothese"}
    assert affiche["nouveau"]["global"] == 3


def test_journal_la_commande_promouvoir_suit_la_regle(
    comparaison,  # noqa: F811
    commande,
    monkeypatch,
    capsys,
):
    dsn, _, regle, _, nouveau, e1, e2 = comparaison
    monkeypatch.setattr(promotion, "charger_regle", lambda chemin=None: regle)
    argv = ["--encodage", str(e2), "--run", nouveau, "--motif", "essai", "--par", "Max"]
    with pytest.raises(journal_promotions.DecisionRefusee, match="dérogation"):
        journal_promotions.main(["refuser", *argv])
    assert journal_promotions.main(["promouvoir", *argv]) == 0
    r = json.loads(capsys.readouterr().out)
    assert (r["verdict"], r["ligne"]["decision"]) == ("promotion proposée", "promu")
    assert r["avant"]["encodage_id"] == e1 and r["apres"]["encodage_id"] == e2
    with psycopg.connect(dsn) as cx:
        assert cx.execute("SELECT count(*) FROM bench.promotions").fetchone()[0] == 2


# --------------------------------------------------------------------------- #
#  etudes — la grille du TF-IDF, les voisins, l'intention
# --------------------------------------------------------------------------- #


@pytest.fixture
def etudes_du_banc(monkeypatch) -> dict:
    """`etudes.toml`, réduit au banc : deux combinaisons TF-IDF (mots, sublinéaire,
    min_df 1 et 2 — le témoin), les voisins et l'intention sur BGE-M3 (la table du
    banc)."""
    e = copy.deepcopy(etudes.charger())
    t = e["tfidf"]
    t["unites"] = [u for u in t["unites"] if u["nom"] == "mots"]
    t["sublinear_tf"], t["min_df"], t["max_df"] = [True], [1, 2], [1.0]
    e["voisins"]["mesure"] = 7
    e["intention"]["voisins"]["instance"] = "bge-m3"
    monkeypatch.setattr(etudes, "charger", lambda chemin=None: copy.deepcopy(e))
    return e


@pytest.fixture
def garde_dans_le_processus(monkeypatch) -> list[dict]:
    """La garde de mémoire, sans processus enfant : la commande `tfidf-une` qu'elle
    lancerait tourne ici, par `main` — la base, la configuration et les doublures du
    test restent en place. Rend les combinaisons lancées."""
    lancees = []

    def garde(commande, ram_min, swap_continu_s, **_):
        assert commande[:4] == [
            sys.executable,
            "-m",
            "mesures_recherche.etudes",
            "tfidf-une",
        ]
        lancees.append(json.loads(commande[commande.index("--combinaison") + 1]))
        debut = time.monotonic()
        code = etudes.main(commande[3:])
        return {
            "code": code,
            "motif": None,
            "erreur": None,
            "duree_s": time.monotonic() - debut,
            "ram_disponible_min": 10**10,
        }

    monkeypatch.setattr(etudes, "sous_garde", garde)
    monkeypatch.setattr(etudes, "ram_disponible", lambda: 10**10)
    return lancees


def test_etudes_tfidf_la_grille_enregistree(
    commande, etudes_du_banc, garde_dans_le_processus, tmp_path
):
    _, _, magasin = commande
    sortie = tmp_path / "etudes" / "tfidf.json"
    assert etudes.main(["tfidf", "--sortie", str(sortie), "--enregistrer"]) == 0
    s = lire(sortie)
    grille = etudes.combinaisons(etudes_du_banc)
    assert garde_dans_le_processus == grille
    assert [r["combinaison"] for r in s["resultats"]] == grille
    assert all(r["vocabulaire"] > 0 and r["agregats"] for r in s["resultats"])
    assert s["resume"]["sautees"] == []
    assert set(s["resume"]["reglages"]["min_df"]) == {"1", "2"}
    assert s["etudes_empreinte"] == configuration.empreinte(etudes.FICHIER)
    parent = client(magasin).get_run(s["run_parent"])
    assert parent.info.status == "FINISHED"
    assert parent.data.params["combinaisons"] == "2"
    assert parent.data.params["commit"] == ARBRE_PROPRE["commit"]
    enfants = client(magasin).search_runs(
        [parent.info.experiment_id],
        f"tags.mlflow.parentRunId = '{parent.info.run_id}'",
    )
    assert sorted(r.info.run_name for r in enfants) == sorted(
        etudes.nom_combinaison(c) for c in grille
    )
    assert {r.info.status for r in enfants} == {"FINISHED"}


def test_etudes_tfidf_sous_le_seuil_la_combinaison_est_sautee(
    commande, etudes_du_banc, garde_dans_le_processus, monkeypatch, tmp_path
):
    """La seconde combinaison trouve la mémoire vive sous le seuil : elle n'est pas
    lancée, elle est nommée, l'étude continue — et sans `--enregistrer`, aucun run."""
    _, _, magasin = commande
    memoire = iter([10**10, 10**9])
    monkeypatch.setattr(etudes, "ram_disponible", lambda: next(memoire))
    sortie = tmp_path / "tfidf.json"
    assert etudes.main(["tfidf", "--sortie", str(sortie)]) == 0
    s = lire(sortie)
    premiere, seconde = etudes.combinaisons(etudes_du_banc)
    assert garde_dans_le_processus == [premiere]
    assert s["resultats"][1] == {
        "combinaison": seconde,
        "motif": "mémoire vive sous le seuil avant le lancement",
    }
    assert [x["combinaison"] for x in s["resume"]["sautees"]] == [
        etudes.nom_combinaison(seconde)
    ]
    assert "run_parent" not in s
    nom = etudes_du_banc["mlflow"]["experience"]
    assert client(magasin).get_experiment_by_name(nom) is None


def test_etudes_tfidf_un_processus_en_echec_arrete(
    commande, etudes_du_banc, monkeypatch, tmp_path
):
    monkeypatch.setattr(etudes, "ram_disponible", lambda: 10**10)
    monkeypatch.setattr(
        etudes,
        "sous_garde",
        lambda commande, *a, **k: {
            "code": 1,
            "motif": None,
            "erreur": "Traceback : boum",
            "duree_s": 0.1,
            "ram_disponible_min": 10**10,
        },
    )
    sortie = tmp_path / "tfidf.json"
    with pytest.raises(RuntimeError, match="échec — Traceback : boum"):
        etudes.main(["tfidf", "--sortie", str(sortie)])
    assert not sortie.exists()


def test_etudes_voisins_enregistre(
    commande, etudes_du_banc, encodeur_doublure, tmp_path, capsys
):
    _, _, magasin = commande
    sortie = tmp_path / "etudes" / "voisins.json"
    assert etudes.main(["voisins", "--sortie", str(sortie), "--enregistrer"]) == 0
    s = lire(sortie)
    assert s["mesure"] == 7 and s["perimetre_entites"] == "catalogue"
    assert s["distances"] == {"identiques": 3, "questions": 3, "differentes": []}
    assert s["hit_rate"]["global"]["1"] == 1.0
    assert len(s["code_empreinte"]) == 64
    run = client(magasin).get_run(s["run"])
    assert run.data.tags["etude"] == "voisins"
    assert run.data.params["commit"] == ARBRE_PROPRE["commit"]
    assert run.data.params["service_version"] == "doublure"
    assert json.loads(capsys.readouterr().out)["run"] == s["run"]


def test_etudes_intention_voisins_un_run_par_k(
    commande, etudes_du_banc, encodeur_doublure, tmp_path, capsys
):
    _, _, magasin = commande
    sortie = tmp_path / "etudes" / "intention_voisins.json"
    argv = ["intention-voisins", "--sortie", str(sortie), "--enregistrer"]
    assert etudes.main(argv) == 0
    s = lire(sortie)
    ks = etudes_du_banc["intention"]["voisins"]["k"]
    assert [r["k"] for r in s["runs"]] == ks
    assert all(r["questions"] == 3 for r in s["runs"])
    assert s["parametres"]["service_service_version"] == "doublure"
    assert s["parametres"]["commit"] == ARBRE_PROPRE["commit"]
    for r in s["runs"]:
        run = client(magasin).get_run(r["run"])
        assert run.data.tags["etude"] == "intention"
        assert run.data.params["k"] == str(r["k"])
        assert run.data.metrics["exactitude"] == pytest.approx(r["exactitude"])
    nom = client(magasin).get_experiment(run.info.experiment_id).name
    assert nom == etudes_du_banc["intention"]["experience"]
    assert len(capsys.readouterr().out.splitlines()) == len(ks)


class MoteurDoublure:
    """Le moteur local (Ollama), sans réseau : une réponse par question selon un mot
    de son texte, la charge déclarée, les déchargements comptés."""

    en_memoire_video = 5
    reponses = {"Zolthar": '{"mode": "refus"}', "carnet": "je ne sais pas"}

    def __init__(self, url, modele, keep_alive):
        self.modele = modele
        self.appels: list = []
        self.dechargements = 0

    def description(self) -> dict:
        return {
            "ollama_version": "0.0-doublure",
            "modele_digest": "d" * 12,
            "modele_quantification": "Q4_K_M",
            "modele_taille_parametres": "3B",
        }

    def discuter(self, msgs, opts, format=None) -> dict:
        self.appels.append((msgs, opts, format))
        texte = msgs[-1]["content"]
        reponse = next(
            (r for mot, r in self.reponses.items() if mot in texte),
            '{"mode": "proposition"}',
        )
        return {
            "reponse": reponse,
            "done_reason": "stop",
            "jetons_entree": 50,
            "jetons_sortie": 6,
            "latence_s": 0.1,
            "duree_totale_s": 0.1,
            "duree_chargement_s": 0.0,
        }

    def charge(self) -> dict:
        return {
            "taille": 5,
            "taille_memoire_video": self.en_memoire_video,
            "contexte": 2048,
        }

    def decharger(self) -> None:
        self.dechargements += 1


@pytest.fixture
def moteur_local(monkeypatch):
    """`generation.Ollama` remplacé par la doublure, et la mémoire vidéo lue par une
    doublure de `nvidia-smi` (1 Gio occupé). Rend la classe et les moteurs créés."""
    crees = []

    class Moteur(MoteurDoublure):
        def __init__(self, *args):
            super().__init__(*args)
            crees.append(self)

    monkeypatch.setattr(generation, "Ollama", Moteur)
    monkeypatch.setattr(
        mesures_service,
        "gpu",
        lambda: {"total": 8 * 2**30, "utilisee": 2**30, "libre": 7 * 2**30},
    )
    return SimpleNamespace(classe=Moteur, crees=crees)


@pytest.mark.parametrize("enregistrer", [False, True], ids=["a-blanc", "enregistre"])
def test_etudes_intention_llm(
    commande, etudes_du_banc, moteur_local, tmp_path, capsys, enregistrer
):
    """Une réponse hors des trois valeurs (Q002) est comptée, et comptée fausse ; le
    modèle est déchargé à la fin."""
    _, _, magasin = commande
    sortie = tmp_path / "etudes" / "intention_llm.json"
    argv = ["intention-llm", "--sortie", str(sortie)]
    assert etudes.main(argv + ["--enregistrer"] * enregistrer) == 0
    s = lire(sortie)
    (moteur,) = moteur_local.crees
    assert moteur.dechargements == 1
    chargement, *questions = moteur.appels
    assert chargement[1]["num_predict"] == 1 and chargement[2] is None
    assert [f for _, _, f in questions] == [etudes.SCHEMA_MODE] * 3
    (r,) = s["runs"]
    assert r["predictions"] == {"Q001": "proposition", "Q002": None, "Q003": "refus"}
    assert r["exactitude"] == pytest.approx(2 / 3) and r["hors_valeurs"] == 1
    assert s["charge"]["vram_occupee"] == 2**30
    llm = etudes_du_banc["intention"]["llm"]
    p = s["parametres"]
    assert p["invite_empreinte"] == generation.empreinte(
        etudes.RACINE_MODULE / llm["invite"]
    )
    assert p["llm_modele"] == llm["modele"] and "llm_url" not in p
    assert (p["charge_taille"], p["ollama_version"]) == ("5", "0.0-doublure")
    assert json.loads(capsys.readouterr().out)["run"] == r.get("run")
    if not enregistrer:
        assert "run" not in r
        nom = etudes_du_banc["intention"]["experience"]
        assert client(magasin).get_experiment_by_name(nom) is None
        return
    run = client(magasin).get_run(r["run"])
    assert run.data.tags["methode"] == "llm"
    assert run.data.metrics["hors_valeurs"] == 1
    assert run.data.metrics["latence_mediane_s"] == pytest.approx(0.1)
    pieces = {a.path for a in client(magasin).list_artifacts(r["run"])}
    assert {"appels.json", "invite_intention.toml", "predictions.json"} <= pieces


def test_etudes_intention_llm_modele_pas_entier_sur_la_carte(
    commande, etudes_du_banc, moteur_local, monkeypatch, tmp_path
):
    monkeypatch.setattr(moteur_local.classe, "en_memoire_video", 3)
    sortie = tmp_path / "intention_llm.json"
    with pytest.raises(SystemExit, match="pas entièrement sur la carte"):
        etudes.main(["intention-llm", "--sortie", str(sortie)])
    (moteur,) = moteur_local.crees
    assert moteur.dechargements == 1 and len(moteur.appels) == 1
    assert not sortie.exists()


# --------------------------------------------------------------------------- #
#  generation — passages, puis génération
# --------------------------------------------------------------------------- #


@pytest.fixture
def reglages_du_banc(tmp_path, monkeypatch):
    """`generation.toml`, réduit au banc : les passages de la mesure 1 (BGE-M3, la
    table du banc), la seule configuration « base »."""
    texte = generation.REGLAGES.read_text(encoding="utf-8")
    assert texte.count("\nmesure = 2\n") == 1
    texte = texte.replace("\nmesure = 2\n", "\nmesure = 1\n")
    tete, base, *_ = texte.split("\n[[configurations]]")
    chemin = tmp_path / "reglages" / "generation.toml"
    chemin.parent.mkdir()
    chemin.write_text(tete + "\n[[configurations]]" + base, encoding="utf-8")
    monkeypatch.setattr(generation, "REGLAGES", chemin)
    reglages = generation.charger_toml(chemin)
    assert reglages["passages"]["mesure"] == 1
    assert [c["nom"] for c in reglages["configurations"]] == ["base"]
    return reglages


def test_generation_passages_confrontes_au_run_de_la_mesure(
    commande, encodeur_doublure, reglages_du_banc, tmp_path, capsys
):
    mesure = tmp_path / "mesure_1.json"
    assert executer.main(["1", "--sortie", str(mesure), "--enregistrer"]) == 0
    sortie = tmp_path / "generation" / "passages.json"
    assert generation.main(["passages", "--sortie", str(sortie)]) == 0
    passages = lire(sortie)
    # La première question de chaque famille : F1, F4, F7.
    assert [q["question_id"] for q in passages] == ["Q001", "Q002", "Q003"]
    du_run = lire(mesure)["questions"]
    tops = {q["question_id"]: [c for c, _ in q["top"]] for q in du_run}
    for q in passages:
        assert [p["entite"] for p in q["passages"]] == tops[q["question_id"]]
        assert all(p["texte"] for p in q["passages"])
    affiche = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert affiche == {
        "questions": ["Q001", "Q002", "Q003"],
        "passages_par_question": reglages_du_banc["passages"]["nombre"],
        "confrontes_a_la_mesure": 1,
    }


def test_generation_passages_differents_de_la_mesure_arretent(
    commande, encodeur_doublure, reglages_du_banc, monkeypatch, tmp_path
):
    """Le service a changé depuis la mesure (toute question vise désormais le
    fragment 101) : les passages ne sont plus ceux du run, arrêt."""
    mesure = tmp_path / "mesure_1.json"
    assert executer.main(["1", "--sortie", str(mesure), "--enregistrer"]) == 0

    def autre_service(designation):
        def encoder(texte):
            v = np.zeros(1024)
            v[0] = 1.0
            return v

        return encoder, {"service_version": "autre"}

    monkeypatch.setattr(executer, "encodeur_du_service", autre_service)
    sortie = tmp_path / "passages.json"
    with pytest.raises(generation.ArretGeneration, match="Q001 : entités ≠ mesure 1"):
        generation.main(["passages", "--sortie", str(sortie)])
    assert not sortie.exists()


def test_generation_generer_enregistre(
    magasin_par_defaut, arbre_propre, moteur_local, reglages_du_banc, tmp_path, capsys
):
    passages = tmp_path / "passages.json"
    passages.write_text(json.dumps(passages_factices(2)), encoding="utf-8")
    sortie = tmp_path / "generation" / "generations.json"
    argv = ["generer", "--passages", str(passages), "--sortie", str(sortie)]
    assert generation.main([*argv, "--enregistrer"]) == 0
    g = lire(sortie)
    (moteur,) = moteur_local.crees
    assert moteur.dechargements == 1
    (base,) = reglages_du_banc["configurations"]
    (conf,) = g["configurations"]
    assert conf["configuration"] == base
    assert len(conf["generations"]) == 2 * base["repetitions"]
    assert conf["resume"]["vram_occupee_debut_mio"] == 1024.0
    assert g["charge"]["vram_occupee"] == 2**30
    contexte = g["contexte"]
    assert contexte["passages_empreinte"] == generation.empreinte(passages)
    assert contexte["reglages_empreinte"] == generation.empreinte(generation.REGLAGES)
    assert contexte["commit"] == arbre_propre["commit"]
    suivi = client(magasin_par_defaut)
    nom = reglages_du_banc["mlflow"]["experience"]
    (run,) = suivi.search_runs([suivi.get_experiment_by_name(nom).experiment_id])
    assert (run.info.run_name, run.info.status) == ("base", "FINISHED")
    assert run.data.params["passages_empreinte"] == contexte["passages_empreinte"]
    pieces = {a.path for a in suivi.list_artifacts(run.info.run_id)}
    assert {"reponses.json", "passages.json", "generation.toml"} <= pieces
    lignes = capsys.readouterr().out.splitlines()
    assert json.loads(lignes[0])["configuration"] == "base"
    assert json.loads(lignes[-1]) == {"charge": g["charge"]}


def test_generation_generer_modele_pas_entier_sur_la_carte_arrete(
    magasin_par_defaut,
    arbre_propre,
    moteur_local,
    reglages_du_banc,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(moteur_local.classe, "en_memoire_video", 3)
    passages = tmp_path / "passages.json"
    passages.write_text(json.dumps(passages_factices(1)), encoding="utf-8")
    sortie = tmp_path / "generations.json"
    argv = ["generer", "--passages", str(passages), "--sortie", str(sortie)]
    with pytest.raises(generation.ArretGeneration, match="pas entièrement sur la"):
        generation.main(argv)
    (moteur,) = moteur_local.crees
    assert moteur.dechargements == 1 and len(moteur.appels) == 1
    assert not sortie.exists()
