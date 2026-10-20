"""L'égalité avec le banc : ce qui est servi est ce qui a été mesuré (étape 2, 03, B).

Les 69 questions du jeu gelé v2, rejouées par l'API avec les valeurs par défaut :
les dix premières séries de chaque question égales, dans l'ordre, au `top` de
`resultat.json` du run a3b7f0e025e4476ea4b60e7d0498876b (mesure 8, corpus v2,
encodage 3). Puis les mêmes questions hors catalogue : le réglage doit changer au
moins un classement (le banc n'a pas de run « tout le corpus » sur le v2 : pas
d'égalité à vérifier de ce côté).

Demande la base réelle et l'instance EmbeddingGemma : joué par l'étape
« équivalence de l'API » de `make verify-poste` (seconde exception de
CONTRIBUTING.md : sous `modele_api`, en lecture seule), qui pose
`API_MODELE_EGALITE_DSN` pour ce seul test ; sauté partout ailleurs (plafond 1 dans
`verification/sauts_attendus.tsv`). L'étape pose aussi `API_MODELE_EGALITE_EXIGEE=1` :
alors une condition manquante est un échec, jamais un saut.

Le jeu n'est lu que par ce test, depuis ses fichiers versionnés, empreinte vérifiée ;
aucun réglage n'en dépend. Les sorties ne portent que des identifiants et des nombres.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import secrets
import statistics
import time
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import RACINE_DEPOT
from fastapi.testclient import TestClient

from api_modele import banc, memoire
from api_modele.application import creer_application
from api_modele.configuration import charger

RUN = "a3b7f0e025e4476ea4b60e7d0498876b"
RESULTAT = (
    RACINE_DEPOT
    / "10_mesures_recherche"
    / "mlflow"
    / "pieces"
    / RUN
    / "artifacts"
    / "resultat.json"
)
EMPREINTE_RESULTAT = "5184cc00c9773404629c31eb486e1c618bb3d4a4a1c9fdb2ca5ab0ee5276b50b"
JEU = RACINE_DEPOT / "database" / "donnees" / "jeu_evaluation" / "v2"
EMPREINTE_JEU = "31712b0d0b0cbd318ef228da105673af0cba2db431a72462f7635b2f6ec78b30"


def manque(raison: str) -> None:
    if os.environ.get("API_MODELE_EGALITE_EXIGEE") == "1":
        pytest.fail(f"égalité avec le banc exigée, mais {raison}")
    pytest.skip(f"égalité avec le banc : {raison} (étape de make verify-poste)")


def test_egalite_avec_le_banc_sur_les_69_questions():
    dsn = os.environ.get("API_MODELE_EGALITE_DSN")
    if not dsn:
        manque("API_MODELE_EGALITE_DSN n'est pas posée")
    assert dsn is not None
    if not RESULTAT.is_file():
        manque(f"la pièce du run de référence est absente : {RESULTAT}")

    # Les entrées, vérifiées avant toute comparaison.
    assert hashlib.sha256(RESULTAT.read_bytes()).hexdigest() == EMPREINTE_RESULTAT
    reference = json.loads(RESULTAT.read_text(encoding="utf-8"))
    assert (
        reference["mesure"],
        reference["corpus_id"],
        reference["encodage_id"],
        reference["perimetre_entites"],
    ) == (8, "v2", 3, "catalogue")
    assert banc.module("jeu").empreinte_fichiers(JEU) == EMPREINTE_JEU
    assert reference["jeu_empreinte"] == EMPREINTE_JEU
    with (JEU / "questions.csv").open(encoding="utf-8", newline="") as f:
        questions = {q["question_id"]: q["texte"] for q in csv.DictReader(f)}
    attendus = {
        q["question_id"]: [cle for cle, _ in q["top"]] for q in reference["questions"]
    }
    scores_banc = {q["question_id"]: dict(q["top"]) for q in reference["questions"]}
    assert len(questions) == 69
    assert set(questions) == set(attendus)

    cle = secrets.token_urlsafe(32)
    reglages = replace(
        charger({"API_MODELE_CLES": f"egalite:{cle}", "API_MODELE_DSN": dsn}),
        debit_par_minute=100_000,
    )
    entetes = {"X-API-Key": cle}
    differentes: list[str] = []
    durees_ms: list[float] = []
    ecart_score = 0.0
    hors_catalogue_differents = 0
    with TestClient(creer_application(reglages)) as api:
        info = api.get("/v1/info", headers=entetes).json()
        if (info["encodage_id"], info["corpus_id"]) != (3, "v2"):
            pytest.fail(
                f"la vue désigne l'encodage {info['encodage_id']} (corpus"
                f" {info['corpus_id']}) : la référence est celle de l'encodage 3 —"
                " refus de comparer"
            )
        rss_apres_demarrage = memoire.rss_octets()
        for qid, texte in sorted(questions.items()):
            debut = time.perf_counter()
            r = api.post("/v1/recherche", json={"question": texte}, headers=entetes)
            durees_ms.append((time.perf_counter() - debut) * 1000)
            assert r.status_code == 200, (qid, r.json()["code"])
            series = r.json()["series"]
            obtenu = [s["entite"] for s in series]
            if obtenu != attendus[qid]:
                differentes.append(qid)
            for s in series:
                if s["entite"] in scores_banc[qid]:
                    ecart = abs(s["score"] - scores_banc[qid][s["entite"]])
                    ecart_score = max(ecart_score, ecart)
            toutes = api.post(
                "/v1/recherche",
                json={"question": texte, "catalogue_seul": False},
                headers=entetes,
            )
            assert toutes.status_code == 200, (qid, toutes.json()["code"])
            if [s["entite"] for s in toutes.json()["series"]] != obtenu:
                hors_catalogue_differents += 1
        pic = memoire.pic_octets()

    bilan = {
        "run_de_reference": RUN,
        "questions": len(questions),
        "egales": len(questions) - len(differentes),
        "differentes": differentes,
        "ecart_de_score_maximal": ecart_score,
        "hors_catalogue_classements_differents": hors_catalogue_differents,
        "duree_ms_mediane": round(statistics.median(durees_ms), 1),
        "duree_ms_p95": round(statistics.quantiles(durees_ms, n=20)[18], 1),
        "memoire_residente_apres_demarrage_octets": rss_apres_demarrage,
        "memoire_pic_octets": pic,
    }
    sortie = os.environ.get("API_MODELE_EGALITE_SORTIE")
    if sortie:
        Path(sortie).write_text(json.dumps(bilan, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(bilan))

    assert differentes == [], f"{len(differentes)} question(s) : {differentes}"
    assert hors_catalogue_differents >= 1
