"""Le banc de mesure de la réponse (spec 06, A) : sans appel réel.

Tout ce qui parle à un fournisseur passe par les doublures du bouclage et les deux
profils factices ; la garde de toute la suite (`aucun_appel_hors_du_bouclage`) le
vérifie. MLflow est remplacé par un exécuteur factice (le script, lui, est éprouvé
avec un MLflow simulé) ; la ligne `bench.eval_runs` est écrite dans la base jetable.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from conftest import (
    CLE_APPLICATION,
    ENVIRONNEMENT_FACTICE,
    QUESTION,
    SORTIE_VALIDE,
    BaseApi,
    DoublureFournisseur,
    Doublures,
)
from fastapi.testclient import TestClient

from api_modele.banc_reponse import __main__ as cli
from api_modele.banc_reponse import enregistrement, jeu, journal_mlflow
from api_modele.banc_reponse.espion import Espion, Releve, nombres
from api_modele.banc_reponse.mesure import (
    Banc,
    PlafondDepasse,
    Run,
    essai,
    estimer,
    garder_plafond,
    mesurer,
    metriques,
    metriques_scenarios,
    scenarios,
    verdict,
)
from api_modele.configuration import Prix, Profil
from api_modele.fournisseurs import cout_usd
from api_modele.modeles import Serie
from api_modele.reponse import INVITE_REPONSE, message_de_reponse

RUN1 = "famille:F7,issue:hors_catalogue,famille:F9,famille:F10"
COURT = "famille:F7,issue:hors_catalogue"
TEXTE_REFUS = "Les passages ne parlent pas de cette série."
REFUS = json.dumps(
    {
        "refus": True,
        "motif_refus": "inconnu",
        "reponse": TEXTE_REFUS,
        "series_citees": [],
    }
)
RUN_ID = "0123456789abcdef0123456789abcdef"


def profil(fournisseur: str = "mistral", **champs: Any) -> Profil:
    valeurs: dict[str, Any] = {
        "nom": "p",
        "fournisseur": fournisseur,
        "modele": "m",
        "adresse": "http://127.0.0.1/v1/chat/completions",
        "variable_cle": "CLE",
        "top_p": 1.0,
        "reasoning_effort": None,
        "reglages_absents": (),
        "prix": Prix(2.0, 8.0),
        "delai_s": 1.0,
        "statut": "stable",
        "disponible": True,
    }
    return Profil(**(valeurs | champs))


# --------------------------------------------------------------------------- #
#  Le jeu, l'espion, les métriques : sans API
# --------------------------------------------------------------------------- #
def test_le_jeu_se_lit_par_famille_et_par_issue():
    questions, empreinte = jeu.lire(COURT)
    assert empreinte == jeu.EMPREINTE
    assert len(questions) == 10
    assert sum(q.famille == "F7" for q in questions) == 5
    assert sum(q.issue == "reconnue_hors_catalogue" for q in questions) == 5
    assert not any(q.attend_une_serie for q in questions)
    assert [q.question_id for q in questions] == sorted(
        q.question_id for q in questions
    )
    run1, _ = jeu.lire(RUN1)
    assert len(run1) == 26
    assert sum(q.attend_une_serie for q in run1) == 16
    assert jeu.nom_court(RUN1) == "F7+hors_catalogue+F9+F10"


@pytest.mark.parametrize("designation", ["question:Q001", "famille:X", "issue:autre"])
def test_une_question_ne_se_designe_pas_une_par_une(designation):
    with pytest.raises(jeu.JeuRefuse):
        jeu.lire(designation)


def test_un_jeu_altere_est_refuse_avant_tout(tmp_path):
    copie = tmp_path / "v2"
    shutil.copytree(jeu.DOSSIER, copie)
    with (copie / "questions.csv").open("a", encoding="utf-8") as f:
        f.write("\n")
    with pytest.raises(jeu.JeuRefuse, match="empreinte"):
        jeu.lire(COURT, copie)


def test_le_texte_d_une_question_par_son_identifiant():
    assert jeu.texte_de("Q001").startswith("Mon père adorait City Hunter")
    with pytest.raises(jeu.JeuRefuse):
        jeu.texte_de("Q999")


def test_le_releve_compte_comme_la_couche_des_fournisseurs():
    mistral = Releve("h", "reponse", {}, 700, statut=200)
    mistral.usage = {"prompt_tokens": 1000, "completion_tokens": 100}
    assert mistral.jetons(profil(), 3.5) == (1000, 100, 0, False)
    assert mistral.cout(profil(), 3.5) == cout_usd(profil(), 1000, 100, 0)

    openai = Releve("h", "reponse", {}, 700, statut=200)
    openai.usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "completion_tokens_details": {"reasoning_tokens": 30},
    }
    assert openai.jetons(profil("openai"), 3.5) == (1000, 70, 30, False)

    sans_usage = Releve("h", "reponse", {}, 700, statut=200)
    assert sans_usage.jetons(profil(), 3.5) == (200, 0, 0, True)

    refuse = Releve("h", "reponse", {}, 700, statut=400)
    refuse.usage = mistral.usage
    assert refuse.cout(profil(), 3.5) == 0.0


def test_l_espion_ne_garde_que_des_nombres():
    assert nombres(
        {
            "prompt_tokens": 12,
            "texte": "une critique",
            "details": {"reasoning_tokens": 3, "vide": {}, "drapeau": True},
            "liste": [1, 2],
        }
    ) == {"prompt_tokens": 12, "details": {"reasoning_tokens": 3}}


def ligne(**champs: object) -> dict[str, object]:
    base: dict[str, object] = {
        "question_id": "Q",
        "famille": "F9",
        "issue": "au_catalogue",
        "statut": 200,
        "code": None,
        "motif": None,
        "pseudonymes": 0,
        "refus": False,
        "mention_hors_catalogue": False,
        "appels": [
            {"jetons": {"entree": 1000, "sortie": 100, "raisonnement": 0}},
        ],
        "latence_modele_ms": 1000.0,
        "latence_totale_ms": 1200.0,
        "nb_series_citees": 2,
        "titres_presents": 1,
        "titres_marques": 2,
        "longueur_reponse": 300,
        "recouvrement_8": 0.1,
    }
    return base | champs


def test_les_metriques_et_le_critere():
    run = Run("p", cout_reel_usd=0.0123)
    run.par_question = [
        *(ligne(famille="F7", issue="inconnue", refus=True) for _ in range(4)),
        ligne(famille="F7", issue="inconnue"),
        ligne(issue="reconnue_hors_catalogue", refus=True),
        *(
            ligne(issue="reconnue_hors_catalogue", mention_hors_catalogue=True)
            for _ in range(3)
        ),
        ligne(issue="reconnue_hors_catalogue"),
        ligne(citee_attendue_1=True, hit_rate_10=True, recouvrement_8=0.5),
        ligne(citee_attendue_1=False, hit_rate_10=True, refus=True),
        ligne(
            statut=503,
            code="sortie_modele_invalide",
            motif="serie_inconnue",
            appels=[{"jetons": {"entree": 900, "sortie": 50, "raisonnement": 0}}],
        ),
    ]
    m = metriques(run)
    assert m["refus_correct_F7"] == 4
    assert m["refus_correct_hors_catalogue"] == 4
    assert m["refus_a_tort"] == 1
    assert m["series_inventees"] == 1
    assert m["sorties_invalides_serie_inconnue"] == 1
    assert (m["citee_attendue_1"], m["hit_rate_10"], m["questions_de_rang"]) == (
        1,
        2,
        2,
    )
    assert m["jetons_entree_somme"] == 12 * 1000 + 900
    assert m["recouvrement_8_max"] == 0.5
    assert m["latence_modele_ms_mediane"] == 1000.0
    assert m["cout_usd"] == 0.0123
    # Sur les douze réponses rendues : un titre écrit sur deux séries citées.
    assert (m["titres_presents"], m["titres_cites"], m["titre_present"]) == (
        12,
        24,
        0.5,
    )
    assert m["citees_sans_marque"] == 0
    # Une série citée sans marque n'entre pas dans `titre_present` : comptée à part.
    run.par_question = [ligne(titres_marques=1)]
    m1 = metriques(run)
    assert (m1["titres_cites"], m1["titre_present"], m1["citees_sans_marque"]) == (
        1,
        1.0,
        1,
    )
    v = verdict(m)
    assert v == {
        "F7": True,
        "hors_catalogue": True,
        "series_inventees": False,
        "pseudonymes": True,
        "tenu": False,
    }
    sans_f7 = verdict(m | {"questions_F7": 0, "series_inventees": 0})
    assert "F7" not in sans_f7 and sans_f7["tenu"] is True


def test_le_plafond_avant_tout_appel():
    from api_modele.banc_reponse.mesure import Estimation

    garder_plafond(Estimation(1, 1, 10, 0.4), 0.5)
    with pytest.raises(PlafondDepasse, match="aucun appel"):
        garder_plafond(Estimation(1, 1, 10, 0.6), 0.5)


# --------------------------------------------------------------------------- #
#  La garde de commit, MLflow, eval_runs
# --------------------------------------------------------------------------- #
def git(dossier: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=dossier, check=True, capture_output=True)


def test_on_commite_d_abord_on_mesure_ensuite(tmp_path):
    distant, local = tmp_path / "distant.git", tmp_path / "local"
    git(tmp_path, "init", "-q", "--bare", str(distant))
    git(tmp_path, "init", "-q", "-b", "travail", str(local))
    for cle, valeur in (
        ("user.name", "essai"),
        ("user.email", "essai@exemple.invalid"),
    ):
        git(local, "config", cle, valeur)
    (local / "f.txt").write_text("1")
    git(local, "add", "f.txt")
    git(local, "commit", "-q", "-m", "un")
    with pytest.raises(enregistrement.CommitRefuse, match="sans amont"):
        enregistrement.exiger_commit(local)
    git(local, "remote", "add", "origin", str(distant))
    git(local, "push", "-q", "-u", "origin", "travail")
    etat = enregistrement.exiger_commit(local)
    assert etat["branche"] == "travail" and len(etat["commit"]) == 40
    (local / "f.txt").write_text("2")
    with pytest.raises(enregistrement.CommitRefuse, match="non propre"):
        enregistrement.exiger_commit(local)
    git(local, "commit", "-q", "-am", "deux")
    with pytest.raises(enregistrement.CommitRefuse, match="pas poussé"):
        enregistrement.exiger_commit(local)


def test_mlflow_par_l_interpreteur_du_module_10(tmp_path, monkeypatch):
    recues: list[list[str]] = []

    def executeur(commande: list[str]) -> str:
        recues.append(commande)
        return f"un avertissement\n{RUN_ID}\n"

    charge = enregistrement.charge_mlflow(
        "nom", {"statut": "termine"}, {"a": "x", "b": [1]}, {"m": 1.0}, {"p.json": [1]}
    )
    assert charge["parametres"] == {"a": "x", "b": "[1]"}
    assert charge["experience"] == "E3-reponse"
    assert enregistrement.journaliser_mlflow(charge, tmp_path, executeur) == RUN_ID
    commande = recues[0]
    assert commande[:5] == [
        "uv",
        "run",
        "--project",
        str(enregistrement.MODULE_10),
        "--locked",
    ]
    assert commande[-3:] == [
        "-I",
        str(enregistrement.SCRIPT),
        str(tmp_path / "mlflow_charge.json"),
    ]
    with pytest.raises(RuntimeError, match="illisible"):
        enregistrement.journaliser_mlflow(charge, tmp_path, lambda _: "rien\n")

    vus: dict[str, Any] = {}

    def lancer(commande: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        vus.update(options)
        return subprocess.CompletedProcess(commande, 0, stdout=RUN_ID)

    monkeypatch.setenv("MISTRAL_API_KEY", "factice-ne-doit-pas-passer")
    monkeypatch.setattr(enregistrement.subprocess, "run", lancer)
    assert enregistrement.executer(["uv"]) == RUN_ID
    assert "MISTRAL_API_KEY" not in vus["env"]
    assert set(vus["env"]) <= set(enregistrement.ENVIRONNEMENT_TRANSMIS)


class MlflowSimule:
    def __init__(self) -> None:
        self.appels: list[tuple[str, Any]] = []
        self.experiences: set[str] = set()

    def __getattr__(self, nom: str) -> Any:
        def appel(*args: Any, **options: Any) -> Any:
            self.appels.append((nom, args))
            if nom == "get_experiment_by_name":
                return args[0] if args[0] in self.experiences else None
            if nom == "create_experiment":
                self.experiences.add(args[0])
            if nom == "start_run":
                return type("R", (), {"info": type("I", (), {"run_id": RUN_ID})()})()
            if nom == "log_metrics" and "boum" in args[0]:
                raise RuntimeError("boum")
            return None

        return appel


def test_le_script_mlflow(tmp_path, capsys, monkeypatch):
    charge = enregistrement.charge_mlflow(
        "nom", {"statut": "interrompu"}, {"a": "x"}, {"m": 1.0}, {"p.json": {"k": 1}}
    )
    mlflow = MlflowSimule()
    assert journal_mlflow.journaliser(mlflow, charge) == RUN_ID
    noms = [n for n, _ in mlflow.appels]
    assert noms[:4] == [
        "set_tracking_uri",
        "get_experiment_by_name",
        "create_experiment",
        "set_experiment",
    ]
    assert ("log_text", ('{\n "k": 1\n}', "p.json")) in mlflow.appels
    assert mlflow.appels[-1] == ("end_run", ("KILLED",))
    mlflow.appels.clear()
    journal_mlflow.journaliser(mlflow, charge | {"etiquettes": {"statut": "termine"}})
    assert "create_experiment" not in [n for n, _ in mlflow.appels]
    assert mlflow.appels[-1] == ("end_run", ("FINISHED",))
    with pytest.raises(RuntimeError):
        journal_mlflow.journaliser(mlflow, charge | {"metriques": {"boum": 1.0}})
    assert mlflow.appels[-1] == ("end_run", ("FAILED",))

    fichier = tmp_path / "charge.json"
    fichier.write_text(json.dumps(charge), encoding="utf-8")
    monkeypatch.setattr(
        journal_mlflow.importlib, "import_module", lambda _: MlflowSimule()
    )
    assert journal_mlflow.principal(["script", str(fichier)]) == 0
    assert capsys.readouterr().out.strip() == RUN_ID
    assert journal_mlflow.principal(["script"]) == 2


def test_la_ligne_eval_runs(base_api: BaseApi):
    enregistrement.inscrire_eval_run(
        base_api.dsn_admin, RUN_ID, "v7", base_api.encodage_id, "essai"
    )
    with psycopg.connect(base_api.dsn_admin) as cx:
        assert cx.execute(
            "SELECT corpus_id, encodage_id, note FROM bench.eval_runs"
            " WHERE run_id = %s",
            (RUN_ID,),
        ).fetchone() == ("v7", base_api.encodage_id, "essai")


def scenarios_csv(dossier: Path, lignes: list[str]) -> Path:
    fichier = dossier / "scenarios.csv"
    entete = ",".join(cli.COLONNES_SCENARIOS)
    fichier.write_text("\n".join([entete, *lignes]) + "\n", encoding="utf-8")
    return fichier


def cinq_scenarios(premiere: str = "Q001") -> list[str]:
    return [
        ligne
        for s in range(1, 6)
        for ligne in (
            f"S{s},1,{premiere},{QUESTION},1",
            f"S{s},2,et un autre ?,{QUESTION},",
        )
    ]


def test_les_scenarios_ecrits_par_max(tmp_path):
    fichier = scenarios_csv(tmp_path, cinq_scenarios())
    tours, empreinte = cli.lire_scenarios(fichier)
    assert len(tours) == 10 and len(empreinte) == 64
    assert tours[0].question == jeu.texte_de("Q001")
    assert (tours[0].serie_attendue_id, tours[1].serie_attendue_id) == (1, None)
    with pytest.raises(cli.BancRefuse, match="5 scénarios"):
        cli.lire_scenarios(scenarios_csv(tmp_path, cinq_scenarios()[:8]))
    with pytest.raises(cli.BancRefuse, match="tour vide"):
        cli.lire_scenarios(scenarios_csv(tmp_path, ["S1,1,,x,"]))
    fichier.write_text("a,b\n", encoding="utf-8")
    with pytest.raises(cli.BancRefuse, match="colonnes"):
        cli.lire_scenarios(fichier)


def test_le_banc_refuse_sans_dsn_ni_configuration(doublures):
    assert cli.principal(["mesurer", "p", COURT], environ={}) == 2
    assert cli.principal(["estimer", "p", COURT], environ={}) == 2
    verifier = ENVIRONNEMENT_FACTICE | {
        "API_MODELE_VERIFIER_MODELES": "vrai",
        "MISTRAL_API_KEY": "factice",
    }
    with pytest.raises(cli.BancRefuse, match="VERIFIER_MODELES"):
        with cli.ouvrir(verifier):
            pass
    assert cli.principal(["estimer", "p", COURT], environ=verifier) == 2
    assert doublures.recus() == []


# --------------------------------------------------------------------------- #
#  Contre l'API et les doublures des fournisseurs
# --------------------------------------------------------------------------- #
@pytest.fixture
def banc_(api_reponse: TestClient) -> Iterator[Banc]:
    etat = api_reponse.app.state.etat
    espion = Espion()
    with espion.ecouter():
        yield Banc(
            api_reponse,
            CLE_APPLICATION,
            etat.reglages.modele_langage,
            etat.pseudonymes,
            espion,
            pause_s=0.0,
            dormir=lambda _: None,
        )


def test_l_estimation_est_la_formule(banc_: Banc):
    p = banc_.profil("factice-a")
    r = banc_.api.post(
        "/v1/recherche",
        json={"question": QUESTION, "nombre_series": 5, "passages_par_serie": 2},
        headers=banc_.entetes,
    )
    series = [Serie.model_validate(s) for s in r.json()["series"]]
    caracteres = len(INVITE_REPONSE) + len(message_de_reponse("", QUESTION, series))
    entree = math.ceil(caracteres / banc_.modele_langage.caracteres_par_jeton)
    attendu = cout_usd(p, entree, 400, 0)
    e = estimer(banc_, p, [QUESTION, QUESTION])
    assert (e.questions, e.appels, e.jetons_entree) == (2, 2, 2 * entree)
    assert e.cout_usd == pytest.approx(2 * attendu)


def test_l_essai_ses_chiffres_et_son_texte(banc_: Banc, doublures: Doublures):
    DoublureFournisseur.usage = {"prompt_tokens": 1000, "completion_tokens": 50}
    chiffres, texte = essai(banc_, banc_.profil("factice-a"), QUESTION)
    assert chiffres["statut"] == 200 and chiffres["pseudonymes"] == 0
    ecrit = json.loads(SORTIE_VALIDE)["reponse"]
    assert texte is not None and texte == ecrit.replace("[S:1]", "Série 1 [S:1]")
    (appel,) = chiffres["appels"]
    assert appel["parametres_envoyes"] == {
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": 400,
    }
    assert appel["usage"] == {"prompt_tokens": 1000, "completion_tokens": 50}
    assert appel["caracteres_par_jeton"] == round(appel["caracteres_envoyes"] / 1000, 3)
    assert chiffres["cout_reel_usd"] == round((1000 * 2.0 + 50 * 8.0) / 1e6, 6)
    assert texte not in json.dumps(chiffres, ensure_ascii=False)

    chiffres, texte = essai(banc_, banc_.profil("factice-b"), QUESTION, temperature=0.7)
    assert (chiffres["statut"], chiffres["code"], texte) == (
        422,
        "reglage_non_expose",
        None,
    )
    assert len(doublures.recus()) == 1

    # Une longueur demandée part telle quelle et borne l'estimation.
    p = banc_.profil("factice-a")
    chiffres, _ = essai(banc_, p, QUESTION, longueur_max=1000)
    (appel,) = chiffres["appels"]
    assert (chiffres["statut"], appel["parametres_envoyes"]["max_tokens"]) == (
        200,
        1000,
    )
    assert chiffres["longueur_max_demandee"] == 1000
    assert chiffres["estimation"] == estimer(banc_, p, [QUESTION], 1000).en_dict()
    assert (
        estimer(banc_, p, [QUESTION], 1000).cout_usd
        > estimer(banc_, p, [QUESTION]).cout_usd
    )


def test_le_plafond_en_cours_de_run(banc_: Banc, doublures: Doublures):
    DoublureFournisseur.contenu = REFUS
    DoublureFournisseur.usage = {"prompt_tokens": 10_000_000, "completion_tokens": 1}
    questions, _ = jeu.lire(COURT)
    run = mesurer(banc_, banc_.profil("factice-a"), questions, 0.5)
    assert run.statut == "interrompu" and "plafond" in str(run.motif_interruption)
    assert len(run.par_question) == 1 and len(doublures.recus()) == 1


def test_un_fournisseur_en_echec_interrompt_le_run(banc_: Banc, doublures: Doublures):
    DoublureFournisseur.statut = 500
    questions, _ = jeu.lire(COURT)
    run = mesurer(banc_, banc_.profil("factice-a"), questions, 0.5)
    assert (run.statut, run.motif_interruption) == ("interrompu", "fournisseur_refus")
    assert len(run.par_question) == 1


def test_une_reponse_rendue_et_ses_mesures(banc_: Banc, doublures: Doublures):
    question = jeu.Question("Q", QUESTION, "F9", "au_catalogue", (1,))
    run = mesurer(banc_, banc_.profil("factice-a"), [question], 0.5)
    (x,) = run.par_question
    assert (x["refus"], x["series_citees"], x["citee_attendue_1"]) == (False, [1], True)
    assert x["hit_rate_10"] is True
    # Le texte lu par Max est celui de la route, titre inséré ; `titres_presents`
    # compte ce que le modèle a écrit seul.
    ecrit = json.loads(SORTIE_VALIDE)["reponse"]
    assert run.textes == {"Q": ecrit.replace("[S:1]", "Série 1 [S:1]")}
    assert (x["titres_presents"], x["titres_marques"]) == (0, 1)
    assert banc_.espion.releves[-1].texte_modele == ecrit
    assert run.contexte["corpus_id"] == "v7"


@contextmanager
def ouvreur_de(banc_: Banc) -> Iterator[Banc]:
    yield banc_


def test_un_run_complet_par_la_ligne_de_commande(
    banc_: Banc, doublures: Doublures, base_api: BaseApi, tmp_path, monkeypatch
):
    DoublureFournisseur.contenu = REFUS
    DoublureFournisseur.usage = {"prompt_tokens": 1000, "completion_tokens": 20}
    sorties = tmp_path / "sorties"
    monkeypatch.setattr(enregistrement, "SORTIES", sorties)
    etat_git = {"commit": "c" * 40, "branche": "b", "arbre_propre": "True"}
    monkeypatch.setattr(enregistrement, "exiger_commit", lambda: etat_git)
    charges: list[dict[str, Any]] = []

    def executer(commande: list[str]) -> str:
        charges.append(json.loads(Path(commande[-1]).read_text(encoding="utf-8")))
        return RUN_ID

    monkeypatch.setattr(enregistrement, "executer", executer)
    code = cli.principal(
        ["mesurer", "factice-a", COURT],
        environ={"DATABASE_URL": base_api.dsn_admin},
        ouvreur=lambda _: ouvreur_de(banc_),
    )
    assert code == 0
    (dossier,) = sorties.iterdir()
    assert dossier.name.startswith("factice-a-F7+hors_catalogue-")
    resume = json.loads((dossier / "resume.json").read_text(encoding="utf-8"))
    assert resume["verdict"]["tenu"] is True
    assert resume["metriques"]["refus_correct_F7"] == 5
    assert resume["parametres"]["commit"] == "c" * 40
    # Les pièces de MLflow : des nombres et des noms, aucun texte de réponse.
    (charge,) = charges
    assert TEXTE_REFUS not in json.dumps(charge, ensure_ascii=False)
    assert TEXTE_REFUS in (dossier / "reponses.md").read_text(encoding="utf-8")
    with psycopg.connect(base_api.dsn_admin) as cx:
        assert cx.execute(
            "SELECT corpus_id FROM bench.eval_runs WHERE run_id = %s", (RUN_ID,)
        ).fetchone() == ("v7",)

    # Reprendre un run mesuré : le même enregistrement, rejoué.
    autre = "fedcba9876543210fedcba9876543210"
    monkeypatch.setattr(enregistrement, "executer", lambda _: autre)
    code = cli.principal(
        ["reprendre", str(dossier)], environ={"DATABASE_URL": base_api.dsn_admin}
    )
    assert code == 0
    assert json.loads((dossier / "run_id.json").read_text())["run_id"] == autre


def test_les_refus_avant_tout_appel(
    banc_: Banc, doublures: Doublures, tmp_path, monkeypatch
):
    environ = {"DATABASE_URL": "postgresql://x@localhost/y"}

    def refuser() -> dict[str, str]:
        raise enregistrement.CommitRefuse("arbre non propre")

    monkeypatch.setattr(enregistrement, "exiger_commit", refuser)
    assert (
        cli.principal(
            ["mesurer", "factice-a", COURT], environ, lambda _: ouvreur_de(banc_)
        )
        == 2
    )
    monkeypatch.setattr(
        enregistrement, "exiger_commit", lambda: {"commit": "c", "branche": "b"}
    )
    pauvre = replace(
        banc_, modele_langage=replace(banc_.modele_langage, plafond_run_usd=1e-9)
    )
    assert (
        cli.principal(
            ["mesurer", "factice-a", COURT], environ, lambda _: ouvreur_de(pauvre)
        )
        == 2
    )
    assert doublures.recus() == []


def test_l_essai_et_l_estimation_par_la_ligne_de_commande(
    banc_: Banc, doublures: Doublures, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(enregistrement, "SORTIES", tmp_path)
    ouvreur = lambda _: ouvreur_de(banc_)  # noqa: E731
    assert cli.principal(["estimer", "factice-a", COURT], {}, ouvreur) == 0
    sortie = json.loads(capsys.readouterr().out)
    assert sortie["questions"] == 10 and sortie["sous_le_plafond"] is True
    assert cli.principal(["essai", "factice-a", QUESTION], {}, ouvreur) == 0
    (dossier,) = (tmp_path / "essais").iterdir()
    assert (dossier / "essai.json").is_file() and (dossier / "reponses.md").is_file()
    assert "réponse (contrôlée" in capsys.readouterr().out
    argv = ["essai", "factice-a", QUESTION, "--longueur-max", "1000"]
    assert cli.principal(argv, {}, ouvreur) == 0
    (long_,) = (d for d in (tmp_path / "essais").iterdir() if d.name.endswith("_l1000"))
    assert (
        json.loads((long_ / "essai.json").read_text())["longueur_max_demandee"] == 1000
    )
    assert cli.principal(["essai", "inconnu", QUESTION], {}, ouvreur) == 2


def test_les_scenarios_contre_les_doublures(
    banc_: Banc, doublures: Doublures, tmp_path
):
    tours, _ = cli.lire_scenarios(scenarios_csv(tmp_path, cinq_scenarios(QUESTION)))
    run = scenarios(banc_, banc_.profil("factice-a"), tours, 0.5)
    m = metriques_scenarios(run)
    assert (m["tours"], m["tours_avec_historique"]) == (10, 5)
    assert m["reformulation_meme_serie"] == 5
    assert m["reformulation_serie_attendue"] == 0
    # Le second tour porte l'historique : la vraie réponse du premier, telle que la
    # route l'a rendue (titre inséré).
    rendue = json.loads(SORTIE_VALIDE)["reponse"].replace("[S:1]", "Série 1 [S:1]")
    seconds = [
        r["corps"]
        for r in doublures.recus()
        if len(r["corps"]["messages"][1]["content"])
        and "Libraire" in r["corps"]["messages"][1]["content"]
    ]
    assert seconds and rendue in seconds[0]["messages"][1]["content"]
    assert run.textes and all("-" in cle for cle in run.textes)


def test_les_scenarios_par_la_ligne_de_commande(
    banc_: Banc, doublures: Doublures, base_api: BaseApi, tmp_path, monkeypatch, capsys
):
    sorties = tmp_path / "sorties"
    monkeypatch.setattr(enregistrement, "SORTIES", sorties)
    monkeypatch.setattr(
        enregistrement, "exiger_commit", lambda: {"commit": "c" * 40, "branche": "b"}
    )

    def en_panne(commande: list[str]) -> str:
        raise subprocess.CalledProcessError(1, commande)

    monkeypatch.setattr(enregistrement, "executer", en_panne)
    fichier = scenarios_csv(tmp_path, cinq_scenarios(QUESTION))
    code = cli.principal(
        ["scenarios", "factice-a", str(fichier)],
        {"DATABASE_URL": base_api.dsn_admin},
        lambda _: ouvreur_de(banc_),
    )
    assert code == 4
    assert "NON enregistré" in capsys.readouterr().err
    (dossier,) = sorties.iterdir()
    assert dossier.name.startswith("factice-a-scenarios-")
    resume = json.loads((dossier / "resume.json").read_text(encoding="utf-8"))
    assert resume["metriques"]["reformulation_meme_serie"] == 5
    assert len(resume["parametres"]["scenarios_empreinte"]) == 64
    assert (dossier / "mlflow_charge.json").is_file()


@pytest.mark.parametrize("panne", ["fournisseur", "plafond"])
def test_les_scenarios_s_interrompent(
    banc_: Banc, doublures: Doublures, tmp_path, panne
):
    if panne == "fournisseur":
        DoublureFournisseur.statut = 500
    else:
        DoublureFournisseur.usage = {
            "prompt_tokens": 10_000_000,
            "completion_tokens": 1,
        }
    tours, _ = cli.lire_scenarios(scenarios_csv(tmp_path, cinq_scenarios(QUESTION)))
    run = scenarios(banc_, banc_.profil("factice-a"), tours, 0.5)
    assert run.statut == "interrompu" and len(run.par_question) == 1


def test_les_erreurs_des_fournisseurs_relevees(banc_: Banc, doublures: Doublures):
    from api_modele.banc_reponse import espion

    assert espion._erreur(
        httpx.Response(
            400,
            json={"error": {"code": "invalide", "param": "response_format", "x": 1}},
        )
    ) == {"code": "invalide", "param": "response_format"}
    assert espion._erreur(httpx.Response(422, json={"detail": [{"msg": "m"}]})) == {
        "message": '[{"msg": "m"}]'
    }
    assert espion._erreur(httpx.Response(400, json={"error": "texte"})) == {
        "message": "texte"
    }
    assert espion._erreur(httpx.Response(502, content=b"<html>")) == {}
    assert espion._caracteres({"messages": "pas une liste"}) == 0

    DoublureFournisseur.statut = 400
    DoublureFournisseur.corps_brut = json.dumps(
        {"error": {"message": "minLength n'est pas admis", "type": "invalid"}}
    ).encode()
    chiffres, texte = essai(banc_, banc_.profil("factice-a"), QUESTION)
    assert (chiffres["statut"], chiffres["code"], texte) == (
        503,
        "fournisseur_refus",
        None,
    )
    (appel,) = chiffres["appels"]
    assert appel["erreur"] == {
        "type": "invalid",
        "message": "minLength n'est pas admis",
    }
    assert appel["cout_usd"] == 0.0

    DoublureFournisseur.statut = 200
    DoublureFournisseur.corps_brut = b"pas du json"
    chiffres, _ = essai(banc_, banc_.profil("factice-a"), QUESTION)
    assert chiffres["code"] == "fournisseur_refus"
    assert chiffres["appels"][-1]["usage"] == {}


def test_les_refus_du_banc(banc_: Banc, monkeypatch):
    with pytest.raises(ValueError, match="inconnu"):
        banc_.profil("inconnu")
    ml = banc_.modele_langage
    sans_cle = replace(
        banc_,
        modele_langage=replace(
            ml, profils=tuple(replace(p, disponible=False) for p in ml.profils)
        ),
    )
    with pytest.raises(ValueError, match="indisponible"):
        sans_cle.profil("factice-a")
    with pytest.raises(RuntimeError, match="422"):
        banc_.rechercher("x" * 600)
    with pytest.raises(PlafondDepasse, match="prix"):
        estimer(banc_, replace(banc_.profil("factice-a"), prix=None), [QUESTION])
    monkeypatch.setattr(banc_, "rechercher", lambda *a, **k: [])
    e = estimer(banc_, banc_.profil("factice-a"), [QUESTION])
    assert (e.questions, e.appels, e.cout_usd) == (1, 0, 0.0)
    with pytest.raises(jeu.JeuRefuse, match="aucune question"):
        jeu.lire("famille:F99")


def test_les_metriques_sans_reponse_rendue():
    run = Run("p")
    run.par_question = [
        ligne(statut=503, code="fournisseur_refus", appels=[]),
    ]
    m = metriques(run)
    assert m["reponses_rendues"] == 0
    assert "latence_modele_ms_mediane" not in m and "recouvrement_8_moyen" not in m
    run.par_question = [ligne()]
    assert metriques(run)["latence_totale_ms_p95"] == 1200.0
    run.par_question = [ligne(titres_presents=None)]
    assert "titre_present" not in metriques(run)
