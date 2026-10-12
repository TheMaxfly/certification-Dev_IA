"""Études — quels réglages comptent (spec E2 jour 4, blocs C, D, E).

    # Bloc C — sensibilité du TF-IDF, 36 combinaisons :
    uv run python -m mesures_recherche.etudes tfidf \
        --sortie resultats/etudes/tfidf.json --enregistrer
    # Bloc D — les voisins (instance EmbeddingGemma lancée seule) :
    uv run python -m mesures_recherche.etudes voisins \
        --sortie resultats/etudes/voisins.json --enregistrer
    # Bloc E — intention : voisins (instance EmbeddingGemma lancée seule), puis LLM
    # local (instance arrêtée, Ollama) :
    uv run python -m mesures_recherche.etudes intention-voisins \
        --sortie resultats/etudes/intention_voisins.json --enregistrer
    uv run python -m mesures_recherche.etudes intention-llm \
        --sortie resultats/etudes/intention_llm.json --enregistrer

Ce sont des études, pas des mesures : aucune ne désigne de « meilleur réglage »
(une grille essayée sur 59 questions trouve toujours un gagnant, qui doit beaucoup
au hasard). Étiquette `etude` dans MLflow, rien dans `bench.eval_mesures`, aucun
test de significativité. Réglages : `config/etudes.toml`.

Bloc C. Chaque combinaison est évaluée par le harnais des mesures (une mesure
TF-IDF déclarée à la volée, classement au catalogue), dans un processus à part,
sous garde : sous 5 Go de mémoire vive disponible, ou avec du swap écrit dix
secondes d'affilée, le processus est arrêté, la combinaison est sautée et nommée,
l'étude continue. Le résumé donne, pour chaque réglage, la moyenne de `hit_rate@10`
et de `ndcg@10` par valeur, toutes les autres confondues, avec l'étendue ; le
détail de F4 selon l'unité ; le temps de construction et la taille du vocabulaire
par unité. Aucun classement des combinaisons.

Bloc D. Sur le classement de la mesure 8 : `hit_rate` à 1, 3, 5, 10, 20 et 50
résultats, global et par mode ; puis les 10 premières entités de chaque question
selon le produit scalaire, le cosinus et la distance euclidienne, comparées.

Bloc E. Le mode de chaque question (proposition, reconnaissance, refus), prédit par
ses k plus proches voisines (vecteurs EmbeddingGemma), puis par le LLM local
(réponse contrainte aux trois valeurs) ; exactitude, rappel par classe, matrice de
confusion. 69 exemples dont 5 refus : un ordre de grandeur, pas une mesure.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import resource
import statistics
import subprocess  # nosec B404 — le processus enfant est ce module, sans shell
import sys
import tempfile
import time
import tomllib
from pathlib import Path

import numpy as np

from mesures_recherche import configuration, executer
from mesures_recherche.configuration import RACINE_MODULE

FICHIER = RACINE_MODULE / "config" / "etudes.toml"
NUMERO_ETUDE = 900  # numéro de la mesure déclarée à la volée, hors configuration


def charger(chemin: Path = FICHIER) -> dict:
    return tomllib.loads(chemin.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
#  Bloc C — la grille du TF-IDF
# --------------------------------------------------------------------------- #


def combinaisons(etudes: dict) -> list[dict]:
    """Les combinaisons de la grille, unité par unité, dans l'ordre déclaré."""
    t = etudes["tfidf"]
    return [
        {
            "unite": u["nom"],
            "sublinear_tf": sub,
            "min_df": mn,
            "max_df": mx,
        }
        for u, sub, mn, mx in itertools.product(
            t["unites"], t["sublinear_tf"], t["min_df"], t["max_df"]
        )
    ]


def nom_combinaison(c: dict) -> str:
    sub = "sublineaire" if c["sublinear_tf"] else "brut"
    return f"{c['unite']}/{sub}/min_df={c['min_df']}/max_df={c['max_df']}"


