"""La ligne de commande du banc (voir `api_modele.banc_reponse`).

    B="uv run --env-file .env python -m api_modele.banc_reponse"
    $B essai <profil> "<question>" [--temperature T]
    $B estimer <profil> <jeu>
    DATABASE_URL=… $B mesurer <profil> <jeu>
    DATABASE_URL=… $B scenarios <profil> <fichier.csv>
    DATABASE_URL=… $B reprendre <dossier du run>

Codes de sortie : 0 fait ; 2 refusé avant tout appel (configuration, jeu, commit,
plafond, fichier) ; 3 run interrompu (plafond atteint, fournisseur en échec) ; 4 run
mesuré mais non enregistré (la charge reste dans son dossier : `reprendre`).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import secrets
import sys
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient

from api_modele import journal
from api_modele.application import creer_application
from api_modele.banc_reponse import enregistrement, jeu
from api_modele.banc_reponse.espion import Espion
from api_modele.banc_reponse.mesure import (
    CRITERE,
    Banc,
    Estimation,
    PlafondDepasse,
    Run,
    Tour,
    essai,
    estimer,
    estimer_scenarios,
    garder_plafond,
    mesurer,
    metriques,
    metriques_scenarios,
    scenarios,
    verdict,
)
from api_modele.configuration import ConfigurationInvalide, Profil, charger
from api_modele.etat import EtatApi

SPEC = "E3-etape3-06"
COLONNES_SCENARIOS = (
    "scenario",
    "tour",
    "question_libraire",
    "question_autonome_attendue",
    "serie_attendue_id",
)
IDENTIFIANT_QUESTION = re.compile(r"^Q\d{3}$")


class BancRefuse(Exception):
    """Refus avant tout appel."""


def horodatage() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def afficher(donnees: object) -> None:
    print(json.dumps(donnees, ensure_ascii=False, indent=1, default=str))


@contextmanager
def ouvrir(environ: Mapping[str, str]) -> Generator[Banc]:
    """L'API dans le processus, sous une clé de consommateur tirée pour ce seul
    lancement, sans limite de débit ; l'espion écoute les appels aux fournisseurs."""
    cle = secrets.token_urlsafe(32)
    reglages = replace(
        charger({**environ, "API_MODELE_CLES": f"banc:{cle}"}),
        debit_par_minute=100_000,
    )
    if reglages.modele_langage.verifier_modeles:
        raise BancRefuse(
            "API_MODELE_VERIFIER_MODELES doit valoir faux : le banc n'interroge pas"
            " la liste des modèles"
        )
    application = creer_application(reglages)
    espion = Espion()
    with espion.ecouter(), TestClient(application) as api:
        etat = cast(EtatApi, application.state.etat)
        yield Banc(api, cle, reglages.modele_langage, etat.pseudonymes, espion)


def lire_scenarios(fichier: Path) -> tuple[list[Tour], str]:
    """Les tours des scénarios écrits par Max, dans l'ordre, et l'empreinte SHA-256
    du fichier. Premier tour : une question du jeu gelé, par son identifiant."""
    donnees = fichier.read_bytes()
    empreinte = hashlib.sha256(donnees).hexdigest()
    lecteur = csv.DictReader(donnees.decode("utf-8").splitlines())
    if tuple(lecteur.fieldnames or ()) != COLONNES_SCENARIOS:
        raise BancRefuse(f"colonnes attendues : {', '.join(COLONNES_SCENARIOS)}")
    tours: list[Tour] = []
    for ligne in lecteur:
        question = ligne["question_libraire"].strip()
        if not question or not ligne["question_autonome_attendue"].strip():
            raise BancRefuse(f"tour vide : {ligne['scenario']}-{ligne['tour']}")
        if IDENTIFIANT_QUESTION.fullmatch(question):
            question = jeu.texte_de(question)
        serie = ligne["serie_attendue_id"].strip()
        tours.append(
            Tour(
                scenario=ligne["scenario"].strip(),
                tour=int(ligne["tour"]),
                question=question,
                question_autonome_attendue=ligne["question_autonome_attendue"].strip(),
                serie_attendue_id=int(serie) if serie else None,
            )
        )
    tours.sort(key=lambda t: (t.scenario, t.tour))
    par_scenario: dict[str, list[int]] = {}
    for t in tours:
        par_scenario.setdefault(t.scenario, []).append(t.tour)
    if len(par_scenario) != 5 or any(
        not 2 <= len(n) <= 5 or n != list(range(1, len(n) + 1))
        for n in par_scenario.values()
    ):
        raise BancRefuse("5 scénarios de 2 à 5 tours, numérotés à partir de 1")
    return tours, empreinte


