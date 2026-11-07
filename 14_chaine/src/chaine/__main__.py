"""`python -m chaine preprod | lancer | promouvoir | revenir | etat`.

    python -m chaine preprod                           # crée ou recrée la préproduction
    python -m chaine lancer <candidat.toml> [--a-blanc] # étapes 0 à 8, sans promotion
    python -m chaine promouvoir <livraison> --motif "…" # geste de Max (préproduction)
    python -m chaine revenir --motif "…"                # geste de Max (préproduction)
    python -m chaine etat                               # journal de la préproduction

Lancées par `make preprod-creer`, `make chaine`, `make chaine-promouvoir`,
`make chaine-revenir`, `make chaine-etat` ; le code de sortie est le code nommé
(`chaine.codes`).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping

from chaine import deployer, executer, rapport
from chaine.candidat import resoudre
from chaine.codes import Arret, Code
from chaine.environnement import cible, creer_preproduction, http_reel, lancer_reel


def analyseur() -> argparse.ArgumentParser:
    a = argparse.ArgumentParser(
        prog="python -m chaine", description=(__doc__ or "").splitlines()[0]
    )
    sous = a.add_subparsers(dest="commande", required=True)
    sous.add_parser("preprod", help="crée ou recrée la base apimanga_preprod")
    lancer = sous.add_parser("lancer", help="les étapes 0 à 8 d'un candidat")
    lancer.add_argument("candidat")
    lancer.add_argument(
        "--a-blanc", action="store_true", help="affiche la séquence, ne lance rien"
    )
    promouvoir = sous.add_parser(
        "promouvoir", help="promotion de Max, en préproduction"
    )
    promouvoir.add_argument("livraison")
    promouvoir.add_argument("--motif", required=True)
    revenir = sous.add_parser("revenir", help="retour arrière de Max, en préproduction")
    revenir.add_argument("--motif", required=True)
    sous.add_parser("etat", help="le journal des promotions de la préproduction")
    return a


def principal(
    arguments: list[str],
    environ: Mapping[str, str] | None = None,
    outils: executer.Outils | None = None,
) -> int:
    args = analyseur().parse_args(arguments)
    env = os.environ if environ is None else environ
    o = outils or executer.Outils(lanceur=lancer_reel, http=http_reel)
    try:
        if args.commande == "preprod":
            d = creer_preproduction(o.lanceur, cible(env))
            o.afficher(
                f"Préproduction prête : {d['base']}, {d['taille']},"
                f" copiée en {d['duree_s']} s ;"
                " empreintes égales à la référence ; production inchangée."
            )
            deployer.journaliser(o, {"geste": "preprod"} | d)
            return int(Code.PROMOTION_PROPOSEE)
        if args.commande == "lancer":
            chemin = resoudre(args.candidat)
            if args.a_blanc:
                return int(executer.a_blanc(chemin, env, o.afficher))
            p = executer.lancer(
                chemin, env, o, ecrire_rapport=rapport.ecrire_et_attacher
            )
            return int(p.code)
        if args.commande == "promouvoir":
            return int(deployer.promouvoir(args.livraison, args.motif, env, o))
        if args.commande == "revenir":
            return int(deployer.revenir(args.motif, env, o))
        return int(deployer.etat(env, o))
    except Arret as arret:
        o.afficher(
            f"Refus — code {int(arret.code)} ({arret.code.name}) : {arret.motif}"
        )
        if arret.sortie:
            o.afficher(rapport.extrait(arret.sortie, (), ()))
        return int(arret.code)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(principal(sys.argv[1:]))
