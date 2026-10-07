"""Exécuter une mesure du banc.

    uv run python -m mesures_recherche.executer 3 --sortie resultat.json

Ordre, toujours le même :
  1. session en lecture seule côté serveur ; PREMIÈRE action : l'empreinte du
     jeu (fichiers et base) contre celle de la configuration — sinon, arrêt ;
  2. lecture du jeu, du rattachement fragment → entité, des séries atteignables ;
  3. pour chaque question, le classement complet des entités, ses k premières,
     le rang de la première série attendue, les métriques ;
  4. les moyennes par portée et par périmètre.

Sans `--enregistrer`, rien n'est écrit nulle part hors du fichier de sortie :
c'est l'exécution « à blanc ». Avec `--enregistrer` : un run MLflow et les lignes
de `bench.eval_mesures` (module `enregistrement`). Avec `--rejeu-de FICHIER` :
exécution à blanc, puis comparaison stricte des métriques avec celles d'une
exécution enregistrée — code 1 si elles diffèrent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import psycopg

from mesures_recherche import configuration, entites, jeu, metriques, recherche


@dataclass
class Contexte:
    cx: psycopg.Connection
    config: dict
    questions: list[jeu.Question]
    entites: entites.Entites
    empreinte_jeu: str
    caches: dict = field(default_factory=dict)
    parametres: dict = field(default_factory=dict)


def ouvrir(dsn: str, config: dict) -> Contexte:
    cx = psycopg.connect(dsn, options="-c default_transaction_read_only=on")
    j = config["jeu"]
    empreinte = jeu.verifier_empreinte(
        cx, j["version"], j["empreinte"], configuration.RACINE_DEPOT / j["dossier"]
    )
    questions = jeu.lire_jeu(cx, j["version"])
    return Contexte(cx, config, questions, entites.charger(cx), empreinte)


def encodeur_du_service(instance_nom: str):
    """Question → vecteur, par l'instance du service (rôle « requete »)."""
    from service_embedding.client import ClientService
    from service_embedding.configuration import charger

    client = ClientService(charger(instance_nom))
    info = client.info()

    def encoder(texte: str):
        return client.encoder([texte], role="requete")[0]

    return encoder, {
        "service_version": info["version"],
        "service_modele": info["model_id"],
        "service_revision": info["model_sha"],
        "service_precision": info["model_dtype"],
        "prefixe_requete": client.instance.prefixe_requete,
    }


def encodage_du_modele(cx, modele: str) -> int:
    lignes = cx.execute(
        "SELECT encodage_id FROM bench.encodages"
        " WHERE modele = %s AND termine_le IS NOT NULL",
        (modele,),
    ).fetchall()
    if len(lignes) != 1:
        raise RuntimeError(
            f"{modele} : {len(lignes)} encodage(s) terminé(s), 1 attendu"
        )
    return lignes[0][0]


def configuration_de(ctx: Contexte, numero: int):
    """La fonction question → scores des entités de la mesure `numero`."""
    if numero in ctx.caches:
        return ctx.caches[numero]
    m = configuration.mesure(ctx.config, numero)
    params: dict = {}
    if m["type"] == "semantique":
        encoder, params = encodeur_du_service(m["instance"])
        params["encodage_id"] = encodage_du_modele(ctx.cx, m["modele"])
        s = recherche.Semantique.charger(ctx.cx, ctx.entites, m["table"], encoder)
        fonction = s.scores
    elif m["type"] == "plein_texte":
        p = recherche.PleinTexte.calculer(
            ctx.cx, ctx.entites, ctx.questions, m["configuration"], m["normalisation"]
        )
        params["duree_plein_texte_s"] = round(p.duree_s, 1)
        fonction = p.scores
    elif m["type"] == "tfidf":
        debut = time.monotonic()
        t = recherche.Tfidf.ajuster(ctx.cx, ctx.entites, m)
        params["duree_ajustement_s"] = round(time.monotonic() - debut, 1)
        params["vocabulaire"] = len(t.vectoriseur.vocabulary_)
        fonction = t.scores
    elif m["type"] == "fusion":
        sources = [configuration_de(ctx, s) for s in m["sources"]]

        def fonction(question, sources=sources, m=m):
            classements = [recherche.classer(ctx.entites, f(question)) for f in sources]
            return recherche.fusion(classements, m["k_rrf"], m["profondeur"])

        for s in m["sources"]:
            params |= {f"source_{s}_{k}": v for k, v in ctx.parametres[s].items()}
    else:
        raise ValueError(f"type de mesure inconnu : {m['type']}")
    ctx.caches[numero] = fonction
    ctx.parametres[numero] = params
    return fonction