def mesure_tfidf(etudes: dict, c: dict, numero: int = NUMERO_ETUDE) -> dict:
    """La combinaison, déclarée comme une mesure TF-IDF du harnais."""
    t = etudes["tfidf"]
    (unite,) = [u for u in t["unites"] if u["nom"] == c["unite"]]
    return {
        "numero": numero,
        "nom": f"etude-tfidf-{nom_combinaison(c)}",
        "tour": 1,
        "type": "tfidf",
        "perimetre_entites": t["perimetre_entites"],
        "lowercase": t["lowercase"],
        "strip_accents": t["strip_accents"],
        "analyzer": unite["analyzer"],
        "ngram_min": unite["ngram_min"],
        "ngram_max": unite["ngram_max"],
        "sublinear_tf": c["sublinear_tf"],
        "min_df": c["min_df"],
        "max_df": c["max_df"],
        "norm": t["norm"],
        "similarite": t["similarite"],
    }


def evaluer(ctx: executer.Contexte, etudes: dict, c: dict) -> dict:
    """Une combinaison, évaluée par le harnais : agrégats, et ce qu'elle coûte."""
    m = mesure_tfidf(etudes, c)
    ctx.config["mesures"] = [
        x for x in ctx.config["mesures"] if x["numero"] != m["numero"]
    ]
    ctx.config["mesures"].append(m)
    ctx.caches.pop(m["numero"], None)
    r = executer.mesurer(ctx, m["numero"])
    return {
        "combinaison": c,
        "agregats": r["agregats"],
        "vocabulaire": r["parametres"]["vocabulaire"],
        "duree_construction_s": r["parametres"]["duree_ajustement_s"],
        "egalites_au_seuil": sum(q["egalite_au_seuil"] for q in r["questions"]),
        "rss_pic_mio": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    }


def valeur(agregats: list[dict], portee: str, metrique: str) -> float | None:
    nom, k = metrique.split("@")
    for a in agregats:
        if (a["portee"], a["perimetre"], a["metrique"], a["k"]) == (
            portee,
            "toutes",
            nom,
            int(k),
        ):
            return a["valeur"]
    return None


def _etendue(valeurs: list[float]) -> dict:
    return {
        "moyenne": statistics.fmean(valeurs),
        "min": min(valeurs),
        "max": max(valeurs),
        "n": len(valeurs),
    }


def resumer_grille(resultats: list[dict]) -> dict:
    """Par réglage et par valeur : moyenne et étendue de hit_rate@10 et ndcg@10,
    toutes les autres valeurs confondues ; F4 et coûts par unité. Les combinaisons
    sautées n'entrent dans aucune moyenne."""
    faits = [r for r in resultats if "agregats" in r]
    sortie: dict = {"reglages": {}, "f4_par_unite": {}, "cout_par_unite": {}}
    for reglage in ("unite", "sublinear_tf", "min_df", "max_df"):
        par_valeur: dict = {}
        for r in faits:
            par_valeur.setdefault(str(r["combinaison"][reglage]), []).append(r)
        sortie["reglages"][reglage] = {
            v: {
                m: _etendue([valeur(r["agregats"], "global", m) for r in rs])
                for m in ("hit_rate@10", "ndcg@10")
            }
            for v, rs in par_valeur.items()
        }
    par_unite: dict = {}
    for r in faits:
        par_unite.setdefault(r["combinaison"]["unite"], []).append(r)
    for u, rs in par_unite.items():
        sortie["f4_par_unite"][u] = {
            m: _etendue([valeur(r["agregats"], "famille:F4", m) for r in rs])
            for m in ("hit_rate@10", "ndcg@10", "mrr@10")
        }
        sortie["cout_par_unite"][u] = {
            "duree_construction_s": _etendue([r["duree_construction_s"] for r in rs]),
            "vocabulaire": _etendue([r["vocabulaire"] for r in rs]),
            "rss_pic_mio": _etendue([r["rss_pic_mio"] for r in rs]),
        }
    sortie["sautees"] = [
        {"combinaison": nom_combinaison(r["combinaison"]), "motif": r["motif"]}
        for r in resultats
        if "agregats" not in r
    ]
    return sortie


# --------------------------------------------------------------------------- #
#  Bloc D — les voisins : profondeur et mesure de distance
# --------------------------------------------------------------------------- #


def hit_rate_profondeurs(rangs: dict[str, int], profondeurs: list[int]) -> dict:
    """hit_rate@k pour chaque profondeur, à partir du rang de la première série
    attendue de chaque question de rang."""
    return {k: sum(r <= k for r in rangs.values()) / len(rangs) for k in profondeurs}


