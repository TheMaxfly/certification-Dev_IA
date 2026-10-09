"""Exécuter une mesure du banc.

    uv run python -m mesures_recherche.executer 3 --sortie resultat.json

Ordre, toujours le même :
  1. session en lecture seule côté serveur ; PREMIÈRE action : l'empreinte du
     jeu (fichiers et base) contre celle de la configuration — sinon, arrêt ;
  2. lecture du jeu, du rattachement fragment → entité, des séries atteignables ;
  3. pour chaque question, le classement des entités du périmètre de la mesure
     (toutes, ou les seules séries du catalogue), ses k premières, le rang de la
     première série attendue, les métriques ;
  4. les moyennes par portée et par périmètre.

LE CORPUS ET L'ENCODAGE (migration 021). Plusieurs corpus et plusieurs encodages
d'un même modèle coexistent : une mesure lit UN corpus et, par le sens, les
vecteurs d'UN encodage, désigné — `--encodage N` ; sinon celui en service s'il est
du modèle de la mesure ; sinon le seul encodage terminé de ce modèle. Le corpus
suit l'encodage désigné (`--corpus` pour une mesure sans vecteurs ; sinon celui en
service). Avant de mesurer, ce que sert l'instance (`/info`) est confronté à la
ligne de l'encodage : un écart arrête.

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
    encodage_id: int | None = None  # désigné explicitement (--encodage)


class ServiceNonConforme(RuntimeError):
    """L'instance ne sert pas ce que dit le registre (ou la configuration)."""


@dataclass(frozen=True)
class Designation:
    """L'instance qui encode les questions, et la ligne de `bench.encodages` dont
    on lit les vecteurs (None : aucun vecteur du corpus n'est lu)."""

    instance: str
    encodage: dict | None = None


COLONNES_ENCODAGE = (
    "encodage_id, corpus_id, modele, revision, precision_calcul, outil_version,"
    " prefixe_requete, termine_le"
)


def ligne_encodage(cx, encodage_id: int) -> dict:
    r = cx.execute(
        f"SELECT {COLONNES_ENCODAGE} FROM bench.encodages WHERE encodage_id = %s",  # nosec B608
        (encodage_id,),
    )
    ligne = r.fetchone()
    if ligne is None:
        raise RuntimeError(f"encodage {encodage_id} inconnu du registre")
    return dict(zip([c.name for c in r.description], ligne, strict=True))


def encodage_designe(cx, modele: str, encodage_id: int | None = None) -> dict:
    """L'encodage dont une mesure de `modele` lit les vecteurs : celui désigné ;
    sinon celui en service s'il est de ce modèle ; sinon le seul encodage terminé
    de ce modèle. Jamais deviné entre plusieurs."""
    if encodage_id is None:
        en_service = cx.execute(
            "SELECT encodage_id FROM bench.v_encodage_en_service WHERE modele = %s",
            (modele,),
        ).fetchone()
        if en_service is not None:
            encodage_id = en_service[0]
        else:
            termines = cx.execute(
                "SELECT encodage_id FROM bench.encodages"
                " WHERE modele = %s AND termine_le IS NOT NULL",
                (modele,),
            ).fetchall()
            if len(termines) != 1:
                raise RuntimeError(
                    f"{modele} : {len(termines)} encodage(s) terminé(s) et aucun en"
                    " service — désigner l'encodage (--encodage)"
                )
            encodage_id = termines[0][0]
    e = ligne_encodage(cx, encodage_id)
    if e["modele"] != modele:
        raise RuntimeError(
            f"encodage {encodage_id} : modèle {e['modele']}, la mesure attend {modele}"
        )
    if e["termine_le"] is None:
        raise RuntimeError(f"encodage {encodage_id} : non terminé")
    return e


def ouvrir(
    dsn: str,
    config: dict,
    encodage_id: int | None = None,
    corpus_id: str | None = None,
) -> Contexte:
    cx = psycopg.connect(dsn, options="-c default_transaction_read_only=on")
    j = config["jeu"]
    empreinte = jeu.verifier_empreinte(
        cx, j["version"], j["empreinte"], configuration.RACINE_DEPOT / j["dossier"]
    )
    if encodage_id is not None:
        designe = ligne_encodage(cx, encodage_id)["corpus_id"]
        if corpus_id is not None and corpus_id != designe:
            raise RuntimeError(
                f"encodage {encodage_id} : corpus {designe}, pas {corpus_id}"
            )
        corpus_id = designe
    questions = jeu.lire_jeu(cx, j["version"])
    return Contexte(
        cx,
        config,
        questions,
        entites.charger(cx, corpus_id),
        empreinte,
        encodage_id=encodage_id,
    )


