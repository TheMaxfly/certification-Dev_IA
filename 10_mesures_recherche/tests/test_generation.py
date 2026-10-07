"""Réglages de la génération : questions retenues, passages, invite, plan des
appels, ce qu'on rapporte — sur des cas écrits à la main, contre une doublure du
moteur (HTTP ou objet), et les passages de bout en bout sur la base jetable."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from mesures_recherche import executer, generation
from mesures_recherche.entites import construire
from mesures_recherche.jeu import Question

REGLAGES = generation.charger_toml(generation.REGLAGES)
INVITE = generation.charger_toml(generation.INVITE)


def q(qid, famille):
    return Question(qid, f"texte {qid}", "proposition", famille, "au_catalogue", {})


def test_premiere_question_par_famille_ordre_des_numeros():
    questions = [q("Q005", "F2"), q("Q001", "F1"), q("Q003", "F2"), q("Q020", "F10")]
    questions += [q("Q002", "F1"), q("Q009", "F9")]
    retenues = generation.premieres_par_famille(questions)
    assert [x.question_id for x in retenues] == ["Q001", "Q003", "Q009", "Q020"]


def test_meilleur_fragment_d_une_entite():
    ent = construire(
        [(10, "ms_review", 5, None), (11, "ms_review", 5, None), (30, "k", None, 7)],
        {5},
    )
    scores = np.array([0.2, 0.9, 0.95])
    assert generation.meilleur_fragment(ent, scores, ent.index_serie[5]) == 1
    egalite = np.array([0.5, 0.5, 0.1])
    assert generation.meilleur_fragment(ent, egalite, ent.index_serie[5]) == 0


def test_part_copiee_sequences_de_cinq_mots():
    passage = "Un jeune pirate au chapeau de paille rêve de trésor."
    # Les mots de la réponse : « le jeune pirate au chapeau de paille part »,
    # 4 séquences de 5 mots, dont 2 dans le passage (casse ignorée).
    reponse = "Le JEUNE pirate au chapeau de paille part"
    assert generation.part_copiee(reponse, [passage], 5) == pytest.approx(2 / 4)
    assert generation.part_copiee("Trop court ici", [passage], 5) is None
    assert (
        generation.part_copiee("aucun mot commun avec ce passage là", [passage], 5) == 0
    )


def test_repetitions_identiques_et_distinctes():
    gen = [
        {"question_id": "Q001", "reponse": "a"},
        {"question_id": "Q001", "reponse": "a"},
        {"question_id": "Q002", "reponse": "b"},
        {"question_id": "Q002", "reponse": "c"},
    ]
    r = generation.repetitions(gen)
    assert r["part_repetitions_identiques"] == 0.5
    assert r["reponses_distinctes_moyenne"] == 1.5
    assert r["questions_non_identiques"] == ["Q002"]
    assert generation.repetitions(gen[:1] + gen[2:3]) is None, "une seule réponse"


def test_messages_et_options():
    passages = [
        {"rang": 1, "titre": "One Piece", "entite": "serie:1", "texte": "Pirates."},
        {"rang": 2, "titre": None, "entite": "kitsu:9", "texte": "Ninjas."},
    ]
    systeme, utilisateur = generation.messages(INVITE, "Quel manga ?", passages)
    assert systeme == {"role": "system", "content": INVITE["systeme"]}
    assert "[1] One Piece\nPirates." in utilisateur["content"]
    assert "[2] kitsu:9\nNinjas." in utilisateur["content"], "sans titre : l'entité"
    assert utilisateur["content"].endswith("Question : Quel manga ?")
    moteur = REGLAGES["moteur"]
    o = generation.options(moteur, {"temperature": 0.7}, 3)
    assert o == {
        "temperature": 0.7,
        "seed": moteur["graine"] + 2,
        "num_predict": 400,
        "num_ctx": moteur["num_ctx"],
    }


def test_invite_porte_les_cinq_consignes_de_la_spec():
    s = INVITE["systeme"]
    for consigne in ("français", "uniquement", "recopie", "allusion personnelle"):
        assert consigne in s
    assert "Je ne sais pas" in s


def test_contexte_trop_court_arrete():
    moteur = {"num_predict": 400, "num_ctx": 8192}
    generation.verifier_contexte({"jetons_entree": 7792}, moteur)
    with pytest.raises(generation.ArretGeneration, match="plus de place"):
        generation.verifier_contexte({"jetons_entree": 7793}, moteur)


class Doublure:
    """Le moteur : une réponse par appel, dépendant de la température et de la
    graine (la température 0 rend toujours la même)."""

    def __init__(self):
        self.appels = []

    def discuter(self, msgs, opts):
        self.appels.append(opts)
        suffixe = "" if opts["temperature"] == 0 else f" (graine {opts['seed']})"
        return {
            "reponse": "Je ne sais pas d'après les passages fournis." + suffixe,
            "done_reason": "stop",
            "jetons_entree": len(msgs[1]["content"]) // 4,
            "jetons_sortie": 12,
            "latence_s": 0.1 + len(self.appels) / 100,
            "duree_totale_s": 0.1,
            "duree_chargement_s": 0.0,
        }


def passages_factices(n_questions: int) -> list[dict]:
    return [
        {
            "question_id": f"Q{i:03d}",
            "famille": f"F{i}",
            "mode": "proposition",
            "texte": f"question {i}",
            "passages": [
                {
                    "rang": r,
                    "titre": f"T{r}",
                    "entite": f"serie:{r}",
                    "chunk_id": 100 * i + r,
                    "texte": f"passage {r} de la question {i}",
                }
                for r in range(1, 11)
            ],
        }
        for i in range(1, n_questions + 1)
    ]


def test_plan_88_generations_et_graines():
    modele = Doublure()
    resultat = generation.generer(
        REGLAGES,
        INVITE,
        passages_factices(11),
        modele,
        contexte={"mesurer_vram": lambda: 0},
    )
    par_conf = {c["configuration"]["nom"]: c for c in resultat["configurations"]}
    assert sum(len(c["generations"]) for c in par_conf.values()) == 88
    base = par_conf["base"]
    assert [g["repetition"] for g in base["generations"]][:12] == [1] * 11 + [2]
    assert {len(g["passages"]) for g in base["generations"]} == {5}
    assert {
        len(g["passages"]) for g in par_conf["beaucoup-de-passages"]["generations"]
    } == {10}
    assert {len(g["passages"]) for g in par_conf["peu-de-passages"]["generations"]} == {
        3
    }
    assert base["resume"]["part_repetitions_identiques"] == 1.0
    chaude = par_conf["temperature-haute"]["resume"]
    assert chaude["part_repetitions_identiques"] == 0.0
    assert chaude["reponses_distinctes_moyenne"] == 3.0
    assert "part_repetitions_identiques" not in par_conf["peu-de-passages"]["resume"]


class Serveur(BaseHTTPRequestHandler):
    recus: list = []

    def log_message(self, *args):
        pass

    def _repondre(self, corps):
        donnees = json.dumps(corps).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def do_GET(self):
        if self.path == "/api/ps":
            self._repondre(
                {
                    "models": [
                        {"name": "m", "size": 5, "size_vram": 5, "context_length": 8192}
                    ]
                }
            )
        elif self.path == "/api/version":
            self._repondre({"version": "0.13.5"})
        else:
            self._repondre(
                {
                    "models": [
                        {
                            "name": "m",
                            "digest": "abc",
                            "details": {
                                "quantization_level": "Q4_K_M",
                                "parameter_size": "3.8B",
                            },
                        }
                    ]
                }
            )

    def do_POST(self):
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Serveur.recus.append((self.path, corps))
        self._repondre(
            {
                "message": {"content": "Réponse."},
                "done_reason": "stop",
                "prompt_eval_count": 120,
                "eval_count": 7,
                "total_duration": 2e9,
                "load_duration": 1e8,
            }
        )


@pytest.fixture
def ollama_doublure():
    Serveur.recus = []
    serveur = HTTPServer(("127.0.0.1", 0), Serveur)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    yield generation.Ollama(f"http://127.0.0.1:{serveur.server_port}", "m", "30m")
    serveur.shutdown()


def test_client_ollama_keep_alive_options_dechargement(ollama_doublure):
    o = ollama_doublure
    appel = o.discuter(
        [{"role": "user", "content": "x"}], {"temperature": 0, "seed": 1}
    )
    assert appel["reponse"] == "Réponse." and appel["jetons_entree"] == 120
    assert appel["jetons_sortie"] == 7 and appel["duree_totale_s"] == 2.0
    chemin, corps = Serveur.recus[0]
    assert chemin == "/api/chat" and corps["keep_alive"] == "30m"
    assert corps["stream"] is False and corps["options"]["seed"] == 1
    o.decharger()
    assert Serveur.recus[-1] == ("/api/generate", {"model": "m", "keep_alive": 0})
    assert o.charge() == {"taille": 5, "taille_memoire_video": 5, "contexte": 8192}
    assert o.description()["modele_digest"] == "abc"


def test_enregistrement_un_run_par_configuration_et_les_traces(tmp_path):
    import mlflow

    from mesures_recherche.enregistrement import uri_suivi

    passages = passages_factices(2)
    generation.generer(
        REGLAGES,
        INVITE,
        passages,
        Doublure(),
        enregistrer=True,
        stockage=tmp_path / "mlflow",
        contexte={"mesurer_vram": lambda: 2**30, "parametres": {"essai": "oui"}},
    )
    mlflow.set_tracking_uri(uri_suivi(tmp_path / "mlflow"))
    runs = mlflow.search_runs(
        experiment_names=[REGLAGES["mlflow"]["experience"]], output_format="list"
    )
    assert sorted(r.info.run_name for r in runs) == sorted(
        c["nom"] for c in REGLAGES["configurations"]
    )
    for r in runs:
        assert r.info.status == "FINISHED"
        assert r.data.tags["bloc"] == "D" and r.data.params["essai"] == "oui"
        assert {
            "latence_mediane_s",
            "latence_p95_s",
            "jetons_entree_moyenne",
            "jetons_sortie_moyenne",
            "part_5grammes_copies_moyenne",
            "vram_occupee_debut_mio",
        } <= set(r.data.metrics)
        pieces = {a.path for a in mlflow.MlflowClient().list_artifacts(r.info.run_id)}
        assert {
            "reponses.json",
            "passages.json",
            "invite_generation.toml",
            "generation.toml",
        } <= pieces
        attendus = 2 * int(r.data.params["configuration.repetitions"])
        traces = mlflow.search_traces(run_id=r.info.run_id, return_type="list")
        assert len(traces) == attendus
    base = next(r for r in runs if r.info.run_name == "base")
    assert base.data.metrics["part_repetitions_identiques"] == 1.0


def test_passages_de_bout_en_bout(banc, encodeur_doublure):
    dsn, config, _ = banc
    ctx = executer.ouvrir(dsn, config)
    (q2,) = [x for x in ctx.questions if x.question_id == "Q002"]
    (sortie,) = generation.recuperer_passages(ctx, [q2], mesure=1, nombre=3)
    tete = sortie["passages"][0]
    # Q002 vise le fragment 104, synopsis rattaché à la série 3 par le moyeu.
    assert tete["entite"] == "serie:3" and tete["chunk_id"] == 104
    assert tete["titre"] == "Série 3" and tete["texte"].startswith("A notebook")
    assert [p["rang"] for p in sortie["passages"]] == [1, 2, 3]
    with pytest.raises(ValueError, match="par le sens"):
        generation.recuperer_passages(ctx, [q2], mesure=3, nombre=3)