def scores_distance(matrice: np.ndarray, q: np.ndarray, distance: str) -> np.ndarray:
    """Score de chaque fragment (plus grand = plus proche) selon la distance."""
    if distance == "produit_scalaire":
        return matrice @ q
    if distance == "cosinus":
        return (matrice @ q) / (np.linalg.norm(matrice, axis=1) * np.linalg.norm(q))
    if distance == "euclidienne":
        return -np.linalg.norm(matrice - q, axis=1)
    raise ValueError(f"distance inconnue : {distance!r}")


def etudier_voisins(ctx: executer.Contexte, etudes: dict) -> dict:
    from mesures_recherche import recherche

    v = etudes["voisins"]
    m = configuration.mesure(ctx.config, v["mesure"])
    perimetre = configuration.perimetre(m)
    sens, encoder, params = executer.semantique(ctx, m)
    rangs, listes = {}, []
    for q in ctx.questions:
        vec = np.asarray(encoder(q.texte), dtype=np.float64)
        tops = {}
        for d in v["distances"]:
            fragments = scores_distance(sens.matrice, vec, d)
            c = recherche.classer(
                ctx.entites, ctx.entites.agreger(fragments), perimetre
            )
            tops[d] = [cle for cle, _ in c.top(ctx.entites, 10)]
            if d == v["reference"] and q.de_rang:
                rangs[q.question_id] = min(
                    c.rang(ctx.entites.index_serie[s])
                    for s in q.attendus
                    if s in ctx.entites.index_serie
                )
        identiques = all(tops[d] == tops[v["reference"]] for d in v["distances"])
        listes.append(
            {
                "question_id": q.question_id,
                "mode": q.mode,
                "identiques": identiques,
                **{f"top_{d}": tops[d] for d in v["distances"]},
            }
        )
    modes = {}
    for q in ctx.questions:
        if q.question_id in rangs:
            modes.setdefault(q.mode, {})[q.question_id] = rangs[q.question_id]
    return {
        "mesure": v["mesure"],
        "perimetre_entites": perimetre,
        "service": params,
        "hit_rate": {
            "global": hit_rate_profondeurs(rangs, v["profondeurs"]),
            **{
                f"mode:{mo}": hit_rate_profondeurs(r, v["profondeurs"])
                for mo, r in sorted(modes.items())
            },
        },
        "rangs": rangs,
        "distances": {
            "identiques": sum(x["identiques"] for x in listes),
            "questions": len(listes),
            "differentes": [x["question_id"] for x in listes if not x["identiques"]],
        },
        "listes": listes,
    }


# --------------------------------------------------------------------------- #
#  Bloc E — classification d'intention
# --------------------------------------------------------------------------- #

MODES = ("proposition", "reconnaissance", "refus")


def voter(voisines: list[tuple[str, float]]) -> str:
    """Vote des voisines (classe, similarité), de la plus proche à la moins proche :
    la classe la plus représentée ; à égalité de voix, celle dont la voisine la
    plus proche est la plus proche."""
    voix: dict[str, int] = {}
    for classe, _ in voisines:
        voix[classe] = voix.get(classe, 0) + 1
    maximum = max(voix.values())
    for classe, _ in voisines:  # déjà triées par similarité décroissante
        if voix[classe] == maximum:
            return classe
    raise AssertionError("vote vide")


def plus_proches_voisins(
    vecteurs: np.ndarray, etiquettes: list[str], k: int
) -> list[str]:
    """Chaque question classée par ses k plus proches voisines parmi les autres
    (cosinus ; égalités de similarité départagées par l'ordre des questions)."""
    v = vecteurs / np.linalg.norm(vecteurs, axis=1, keepdims=True)
    sim = v @ v.T
    predictions = []
    for i in range(len(etiquettes)):
        autres = [j for j in range(len(etiquettes)) if j != i]
        ordre = sorted(autres, key=lambda j: (-sim[i, j], j))[:k]
        predictions.append(voter([(etiquettes[j], float(sim[i, j])) for j in ordre]))
    return predictions


