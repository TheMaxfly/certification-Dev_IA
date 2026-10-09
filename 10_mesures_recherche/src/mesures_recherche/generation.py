"""Réglages de la génération — démonstration, pas une mesure de qualité.

    # 1. passages, l'instance EmbeddingGemma du service lancée seule :
    uv run python -m mesures_recherche.generation passages \
        --sortie resultats/generation/passages.json
    # 2. génération, l'instance arrêtée, Ollama sur la carte graphique :
    uv run python -m mesures_recherche.generation generer \
        --passages resultats/generation/passages.json \
        --sortie resultats/generation/generations.json --enregistrer

Questions : la première, par identifiant, de chacune des onze familles. Passages :
ceux de la mesure 2 — les entités de tête et, pour chacune, son meilleur fragment —,
récupérés une fois et enregistrés ; les 10 premières entités sont confrontées au run
MLflow de la mesure 2. Puis, configuration par configuration (température, nombre
de passages, répétitions ; `config/generation.toml`), un appel au modèle par
question et par répétition, avec l'invite versionnée (`config/invite_generation.toml`).

Aucun juge, aucune note, rien dans `bench.eval_mesures`. Avec `--enregistrer` : une
expérience MLflow à part, un run par configuration — paramètres, métriques
(latence médiane et p95, jetons en entrée et en sortie, part des répétitions
identiques, part des séquences de 5 mots de la réponse présentes dans les
passages), pièces (réponses, passages, invite) ; chaque appel est tracé (span MLflow).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mesures_recherche.configuration import RACINE_MODULE

REGLAGES = RACINE_MODULE / "config" / "generation.toml"
INVITE = RACINE_MODULE / "config" / "invite_generation.toml"


class ArretGeneration(Exception):
    """La série ne peut pas continuer sans fausser ce qu'elle montre."""


def charger_toml(chemin: Path) -> dict:
    return tomllib.loads(chemin.read_text(encoding="utf-8"))