@dataclass
class LigneQuestion:
    question_id: str
    famille: str
    mode: str
    issue: str
    de_rang: bool
    atteignable: bool
    rang_premiere_attendue: int | None
    egalite_au_seuil: bool
    top: list[tuple[str, float]]
    metriques: dict[str, float] | None


def mesurer(ctx: Contexte, numero: int) -> dict:
    k = ctx.config["classement"]["k"]
    fonction = configuration_de(ctx, numero)
    debut = time.monotonic()
    lignes: list[LigneQuestion] = []
    for q in ctx.questions:
        c = recherche.classer(ctx.entites, fonction(q))
        top = c.top(ctx.entites, k)
        rangs = [
            c.rang(ctx.entites.index_serie[s])
            for s in q.attendus
            if s in ctx.entites.index_serie
        ]
        egalite = bool(
            len(c.ordre) > k and c.scores[c.ordre[k - 1]] == c.scores[c.ordre[k]]
        )
        lignes.append(
            LigneQuestion(
                q.question_id,
                q.famille,
                q.mode,
                q.issue,
                q.de_rang,
                metriques.atteignable(q, ctx.entites.atteignables),
                min(rangs) if rangs else None,
                egalite,
                top,
                metriques.mesurer(q, [cle for cle, _ in top]),
            )
        )
    duree = time.monotonic() - debut
    agregats = metriques.agreger(
        ctx.questions,
        {lq.question_id: lq.metriques for lq in lignes},
        ctx.entites.atteignables,
    )
    m = configuration.mesure(ctx.config, numero)
    return {
        "mesure": numero,
        "nom": m["nom"],
        "reglages": m,
        "jeu_version": ctx.config["jeu"]["version"],
        "jeu_empreinte": ctx.empreinte_jeu,
        "configuration_empreinte": configuration.empreinte(),
        "parametres": ctx.parametres[numero],
        "duree_questions_s": round(duree, 1),
        "questions": [asdict(lq) for lq in lignes],
        "agregats": [asdict(a) for a in agregats],
    }


def _json(obj):
    if isinstance(obj, np.generic):
        return obj.item()
    raise TypeError(type(obj))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mesure", type=int)
    parser.add_argument("--sortie", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--enregistrer", action="store_true")
    mode.add_argument("--rejeu-de", type=Path, default=None)
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL absente")
    config = configuration.charger()
    debut = time.monotonic()
    ctx = ouvrir(dsn, config)
    try:
        resultat = mesurer(ctx, args.mesure)
    finally:
        ctx.cx.close()
    resultat["duree_totale_s"] = round(time.monotonic() - debut, 1)
    resultat["experience"] = config["mlflow"]["experience"]
    code = 0
    if args.enregistrer:
        from mesures_recherche.enregistrement import enregistrer

        resultat["run_id"] = enregistrer(resultat, dsn)
    elif args.rejeu_de is not None:
        reference = json.loads(args.rejeu_de.read_text(encoding="utf-8"))
        cle = ("agregats", "questions")
        identiques = all(
            json.loads(json.dumps(resultat[c], default=_json)) == reference[c]
            for c in cle
        )
        resultat["rejeu"] = {"reference": str(args.rejeu_de), "identiques": identiques}
        code = 0 if identiques else 1
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(
        json.dumps(resultat, ensure_ascii=False, indent=1, default=_json), "utf-8"
    )
    globales = {
        f"{a['metrique']}@{a['k']}/{a['perimetre']}": round(a["valeur"], 4)
        for a in resultat["agregats"]
        if a["portee"] == "global"
    }
    print(
        json.dumps(
            {
                "mesure": args.mesure,
                "global": globales,
                "duree_totale_s": resultat["duree_totale_s"],
                "parametres": resultat["parametres"],
                "run_id": resultat.get("run_id"),
                "rejeu": resultat.get("rejeu"),
            },
            ensure_ascii=False,
        )
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