def evaluer_classement(etiquettes: list[str], predictions: list[str | None]) -> dict:
    """Exactitude, rappel par classe, matrice de confusion (lignes : vraie classe ;
    colonnes : classe prédite, plus « hors valeurs »)."""
    colonnes = list(MODES) + ["hors_valeurs"]
    matrice = {vraie: dict.fromkeys(colonnes, 0) for vraie in MODES}
    for vraie, predite in zip(etiquettes, predictions, strict=True):
        matrice[vraie][predite if predite in MODES else "hors_valeurs"] += 1
    effectifs = {c: sum(matrice[c].values()) for c in MODES}
    return {
        "questions": len(etiquettes),
        "exactitude": sum(e == p for e, p in zip(etiquettes, predictions, strict=True))
        / len(etiquettes),
        "rappel": {c: matrice[c][c] / effectifs[c] for c in MODES if effectifs[c]},
        "effectifs": effectifs,
        "hors_valeurs": sum(m["hors_valeurs"] for m in matrice.values()),
        "matrice": matrice,
    }


def lire_mode(reponse: str) -> str | None:
    """Le mode d'une réponse du modèle (`{"mode": …}`), ou None hors des trois."""
    try:
        valeur = json.loads(reponse).get("mode")
    except (ValueError, AttributeError):
        return None
    return valeur if valeur in MODES else None


SCHEMA_MODE = {
    "type": "object",
    "properties": {"mode": {"type": "string", "enum": list(MODES)}},
    "required": ["mode"],
}


def classer_par_voisins(ctx: executer.Contexte, etudes: dict) -> dict:
    """Méthode 1 : les vecteurs EmbeddingGemma des questions (service), puis le vote
    des k plus proches voisines, pour chaque k déclaré."""
    cfg = etudes["intention"]
    # Les questions seules sont encodées : aucun vecteur du corpus n'est lu, le
    # service est confronté à la configuration de l'instance.
    encoder, params = executer.encodeur_du_service(
        executer.Designation(cfg["voisins"]["instance"])
    )
    ids = [q.question_id for q in ctx.questions]
    etiquettes = [q.mode for q in ctx.questions]
    vecteurs = np.vstack(
        [np.asarray(encoder(q.texte), dtype=np.float64) for q in ctx.questions]
    )
    runs = []
    for k in cfg["voisins"]["k"]:
        predictions = plus_proches_voisins(vecteurs, etiquettes, k)
        runs.append(
            {
                "methode": "voisins",
                "k": k,
                "predictions": dict(zip(ids, predictions, strict=True)),
                **evaluer_classement(etiquettes, predictions),
            }
        )
    return {
        "service": params,
        "etiquettes": dict(zip(ids, etiquettes, strict=True)),
        "runs": runs,
    }


def classer_par_llm(questions, etudes: dict, modele, invite: dict, tracer=None) -> dict:
    """Méthode 2 : une question par appel, température 0, réponse contrainte aux
    trois valeurs par un schéma JSON."""
    from mesures_recherche.generation import verifier_contexte

    cfg = etudes["intention"]["llm"]
    opts = {
        "temperature": cfg["temperature"],
        "seed": cfg["graine"],
        "num_predict": cfg["num_predict"],
        "num_ctx": cfg["num_ctx"],
    }
    appels = []
    for q in questions:
        msgs = [
            {"role": "system", "content": invite["systeme"]},
            {"role": "user", "content": invite["utilisateur"].format(question=q.texte)},
        ]
        appel = (tracer or (lambda _n, _m, _o, f: f()))(
            f"intention/{q.question_id}",
            msgs,
            opts,
            lambda msgs=msgs: modele.discuter(msgs, opts, SCHEMA_MODE),
        )
        verifier_contexte(appel, cfg)
        appels.append(
            {
                "question_id": q.question_id,
                "mode": q.mode,
                "prediction": lire_mode(appel["reponse"]),
                **appel,
            }
        )
    etiquettes = [a["mode"] for a in appels]
    predictions = [a["prediction"] for a in appels]
    latences = [a["latence_s"] for a in appels]
    return {
        "methode": "llm",
        "predictions": {a["question_id"]: a["prediction"] for a in appels},
        "appels": appels,
        "latence_mediane_s": float(np.median(latences)),
        "latence_p95_s": float(np.percentile(latences, 95)),
        "jetons_entree_moyenne": statistics.fmean(a["jetons_entree"] for a in appels),
        "jetons_sortie_moyenne": statistics.fmean(a["jetons_sortie"] for a in appels),
        **evaluer_classement(etiquettes, predictions),
    }