def empreinte(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
#  Questions et passages
# --------------------------------------------------------------------------- #


def premieres_par_famille(questions) -> list:
    """La première question, par identifiant, de chaque famille, familles dans
    l'ordre de leur numéro."""
    retenues: dict[str, object] = {}
    for q in sorted(questions, key=lambda q: q.question_id):
        retenues.setdefault(q.famille, q)
    return [retenues[f] for f in sorted(retenues, key=lambda f: int(f[1:]))]


def meilleur_fragment(entites, scores_fragments: np.ndarray, entite: int) -> int:
    """Position du meilleur fragment d'une entité ; à égalité, le plus petit
    chunk_id (les positions suivent l'ordre croissant des chunk_id)."""
    positions = np.flatnonzero(entites.entite == entite)
    return int(positions[np.argmax(scores_fragments[positions])])


def recuperer_passages(ctx, questions, mesure: int, nombre: int) -> list[dict]:
    """Pour chaque question, les `nombre` premières entités de la mesure (sens) et
    le meilleur fragment de chacune : texte, titre de l'entité, scores."""
    from mesures_recherche import configuration, diagnostic, executer, recherche

    m = configuration.mesure(ctx.config, mesure)
    if m["type"] != "semantique":
        raise ValueError(f"mesure {mesure} : passages par le sens seulement")
    sens, encoder, _ = executer.semantique(ctx, m)
    sortie = []
    for q in questions:
        vecteur = np.asarray(encoder(q.texte), dtype=np.float64)
        fragments = sens.matrice @ vecteur
        c = recherche.classer(ctx.entites, ctx.entites.agreger(fragments))
        tete = c.ordre[:nombre]
        positions = [meilleur_fragment(ctx.entites, fragments, int(e)) for e in tete]
        chunk_ids = [int(ctx.entites.chunk_ids[p]) for p in positions]
        textes = dict(
            ctx.cx.execute(
                "SELECT chunk_id, chunk_text FROM bench.corpus_chunks"
                " WHERE corpus_id = %s AND chunk_id = ANY(%s)",
                (ctx.entites.corpus_id, chunk_ids),
            ).fetchall()
        )
        cles = [ctx.entites.cle(int(e)) for e in tete]
        titres = diagnostic.titres(ctx.cx, cles, ctx.entites.corpus_id)
        sortie.append(
            {
                "question_id": q.question_id,
                "famille": q.famille,
                "mode": q.mode,
                "texte": q.texte,
                "passages": [
                    {
                        "rang": i,
                        "entite": cle,
                        "titre": titres[cle],
                        "score_entite": float(c.scores[e]),
                        "chunk_id": cid,
                        "score_fragment": float(fragments[p]),
                        "texte": textes[cid],
                    }
                    for i, (cle, e, cid, p) in enumerate(
                        zip(cles, tete, chunk_ids, positions, strict=True), start=1
                    )
                ],
            }
        )
    return sortie


# --------------------------------------------------------------------------- #
#  Invite et appel au modèle
# --------------------------------------------------------------------------- #


def messages(invite: dict, question: str, passages: list[dict]) -> list[dict]:
    rendus = "\n\n".join(
        invite["passage"].format(
            rang=p["rang"], titre=p["titre"] or p["entite"], texte=p["texte"]
        )
        for p in passages
    )
    return [
        {"role": "system", "content": invite["systeme"]},
        {
            "role": "user",
            "content": invite["utilisateur"].format(passages=rendus, question=question),
        },
    ]


def graine(moteur: dict, repetition: int) -> int:
    return moteur["graine"] + repetition - 1


def options(moteur: dict, configuration: dict, repetition: int) -> dict:
    return {
        "temperature": configuration["temperature"],
        "seed": graine(moteur, repetition),
        "num_predict": moteur["num_predict"],
        "num_ctx": moteur["num_ctx"],
    }


def _requete(url: str, corps: dict | None = None, delai: float = 600.0) -> dict:
    donnees = None if corps is None else json.dumps(corps).encode("utf-8")
    requete = urllib.request.Request(  # nosec B310 — moteur local, URL de la config
        url,
        data=donnees,
        headers={"Content-Type": "application/json"} if donnees else {},
        method="GET" if donnees is None else "POST",
    )
    with urllib.request.urlopen(requete, timeout=delai) as reponse:  # nosec B310
        return json.loads(reponse.read())


@dataclass
class Ollama:
    url: str
    modele: str
    keep_alive: str

    def discuter(
        self, msgs: list[dict], opts: dict, format: dict | None = None
    ) -> dict:
        """Un appel ; `format` (schéma JSON) contraint la réponse, s'il est donné."""
        debut = time.monotonic()
        corps = {
            "model": self.modele,
            "messages": msgs,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": opts,
        }
        if format is not None:
            corps["format"] = format
        r = _requete(f"{self.url}/api/chat", corps)
        return {
            "reponse": r["message"]["content"],
            "done_reason": r.get("done_reason"),
            "jetons_entree": r.get("prompt_eval_count"),
            "jetons_sortie": r.get("eval_count"),
            "latence_s": time.monotonic() - debut,
            "duree_totale_s": r.get("total_duration", 0) / 1e9,
            "duree_chargement_s": r.get("load_duration", 0) / 1e9,
        }

    def decharger(self) -> None:
        _requete(f"{self.url}/api/generate", {"model": self.modele, "keep_alive": 0})

    def description(self) -> dict:
        version = _requete(f"{self.url}/api/version")["version"]
        modeles = _requete(f"{self.url}/api/tags")["models"]
        (m,) = [x for x in modeles if x["name"] == self.modele]
        return {
            "ollama_version": version,
            "modele_digest": m["digest"],
            "modele_quantification": m["details"]["quantization_level"],
            "modele_taille_parametres": m["details"]["parameter_size"],
        }

    def charge(self) -> dict:
        """Ce que le moteur dit du modèle chargé : taille, part en mémoire vidéo."""
        (m,) = [
            x
            for x in _requete(f"{self.url}/api/ps")["models"]
            if x["name"] == self.modele
        ]
        return {
            "taille": m["size"],
            "taille_memoire_video": m["size_vram"],
            "contexte": m.get("context_length"),
        }


def verifier_contexte(appel: dict, moteur: dict) -> None:
    """L'entrée doit laisser la place de la réponse : sinon le moteur tronque."""
    if appel["jetons_entree"] + moteur["num_predict"] > moteur["num_ctx"]:
        raise ArretGeneration(
            f"entrée de {appel['jetons_entree']} jetons : plus de place pour "
            f"{moteur['num_predict']} jetons de réponse dans {moteur['num_ctx']}"
        )


# --------------------------------------------------------------------------- #
#  Ce qu'on rapporte d'une configuration
# --------------------------------------------------------------------------- #


def mots(texte: str) -> list[str]:
    return re.findall(r"\w+", texte.lower())


def ngrammes(texte: str, n: int) -> list[tuple[str, ...]]:
    m = mots(texte)
    return [tuple(m[i : i + n]) for i in range(len(m) - n + 1)]


def part_copiee(reponse: str, passages: list[str], n: int) -> float | None:
    """Part des séquences de n mots de la réponse présentes dans les passages ;
    None si la réponse a moins de n mots."""
    sequences = ngrammes(reponse, n)
    if not sequences:
        return None
    sources = {g for p in passages for g in ngrammes(p, n)}
    return sum(g in sources for g in sequences) / len(sequences)


def repetitions(generations: list[dict]) -> dict | None:
    """Sur les questions répétées : part de celles dont toutes les réponses sont
    identiques, et nombre moyen de réponses distinctes."""
    par_question: dict[str, list[str]] = {}
    for g in generations:
        par_question.setdefault(g["question_id"], []).append(g["reponse"])
    if not par_question or max(len(v) for v in par_question.values()) < 2:
        return None
    distinctes = [len(set(v)) for v in par_question.values()]
    return {
        "part_repetitions_identiques": sum(d == 1 for d in distinctes)
        / len(distinctes),
        "reponses_distinctes_moyenne": statistics.fmean(distinctes),
        "questions_non_identiques": sorted(
            q for q, v in par_question.items() if len(set(v)) > 1
        ),
    }


def resumer(generations: list[dict], n: int) -> dict:
    latences = [g["latence_s"] for g in generations]
    entree = [g["jetons_entree"] for g in generations]
    sortie = [g["jetons_sortie"] for g in generations]
    copies = [g["part_copiee"] for g in generations if g["part_copiee"] is not None]
    resume = {
        "generations": len(generations),
        "latence_mediane_s": float(np.median(latences)),
        "latence_p95_s": float(np.percentile(latences, 95)),
        "jetons_entree_moyenne": statistics.fmean(entree),
        "jetons_entree_total": sum(entree),
        "jetons_sortie_moyenne": statistics.fmean(sortie),
        "jetons_sortie_total": sum(sortie),
        f"part_{n}grammes_copies_moyenne": statistics.fmean(copies) if copies else 0.0,
        f"part_{n}grammes_copies_max": max(copies) if copies else 0.0,
        "reponses_trop_courtes": len(generations) - len(copies),
        "reponses_tronquees": sum(g["done_reason"] == "length" for g in generations),
    }
    rep = repetitions(generations)
    if rep is not None:
        resume |= {k: v for k, v in rep.items() if k != "questions_non_identiques"}
        resume["questions_non_identiques"] = rep["questions_non_identiques"]
    return resume


# --------------------------------------------------------------------------- #
#  La série
# --------------------------------------------------------------------------- #


def executer_configuration(
    moteur, modele, invite, configuration, passages, n, tracer=None
) -> list[dict]:
    """Répétition par répétition, les questions dans l'ordre : un appel chacune."""
    generations = []
    for repetition in range(1, configuration["repetitions"] + 1):
        for q in passages:
            fournis = q["passages"][: configuration["passages"]]
            msgs = messages(invite, q["texte"], fournis)
            opts = options(moteur, configuration, repetition)
            appel = (tracer or (lambda _n, _m, _o, f: f()))(
                f"{configuration['nom']}/{q['question_id']}/r{repetition}",
                msgs,
                opts,
                lambda msgs=msgs, opts=opts: modele.discuter(msgs, opts),
            )
            verifier_contexte(appel, moteur)
            generations.append(
                {
                    "configuration": configuration["nom"],
                    "question_id": q["question_id"],
                    "famille": q["famille"],
                    "repetition": repetition,
                    "graine": opts["seed"],
                    "passages": [p["chunk_id"] for p in fournis],
                    **appel,
                    "part_copiee": part_copiee(
                        appel["reponse"], [p["texte"] for p in fournis], n
                    ),
                }
            )
    return generations


def tracer_mlflow(nom, msgs, opts, appeler):
    """Un span MLflow par appel : entrées, options, sortie, jetons, durées."""
    import mlflow
    from mlflow.entities import SpanType

    with mlflow.start_span(name=nom, span_type=SpanType.LLM) as span:
        span.set_inputs({"messages": msgs, "options": opts})
        appel = appeler()
        span.set_outputs({"reponse": appel["reponse"]})
        span.set_attributes(
            {k: v for k, v in appel.items() if k != "reponse" and v is not None}
        )
    return appel


def memoire_video() -> int:
    from service_embedding import mesures

    return mesures.gpu()["utilisee"]


def generer(
    reglages: dict,
    invite: dict,
    passages: list[dict],
    modele,
    enregistrer: bool = False,
    stockage: Path | None = None,
    contexte: dict | None = None,
) -> dict:
    moteur = reglages["moteur"]
    n = reglages["metriques"]["ngramme"]
    contexte = dict(contexte or {})
    if enregistrer:
        import mlflow

        from mesures_recherche.enregistrement import STOCKAGE, uri_suivi

        stockage = stockage or STOCKAGE
        stockage.mkdir(parents=True, exist_ok=True)
        mlflow.set_tracking_uri(uri_suivi(stockage))
        nom = reglages["mlflow"]["experience"]
        if mlflow.get_experiment_by_name(nom) is None:
            mlflow.create_experiment(
                nom, artifact_location=(stockage / "pieces").as_uri()
            )
        mlflow.set_experiment(nom)
    resultats = []
    for configuration in reglages["configurations"]:
        if enregistrer:
            mlflow.start_run(run_name=configuration["nom"])
        statut = "FAILED"
        try:
            vram_debut = contexte.get("mesurer_vram", memoire_video)()
            generations = executer_configuration(
                moteur,
                modele,
                invite,
                configuration,
                passages,
                n,
                tracer_mlflow if enregistrer else None,
            )
            vram_fin = contexte.get("mesurer_vram", memoire_video)()
            resume = resumer(generations, n)
            resume |= {
                "vram_occupee_debut_mio": vram_debut / 2**20,
                "vram_occupee_fin_mio": vram_fin / 2**20,
            }
            resultats.append(
                {
                    "configuration": configuration,
                    "resume": resume,
                    "generations": generations,
                }
            )
            if enregistrer:
                _journaliser(
                    mlflow,
                    reglages,
                    configuration,
                    resume,
                    generations,
                    passages,
                    contexte,
                )
            statut = "FINISHED"
        finally:
            if enregistrer:
                mlflow.flush_trace_async_logging()
                mlflow.end_run(statut)
    return {"contexte": contexte.get("parametres", {}), "configurations": resultats}


def _journaliser(
    mlflow, reglages, configuration, resume, generations, passages, contexte
):
    moteur = reglages["moteur"]
    mlflow.set_tags(
        {"spec": "E2 jour 3", "bloc": "D", "configuration": configuration["nom"]}
    )
    mlflow.log_params(
        {f"configuration.{k}": v for k, v in configuration.items()}
        | {f"moteur.{k}": v for k, v in moteur.items() if k != "url"}
        | {
            "regle_graine": "graine + (répétition − 1)",
            "questions": ",".join(q["question_id"] for q in passages),
            "passages_mesure": reglages["passages"]["mesure"],
        }
        | contexte.get("parametres", {})
    )
    mlflow.log_metrics(
        {k: float(v) for k, v in resume.items() if isinstance(v, int | float)}
    )
    if "questions_non_identiques" in resume:
        mlflow.log_param(
            "questions_non_identiques", ",".join(resume["questions_non_identiques"])
        )
    mlflow.log_text(
        json.dumps(generations, ensure_ascii=False, indent=1), "reponses.json"
    )
    mlflow.log_text(json.dumps(passages, ensure_ascii=False, indent=1), "passages.json")
    mlflow.log_artifact(str(contexte.get("invite", INVITE)))
    mlflow.log_artifact(str(contexte.get("reglages", REGLAGES)))


# --------------------------------------------------------------------------- #
#  Ligne de commande
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sous = parser.add_subparsers(dest="commande", required=True)
    p = sous.add_parser("passages")
    p.add_argument("--sortie", type=Path, required=True)
    g = sous.add_parser("generer")
    g.add_argument("--passages", type=Path, required=True)
    g.add_argument("--sortie", type=Path, required=True)
    g.add_argument("--enregistrer", action="store_true")
    args = parser.parse_args(argv)
    reglages = charger_toml(REGLAGES)

    if args.commande == "passages":
        from mesures_recherche import configuration, diagnostic, executer

        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise SystemExit("DATABASE_URL absente")
        ctx = executer.ouvrir(dsn, configuration.charger())
        try:
            questions = premieres_par_famille(ctx.questions)
            numero = reglages["passages"]["mesure"]
            sortie = recuperer_passages(
                ctx, questions, numero, reglages["passages"]["nombre"]
            )
        finally:
            ctx.cx.close()
        reference = {
            q["question_id"]: [c for c, _ in q["top"]]
            for q in diagnostic.reference_mlflow(numero)["questions"]
        }
        for q in sortie:
            if [p["entite"] for p in q["passages"]] != reference[q["question_id"]]:
                raise ArretGeneration(f"{q['question_id']} : entités ≠ mesure {numero}")
        args.sortie.parent.mkdir(parents=True, exist_ok=True)
        args.sortie.write_text(
            json.dumps(sortie, ensure_ascii=False, indent=1), "utf-8"
        )
        print(
            json.dumps(
                {
                    "questions": [q["question_id"] for q in sortie],
                    "passages_par_question": reglages["passages"]["nombre"],
                    "confrontes_a_la_mesure": numero,
                },
                ensure_ascii=False,
            )
        )
        return 0

    from mesures_recherche.enregistrement import empreinte_code, etat_git

    passages = json.loads(args.passages.read_text(encoding="utf-8"))
    invite = charger_toml(INVITE)
    moteur = reglages["moteur"]
    modele = Ollama(moteur["url"], moteur["modele"], moteur["keep_alive"])
    parametres = modele.description() | {
        "reglages_empreinte": empreinte(REGLAGES),
        "invite_empreinte": empreinte(INVITE),
        "passages_empreinte": empreinte(args.passages),
        "code_empreinte": empreinte_code(),
        **etat_git(),
    }
    # Chargement, hors série : la mémoire vidéo du modèle chargé, puis la série.
    debut = time.monotonic()
    modele.discuter(
        [{"role": "user", "content": "Bonjour."}],
        {"temperature": 0, "num_predict": 1, "num_ctx": moteur["num_ctx"]},
    )
    charge = modele.charge() | {
        "vram_occupee": memoire_video(),
        "chargement_s": time.monotonic() - debut,
    }
    if charge["taille_memoire_video"] < charge["taille"]:
        modele.decharger()
        raise ArretGeneration(f"modèle pas entièrement sur la carte : {charge}")
    try:
        resultat = generer(
            reglages,
            invite,
            passages,
            modele,
            enregistrer=args.enregistrer,
            contexte={
                "parametres": parametres | {f"charge_{k}": v for k, v in charge.items()}
            },
        )
    finally:
        modele.decharger()
    resultat["charge"] = charge
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(json.dumps(resultat, ensure_ascii=False, indent=1), "utf-8")
    for c in resultat["configurations"]:
        r = c["resume"]
        print(
            json.dumps(
                {
                    "configuration": c["configuration"]["nom"],
                    "generations": r["generations"],
                    "latence_mediane_s": round(r["latence_mediane_s"], 2),
                    "identiques": r.get("part_repetitions_identiques"),
                },
                ensure_ascii=False,
            )
        )
    print(json.dumps({"charge": charge}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
