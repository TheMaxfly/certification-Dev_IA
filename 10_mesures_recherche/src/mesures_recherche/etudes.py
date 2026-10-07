"""Études — quels réglages comptent (spec E2 jour 4, blocs C, D, E).

    # Bloc C — sensibilité du TF-IDF, 36 combinaisons :
    uv run python -m mesures_recherche.etudes tfidf \
        --sortie resultats/etudes/tfidf.json --enregistrer

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


def _experience(etudes: dict, stockage: Path | None):
    import mlflow

    from mesures_recherche.enregistrement import STOCKAGE, uri_suivi

    stockage = stockage or STOCKAGE
    stockage.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(uri_suivi(stockage))
    nom = etudes["mlflow"]["experience"]
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


# --------------------------------------------------------------------------- #
#  Ligne de commande
# --------------------------------------------------------------------------- #


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL absente")
    return dsn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sous = parser.add_subparsers(dest="commande", required=True)
    t = sous.add_parser("tfidf", help="bloc C : la grille, sous garde de mémoire")
    t.add_argument("--sortie", type=Path, required=True)
    t.add_argument("--enregistrer", action="store_true")
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