def matrice_markdown(matrice: dict) -> str:
    colonnes = list(MODES) + ["hors_valeurs"]
    lignes = [
        "| vraie \\ prédite | " + " | ".join(colonnes) + " |",
        "|---|" + "---:|" * len(colonnes),
    ]
    lignes += [
        f"| {v} | " + " | ".join(str(matrice[v][c]) for c in colonnes) + " |"
        for v in MODES
    ]
    return "\n".join(lignes) + "\n"


def journaliser_classement(
    mlflow, etudes: dict, r: dict, etiquettes: list[str], parametres: dict
) -> None:
    """Dans le run actif : paramètres, métriques, matrice de confusion, prédictions."""
    cfg = etudes["intention"]
    naive = sum(m == cfg["reference_naive"] for m in etiquettes) / len(etiquettes)
    mlflow.set_tags(
        {"etude": cfg["nom"], "spec": "E2 jour 4", "bloc": "E", "methode": r["methode"]}
    )
    mlflow.log_params(
        {"methode": r["methode"], "reference_naive": cfg["reference_naive"]}
        | ({"k": r["k"]} if "k" in r else {})
        | parametres
    )
    metriques = {
        "exactitude": r["exactitude"],
        "reference_naive_exactitude": naive,
        "questions": r["questions"],
        "hors_valeurs": r["hors_valeurs"],
        **{f"rappel_{c}": v for c, v in r["rappel"].items()},
    }
    for cle in (
        "latence_mediane_s",
        "latence_p95_s",
        "jetons_entree_moyenne",
        "jetons_sortie_moyenne",
    ):
        if cle in r:
            metriques[cle] = r[cle]
    mlflow.log_metrics(metriques)
    mlflow.log_text(
        json.dumps(r["matrice"], ensure_ascii=False, indent=1), "matrice_confusion.json"
    )
    mlflow.log_text(matrice_markdown(r["matrice"]), "matrice_confusion.md")
    mlflow.log_text(
        json.dumps(r["predictions"], ensure_ascii=False, indent=1), "predictions.json"
    )


# --------------------------------------------------------------------------- #
#  La garde de mémoire
# --------------------------------------------------------------------------- #


def ram_disponible() -> int:
    from service_embedding.mesures import meminfo

    return meminfo()["MemAvailable"]


def pages_swap_ecrites() -> int:
    from service_embedding.mesures import pages_swap_ecrites as lire

    return lire()