def encodeur_du_service(designation: Designation):
    """Question → vecteur, par l'instance du service (rôle « requete »), APRÈS
    confrontation de ce qu'elle sert (`/info`) à la ligne de l'encodage — ou, sans
    encodage, à la configuration de l'instance. Un écart arrête."""
    from service_embedding.client import ClientService
    from service_embedding.configuration import charger

    client = ClientService(charger(designation.instance))
    info = client.info()
    servi = {
        "modele": info["model_id"],
        "revision": info["model_sha"],
        "precision_calcul": info["model_dtype"],
        "prefixe_requete": client.instance.prefixe_requete,
    }
    e = designation.encodage
    if e is not None:
        servi["outil_version"] = info["version"]
        attendu = {k: e[k] for k in servi}
    else:
        i = client.instance
        attendu = {
            "modele": i.model_id,
            "revision": i.revision,
            "precision_calcul": i.dtype,
            "prefixe_requete": i.prefixe_requete,
        }
    if servi != attendu:
        ecarts = {k: (servi[k], attendu[k]) for k in servi if servi[k] != attendu[k]}
        raise ServiceNonConforme(f"{designation.instance} : servi ≠ attendu {ecarts}")

    def encoder(texte: str):
        return client.encoder([texte], role="requete")[0]

    return encoder, {
        "service_version": info["version"],
        "service_modele": info["model_id"],
        "service_revision": info["model_sha"],
        "service_precision": info["model_dtype"],
        "prefixe_requete": client.instance.prefixe_requete,
    }


def semantique(ctx: Contexte, m: dict, representation: str = "meilleur_fragment"):
    """La recherche par le sens d'une mesure : encodage désigné, service confronté,
    vecteurs lus PAR CET ENCODAGE seulement. Rend (Semantique, encoder, params)."""
    e = encodage_designe(ctx.cx, m["modele"], ctx.encodage_id)
    if e["corpus_id"] != ctx.entites.corpus_id:
        raise RuntimeError(
            f"encodage {e['encodage_id']} : corpus {e['corpus_id']}, la mesure lit"
            f" {ctx.entites.corpus_id} (--corpus ou --encodage)"
        )
    encoder, params = encodeur_du_service(Designation(m["instance"], e))
    params = params | {"encodage_id": e["encodage_id"], "corpus_id": e["corpus_id"]}
    s = recherche.Semantique.charger(
        ctx.cx,
        ctx.entites,
        m["table"],
        encoder,
        encodage_id=e["encodage_id"],
        representation=representation,
    )
    return s, encoder, params


def configuration_de(ctx: Contexte, numero: int):
    """La fonction question → scores des entités de la mesure `numero`."""
    if numero in ctx.caches:
        return ctx.caches[numero]
    m = configuration.mesure(ctx.config, numero)
    params: dict = {}
    if m["type"] == "semantique":
        s, _, params = semantique(ctx, m, configuration.representation(m))
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
        perimetre = configuration.perimetre(m)

        def fonction(question, sources=sources, m=m, perimetre=perimetre):
            classements = [
                recherche.classer(ctx.entites, f(question), perimetre) for f in sources
            ]
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
    m = configuration.mesure(ctx.config, numero)
    perimetre = configuration.perimetre(m)
    fonction = configuration_de(ctx, numero)
    debut = time.monotonic()
    lignes: list[LigneQuestion] = []
    for q in ctx.questions:
        c = recherche.classer(ctx.entites, fonction(q), perimetre)
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
    tour = configuration.tour(m)
    encodages = {
        v for c, v in ctx.parametres[numero].items() if c.endswith("encodage_id")
    }
    if len(encodages) > 1:
        raise RuntimeError(f"mesure {numero} : plusieurs encodages {sorted(encodages)}")
    return {
        "mesure": numero,
        "corpus_id": ctx.entites.corpus_id,
        "encodage_id": next(iter(encodages), None),
        "nom": m["nom"],
        "tour": tour,
        "spec": ctx.config["tours"][str(tour)],
        "perimetre_entites": perimetre,
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
    parser.add_argument("--encodage", type=int, default=None, help="encodage lu")
    parser.add_argument("--corpus", default=None, help="corpus lu (sans vecteurs)")
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL absente")
    config = configuration.charger()
    debut = time.monotonic()
    ctx = ouvrir(dsn, config, args.encodage, args.corpus)
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
                "corpus_id": resultat["corpus_id"],
                "encodage_id": resultat["encodage_id"],
                "run_id": resultat.get("run_id"),
                "rejeu": resultat.get("rejeu"),
            },
            ensure_ascii=False,
        )
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