def parametres_du_run(
    run: Run,
    profil: Profil,
    designation: str,
    taille: int,
    empreinte_jeu: str,
    git: Mapping[str, str],
    estimation: Estimation,
    plafond: float,
    caracteres_par_jeton: float,
) -> dict[str, object]:
    c = run.contexte
    return {
        "profil": profil.nom,
        "fournisseur": profil.fournisseur,
        "modele_demande": profil.modele,
        "modele_rendu": ",".join(sorted(run.modeles_rendus)) or profil.modele,
        "statut_profil": profil.statut,
        "reglages": c.get("reglages"),
        "reglages_appliques": c.get("reglages_appliques"),
        "jeu": designation,
        "taille_jeu": str(taille),
        "jeu_empreinte": empreinte_jeu,
        "commit": git["commit"],
        "branche": git["branche"],
        "corpus_id": str(c.get("corpus_id")),
        "encodage_id": str(c.get("encodage_id")),
        "version_api": str(c.get("version_api")),
        "date_appel": str(c.get("date_appel")),
        "plafond_usd": str(plafond),
        "estimation_usd": str(round(estimation.cout_usd, 6)),
        "caracteres_par_jeton": str(caracteres_par_jeton),
        "critere": CRITERE,
        "statut_run": run.statut,
        "motif_interruption": str(run.motif_interruption),
    }


def enregistrer(
    banc_: Banc,
    run: Run,
    nom: str,
    parametres: dict[str, object],
    mesures: dict[str, float],
    resultat: dict[str, object],
    dsn: str,
) -> int:
    """Textes hors dépôt, run MLflow, ligne `eval_runs` ; le code de sortie."""
    dossier = enregistrement.dossier_du_run(nom)
    enregistrement.ecrire_textes(dossier, nom, run.textes)
    pieces: dict[str, object] = {
        "par_question.json": run.par_question,
        "resume.json": resultat,
    }
    for chemin, contenu in pieces.items():
        enregistrement.ecrire_json(dossier / chemin, contenu)
    charge = enregistrement.charge_mlflow(
        nom,
        {
            "spec": SPEC,
            "statut": run.statut,
            "profil": run.profil,
            "statut_profil": str(parametres["statut_profil"]),
        },
        parametres,
        {k: float(v) for k, v in mesures.items()},
        pieces,
    )
    code = 3 if run.statut == "interrompu" else 0
    try:
        run_id = enregistrement.journaliser_mlflow(charge, dossier)
        c = run.contexte
        enregistrement.inscrire_eval_run(
            dsn,
            run_id,
            str(c.get("corpus_id", "v2")),
            int(cast(int, c.get("encodage_id", 3))),
            f"E3 étape 3, spec 06 — réponse rédigée — {nom}",
        )
    except Exception as exc:  # la mesure est faite : la garder, dire comment reprendre
        print(
            f"run mesuré, NON enregistré ({type(exc).__name__}) ; tout est dans"
            f" {dossier} — reprendre : python -m api_modele.banc_reponse reprendre"
            f" {dossier}",
            file=sys.stderr,
        )
        return 4
    enregistrement.ecrire_json(dossier / "run_id.json", {"run_id": run_id, "nom": nom})
    afficher({"run_id": run_id, "dossier": str(dossier), **resultat})
    return code