def sous_garde(
    commande: list[str],
    ram_min: int,
    swap_continu_s: float,
    lire_ram=ram_disponible,
    lire_swap=pages_swap_ecrites,
    periode: float = 0.5,
) -> dict:
    """Lance la commande et l'arrête si la mémoire vive disponible passe sous
    `ram_min`, ou si du swap s'écrit `swap_continu_s` secondes d'affilée."""
    debut = time.monotonic()
    ram_min_vue = lire_ram()
    swap_avant, swap_depuis = lire_swap(), None
    with subprocess.Popen(  # nosec B603 — commande construite ici, sans shell
        commande, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as p:
        motif = None
        while p.poll() is None:
            time.sleep(periode)
            ram = lire_ram()
            ram_min_vue = min(ram_min_vue, ram)
            swap = lire_swap()
            if swap > swap_avant:
                swap_depuis = swap_depuis or time.monotonic()
            else:
                swap_depuis = None
            swap_avant = swap
            if ram < ram_min:
                motif = (
                    f"mémoire vive disponible {ram / 1e9:.2f} Go"
                    f" < {ram_min / 1e9:.0f} Go"
                )
            elif swap_depuis and time.monotonic() - swap_depuis >= swap_continu_s:
                motif = f"swap écrit {swap_continu_s:g} s d'affilée"
            if motif:
                p.kill()
                p.wait()
                break
        _, erreur = p.communicate()
    return {
        "code": p.returncode,
        "motif": motif,
        "erreur": None if p.returncode == 0 else erreur[-2000:],
        "duree_s": time.monotonic() - debut,
        "ram_disponible_min": ram_min_vue,
    }


# --------------------------------------------------------------------------- #
#  MLflow : un run parent, un run enfant par combinaison
# --------------------------------------------------------------------------- #


def _experience(etudes: dict, stockage: Path | None, nom: str | None = None):
    import mlflow

    from mesures_recherche.enregistrement import STOCKAGE, uri_suivi

    stockage = stockage or STOCKAGE
    stockage.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(uri_suivi(stockage))
    nom = nom or etudes["mlflow"]["experience"]
    if mlflow.get_experiment_by_name(nom) is None:
        mlflow.create_experiment(nom, artifact_location=(stockage / "pieces").as_uri())
    mlflow.set_experiment(nom)
    return mlflow


def metriques_mlflow(agregats: list[dict]) -> dict[str, float]:
    return {
        f"{a['metrique']}_{a['k']}/{a['portee']}": a["valeur"]
        for a in agregats
        if a["perimetre"] == "toutes"
    }


def journaliser_grille(
    etudes, resultats, resume, parametres, stockage: Path | None = None
) -> str:
    mlflow = _experience(etudes, stockage)
    t = etudes["tfidf"]
    with mlflow.start_run(run_name="grille-tfidf") as parent:
        mlflow.set_tags({"etude": t["nom"], "spec": "E2 jour 4", "bloc": "C"})
        mlflow.log_params(
            {
                "combinaisons": len(resultats),
                "sautees": len(resume["sautees"]),
                "perimetre_entites": t["perimetre_entites"],
                "unites": json.dumps([u["nom"] for u in t["unites"]]),
                "sublinear_tf": json.dumps(t["sublinear_tf"]),
                "min_df": json.dumps(t["min_df"]),
                "max_df": json.dumps(t["max_df"]),
            }
            | parametres
        )
        mlflow.log_text(json.dumps(resume, ensure_ascii=False, indent=1), "resume.json")
        mlflow.log_artifact(str(FICHIER))
        for r in resultats:
            c = r["combinaison"]
            mlflow.start_run(run_name=nom_combinaison(c), nested=True)
            mlflow.set_tags({"etude": t["nom"], "spec": "E2 jour 4", "bloc": "C"})
            mlflow.log_params({k: str(v) for k, v in c.items()})
            if "agregats" in r:
                mlflow.log_metrics(
                    metriques_mlflow(r["agregats"])
                    | {
                        "vocabulaire": r["vocabulaire"],
                        "duree_construction_s": r["duree_construction_s"],
                        "rss_pic_mio": r["rss_pic_mio"],
                        "egalites_au_seuil": r["egalites_au_seuil"],
                    }
                )
                mlflow.end_run()
            else:
                mlflow.set_tags({"sautee": "oui", "motif": r["motif"]})
                mlflow.end_run(status="KILLED")
        return parent.info.run_id


def journaliser_voisins(etudes, resultat, parametres, stockage: Path | None = None):
    mlflow = _experience(etudes, stockage)
    v = etudes["voisins"]
    with mlflow.start_run(run_name="voisins-profondeur-distance") as run:
        mlflow.set_tags({"etude": v["nom"], "spec": "E2 jour 4", "bloc": "D"})
        mlflow.log_params(
            {
                "mesure": v["mesure"],
                "perimetre_entites": resultat["perimetre_entites"],
                "profondeurs": json.dumps(v["profondeurs"]),
                "distances": json.dumps(v["distances"]),
                "reference": v["reference"],
            }
            | {k: str(x) for k, x in resultat["service"].items()}
            | parametres
        )
        mlflow.log_metrics(
            {
                f"hit_rate_{k}/{portee}": x
                for portee, par_k in resultat["hit_rate"].items()
                for k, x in par_k.items()
            }
            | {
                "distances_listes_identiques": resultat["distances"]["identiques"],
                "distances_questions": resultat["distances"]["questions"],
            }
        )
        mlflow.log_text(
            json.dumps(resultat, ensure_ascii=False, indent=1), "voisins.json"
        )
        mlflow.log_artifact(str(FICHIER))
        return run.info.run_id


# --------------------------------------------------------------------------- #
#  Ligne de commande
# --------------------------------------------------------------------------- #


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL absente")
    return dsn


def _intention(args, etudes, empreinte_code, etat_git) -> int:
    """Bloc E : la méthode 1 (instance EmbeddingGemma lancée seule) ou la méthode 2
    (instance arrêtée ; Ollama, modèle entier sur la carte)."""
    from mesures_recherche.generation import (
        Ollama,
        charger_toml,
        empreinte,
        memoire_video,
        tracer_mlflow,
    )

    cfg = etudes["intention"]
    ctx = executer.ouvrir(_dsn(), configuration.charger())
    questions = ctx.questions
    etiquettes = [q.mode for q in questions]
    parametres = {
        "etudes_empreinte": configuration.empreinte(FICHIER),
        "code_empreinte": empreinte_code(),
        **etat_git(),
    }
    mlflow = _experience(etudes, None, cfg["experience"]) if args.enregistrer else None
    try:
        if args.commande == "intention-voisins":
            resultat = classer_par_voisins(ctx, etudes)
            parametres |= {
                f"service_{k}": str(v) for k, v in resultat["service"].items()
            }
            for r in resultat["runs"]:
                if mlflow:
                    with mlflow.start_run(run_name=f"voisins-k={r['k']}") as run:
                        journaliser_classement(
                            mlflow, etudes, r, etiquettes, parametres
                        )
                        r["run"] = run.info.run_id
        else:
            llm = cfg["llm"]
            chemin_invite = RACINE_MODULE / llm["invite"]
            invite = charger_toml(chemin_invite)
            modele = Ollama(llm["url"], llm["modele"], llm["keep_alive"])
            parametres |= modele.description() | {
                "invite_empreinte": empreinte(chemin_invite),
                **{f"llm_{k}": str(v) for k, v in llm.items() if k != "url"},
            }
            debut = time.monotonic()
            modele.discuter(
                [{"role": "user", "content": "Bonjour."}],
                {"temperature": 0, "num_predict": 1, "num_ctx": llm["num_ctx"]},
            )
            charge = modele.charge() | {
                "vram_occupee": memoire_video(),
                "chargement_s": time.monotonic() - debut,
            }
            if charge["taille_memoire_video"] < charge["taille"]:
                modele.decharger()
                raise SystemExit(f"modèle pas entièrement sur la carte : {charge}")
            parametres |= {f"charge_{k}": str(v) for k, v in charge.items()}
            try:
                if mlflow:
                    with mlflow.start_run(run_name="llm-ministral-3") as run:
                        r = classer_par_llm(
                            questions, etudes, modele, invite, tracer_mlflow
                        )
                        journaliser_classement(
                            mlflow, etudes, r, etiquettes, parametres
                        )
                        mlflow.log_text(
                            json.dumps(r["appels"], ensure_ascii=False, indent=1),
                            "appels.json",
                        )
                        mlflow.log_artifact(str(chemin_invite))
                        mlflow.flush_trace_async_logging()
                        r["run"] = run.info.run_id
                else:
                    r = classer_par_llm(questions, etudes, modele, invite)
            finally:
                modele.decharger()
            resultat = {"charge": charge, "runs": [r]}
    finally:
        ctx.cx.close()
    resultat |= {"parametres": parametres}
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(json.dumps(resultat, ensure_ascii=False, indent=1), "utf-8")
    for r in resultat["runs"]:
        resume = {
            k: r.get(k)
            for k in (
                "methode",
                "k",
                "exactitude",
                "rappel",
                "hors_valeurs",
                "latence_mediane_s",
                "run",
            )
        }
        print(json.dumps(resume, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sous = parser.add_subparsers(dest="commande", required=True)
    t = sous.add_parser("tfidf", help="bloc C : la grille, sous garde de mémoire")
    t.add_argument("--sortie", type=Path, required=True)
    t.add_argument("--enregistrer", action="store_true")
    vo = sous.add_parser("voisins", help="bloc D : profondeur et distances")
    vo.add_argument("--sortie", type=Path, required=True)
    vo.add_argument("--enregistrer", action="store_true")
    iv = sous.add_parser("intention-voisins", help="bloc E : plus proches voisins")
    iv.add_argument("--sortie", type=Path, required=True)
    iv.add_argument("--enregistrer", action="store_true")
    il = sous.add_parser("intention-llm", help="bloc E : LLM local, Ollama")
    il.add_argument("--sortie", type=Path, required=True)
    il.add_argument("--enregistrer", action="store_true")
    u = sous.add_parser("tfidf-une", help="une combinaison (processus enfant)")
    u.add_argument("--combinaison", required=True)
    u.add_argument("--sortie", type=Path, required=True)
    args = parser.parse_args(argv)
    etudes = charger()

    if args.commande == "tfidf-une":
        ctx = executer.ouvrir(_dsn(), configuration.charger())
        try:
            r = evaluer(ctx, etudes, json.loads(args.combinaison))
        finally:
            ctx.cx.close()
        args.sortie.write_text(json.dumps(r, ensure_ascii=False), "utf-8")
        return 0

    from mesures_recherche.enregistrement import empreinte_code, etat_git

    if args.commande in ("intention-voisins", "intention-llm"):
        return _intention(args, etudes, empreinte_code, etat_git)

    if args.commande == "voisins":
        ctx = executer.ouvrir(_dsn(), configuration.charger())
        try:
            resultat = etudier_voisins(ctx, etudes)
        finally:
            ctx.cx.close()
        resultat |= {
            "etudes_empreinte": configuration.empreinte(FICHIER),
            "code_empreinte": empreinte_code(),
        }
        if args.enregistrer:
            resultat["run"] = journaliser_voisins(
                etudes,
                resultat,
                {
                    "etudes_empreinte": resultat["etudes_empreinte"],
                    "code_empreinte": resultat["code_empreinte"],
                    **etat_git(),
                },
            )
        args.sortie.parent.mkdir(parents=True, exist_ok=True)
        args.sortie.write_text(
            json.dumps(resultat, ensure_ascii=False, indent=1), "utf-8"
        )
        print(
            json.dumps(
                {
                    "hit_rate": resultat["hit_rate"],
                    "distances": resultat["distances"],
                    "run": resultat.get("run"),
                },
                ensure_ascii=False,
            )
        )
        return 0

    _dsn()
    mem = etudes["memoire"]
    resultats = []
    with tempfile.TemporaryDirectory() as dossier:
        for i, c in enumerate(combinaisons(etudes), start=1):
            sortie = Path(dossier) / f"{i}.json"
            commande = [
                sys.executable,
                "-m",
                "mesures_recherche.etudes",
                "tfidf-une",
                "--combinaison",
                json.dumps(c),
                "--sortie",
                str(sortie),
            ]
            if ram_disponible() < mem["ram_disponible_min"]:
                garde = {"motif": "mémoire vive sous le seuil avant le lancement"}
            else:
                garde = sous_garde(
                    commande, mem["ram_disponible_min"], mem["swap_continu_s"]
                )
            if garde.get("motif") is None and garde.get("code") == 0:
                r = json.loads(sortie.read_text(encoding="utf-8"))
                r["ram_disponible_min"] = garde["ram_disponible_min"]
                r["duree_processus_s"] = garde["duree_s"]
            elif garde.get("motif") is None:
                raise RuntimeError(f"{nom_combinaison(c)} : échec — {garde['erreur']}")
            else:
                r = {"combinaison": c, "motif": garde["motif"]}
            resultats.append(r)
            print(
                json.dumps(
                    {
                        "combinaison": nom_combinaison(c),
                        "hit_rate@10": valeur(r["agregats"], "global", "hit_rate@10")
                        if "agregats" in r
                        else None,
                        "vocabulaire": r.get("vocabulaire"),
                        "duree_s": r.get("duree_processus_s"),
                        "sautee": r.get("motif"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    resume = resumer_grille(resultats)
    sortie = {
        "etude": etudes["tfidf"]["nom"],
        "etudes_empreinte": configuration.empreinte(FICHIER),
        "code_empreinte": empreinte_code(),
        "resultats": resultats,
        "resume": resume,
    }
    if args.enregistrer:
        sortie["run_parent"] = journaliser_grille(
            etudes,
            resultats,
            resume,
            {
                "etudes_empreinte": sortie["etudes_empreinte"],
                "code_empreinte": sortie["code_empreinte"],
                **etat_git(),
            },
        )
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(json.dumps(sortie, ensure_ascii=False, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