def commande_essai(banc_: Banc, args: argparse.Namespace) -> int:
    profil = banc_.profil(args.profil)
    chiffres, texte = essai(banc_, profil, args.question, args.temperature)
    chiffres["git"] = enregistrement.etat_git()
    suffixe = "" if args.temperature is None else f"_t{args.temperature}"
    dossier = enregistrement.dossier_du_run(
        f"{horodatage()}_{profil.nom}{suffixe}", enregistrement.SORTIES / "essais"
    )
    enregistrement.ecrire_json(dossier / "essai.json", chiffres)
    if texte is not None:
        enregistrement.ecrire_textes(
            dossier, f"Essai — {profil.nom}", {"reponse": texte}
        )
    afficher({"dossier": str(dossier), **chiffres})
    if texte is not None:
        print("\n--- réponse (contrôlée : aucun nom de membre) ---\n" + texte)
    return 0 if chiffres["statut"] == 200 else 3


def commande_estimer(banc_: Banc, args: argparse.Namespace) -> int:
    profil = banc_.profil(args.profil)
    questions, empreinte = jeu.lire(args.jeu)
    estimation = estimer(banc_, profil, [q.texte for q in questions])
    plafond = banc_.modele_langage.plafond_run_usd
    afficher(
        {
            "profil": profil.nom,
            "jeu": args.jeu,
            "jeu_empreinte": empreinte,
            **estimation.en_dict(),
            "plafond_usd": plafond,
            "sous_le_plafond": estimation.cout_usd <= plafond,
        }
    )
    return 0


def commande_mesurer(banc_: Banc, args: argparse.Namespace, dsn: str) -> int:
    git = enregistrement.exiger_commit()
    profil = banc_.profil(args.profil)
    questions, empreinte = jeu.lire(args.jeu)
    plafond = banc_.modele_langage.plafond_run_usd
    estimation = estimer(banc_, profil, [q.texte for q in questions])
    garder_plafond(estimation, plafond)
    run = mesurer(banc_, profil, questions, plafond)
    mesures = metriques(run) | {"estimation_cout_usd": round(estimation.cout_usd, 6)}
    nom = f"{profil.nom}-{jeu.nom_court(args.jeu)}-{horodatage()}"
    parametres = parametres_du_run(
        run,
        profil,
        args.jeu,
        len(questions),
        empreinte,
        git,
        estimation,
        plafond,
        banc_.modele_langage.caracteres_par_jeton,
    )
    resultat: dict[str, object] = {
        "nom": nom,
        "statut": run.statut,
        "motif_interruption": run.motif_interruption,
        "metriques": mesures,
        "verdict": verdict(mesures),
        "parametres": parametres,
    }
    return enregistrer(banc_, run, nom, parametres, mesures, resultat, dsn)


def commande_scenarios(banc_: Banc, args: argparse.Namespace, dsn: str) -> int:
    git = enregistrement.exiger_commit()
    profil = banc_.profil(args.profil)
    if jeu.empreinte() != jeu.EMPREINTE:
        raise jeu.JeuRefuse("empreinte du jeu : rien n'est mesuré")
    tours, empreinte = lire_scenarios(Path(args.fichier))
    plafond = banc_.modele_langage.plafond_run_usd
    estimation = estimer_scenarios(banc_, profil, tours)
    garder_plafond(estimation, plafond)
    run = scenarios(banc_, profil, tours, plafond)
    mesures = metriques_scenarios(run) | {
        "estimation_cout_usd": round(estimation.cout_usd, 6)
    }
    nom = f"{profil.nom}-scenarios-{horodatage()}"
    parametres = parametres_du_run(
        run,
        profil,
        "scenarios",
        len(tours),
        jeu.EMPREINTE,
        git,
        estimation,
        plafond,
        banc_.modele_langage.caracteres_par_jeton,
    ) | {"scenarios_empreinte": empreinte}
    resultat: dict[str, object] = {
        "nom": nom,
        "statut": run.statut,
        "motif_interruption": run.motif_interruption,
        "metriques": mesures,
        "parametres": parametres,
    }
    return enregistrer(banc_, run, nom, parametres, mesures, resultat, dsn)


def commande_reprendre(dossier: Path, dsn: str) -> int:
    """Rejouer l'enregistrement d'un run mesuré (MLflow, puis `eval_runs`)."""
    charge = cast(
        dict[str, object],
        json.loads((dossier / "mlflow_charge.json").read_text(encoding="utf-8")),
    )
    resultat = cast(
        dict[str, object],
        json.loads((dossier / "resume.json").read_text(encoding="utf-8")),
    )
    parametres = cast(dict[str, object], resultat["parametres"])
    run_id = enregistrement.journaliser_mlflow(charge, dossier)
    enregistrement.inscrire_eval_run(
        dsn,
        run_id,
        str(parametres["corpus_id"]),
        int(str(parametres["encodage_id"])),
        f"E3 étape 3, spec 06 — réponse rédigée — {resultat['nom']}",
    )
    enregistrement.ecrire_json(
        dossier / "run_id.json", {"run_id": run_id, "nom": resultat["nom"]}
    )
    afficher({"run_id": run_id, "dossier": str(dossier)})
    return 0


def analyser(argv: Sequence[str]) -> argparse.Namespace:
    parseur = argparse.ArgumentParser(prog="python -m api_modele.banc_reponse")
    sous = parseur.add_subparsers(dest="commande", required=True)
    p = sous.add_parser("essai", help="un appel, coût affiché avant et après")
    p.add_argument("profil")
    p.add_argument("question")
    p.add_argument("--temperature", type=float, default=None)
    for nom in ("estimer", "mesurer"):
        p = sous.add_parser(nom)
        p.add_argument("profil")
        p.add_argument("jeu", help="famille:F7,issue:hors_catalogue,…")
    p = sous.add_parser("scenarios")
    p.add_argument("profil")
    p.add_argument("fichier", help="le CSV des scénarios écrits par Max")
    p = sous.add_parser("reprendre", help="enregistrer un run déjà mesuré")
    p.add_argument("dossier")
    return parseur.parse_args(argv)


type Ouvreur = Callable[[Mapping[str, str]], AbstractContextManager[Banc]]


def principal(
    argv: Sequence[str],
    environ: Mapping[str, str] = os.environ,
    ouvreur: Ouvreur = ouvrir,
) -> int:
    args = analyser(argv)
    dsn = environ.get("DATABASE_URL", "")
    try:
        if args.commande in ("mesurer", "scenarios", "reprendre") and not dsn:
            raise BancRefuse(
                "DATABASE_URL absente : le DSN qui écrit `bench.eval_runs` (module 10)"
            )
        if args.commande == "reprendre":
            return commande_reprendre(Path(args.dossier), dsn)
        with ouvreur(environ) as banc_:
            if args.commande == "essai":
                return commande_essai(banc_, args)
            if args.commande == "estimer":
                return commande_estimer(banc_, args)
            if args.commande == "mesurer":
                return commande_mesurer(banc_, args, dsn)
            return commande_scenarios(banc_, args, dsn)
    except ConfigurationInvalide as exc:
        afficher({"refus": "configuration", "ecarts": exc.ecarts})
        return 2
    except (
        BancRefuse,
        jeu.JeuRefuse,
        enregistrement.CommitRefuse,
        PlafondDepasse,
        ValueError,
        FileNotFoundError,
    ) as exc:
        afficher({"refus": str(exc)})
        return 2


if __name__ == "__main__":
    journal.configurer()
    sys.exit(principal(sys.argv[1:]))
