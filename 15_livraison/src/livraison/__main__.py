"""`livrer` — construire, déployer par cible, santé, retour arrière, arrêt, état.

    livrer construire [--reconstruire] [--a-blanc]
    livrer deployer --cible staging|production [--a-blanc]
    livrer sante    --cible staging|production [--etiquette <étiquette>]
    livrer retour   --cible staging|production --vers <étiquette> --motif "…"
    livrer arreter  --cible staging|production
    livrer etat

Lancées par `make livrer ACTION=… CIBLE=…` et `make livrer-etat` ; le code de sortie est
le code nommé (`livraison.codes`) : 0 fait et vert, 1 santé rouge, 20 un préalable…
"""

from __future__ import annotations

import argparse
import sys

from livraison import cibles, deployer, depot, images, retour
from livraison.codes import Arret, Code
from livraison.outils import Outils, http_reel, lancer_reel


def analyseur() -> argparse.ArgumentParser:
    a = argparse.ArgumentParser(
        prog="livrer", description=(__doc__ or "").splitlines()[0]
    )
    sous = a.add_subparsers(dest="commande", required=True)
    c = sous.add_parser("construire", help="les images de la pile, à l'étiquette")
    c.add_argument(
        "--reconstruire",
        action="store_true",
        help="réécrit une étiquette d'image d'empreinte différente (journalisé)",
    )
    c.add_argument("--a-blanc", action="store_true", help="affiche, ne construit rien")
    d = sous.add_parser("deployer", help="lève la pile de l'étiquette sur la cible")
    d.add_argument("--cible", required=True, choices=cibles.NOMS)
    d.add_argument("--a-blanc", action="store_true", help="affiche, ne lève rien")
    s = sous.add_parser("sante", help="les contrôles de santé de la cible")
    s.add_argument("--cible", required=True, choices=cibles.NOMS)
    s.add_argument(
        "--etiquette",
        help="la référence jugée (par défaut : l'étiquette du dernier journal de la"
        " cible ; sans journal, celle de HEAD)",
    )
    r = sous.add_parser("retour", help="redéploie une étiquette antérieure")
    r.add_argument("--cible", required=True, choices=cibles.NOMS)
    r.add_argument("--vers", required=True)
    r.add_argument("--motif", required=True)
    ar = sous.add_parser("arreter", help="arrête la cible")
    ar.add_argument("--cible", required=True, choices=cibles.NOMS)
    sous.add_parser("etat", help="les deux cibles")
    return a


def construire(o: Outils, reconstruire: bool, a_blanc: bool) -> Code:
    if a_blanc:
        reference = depot.etiquette_de_head(o) or "HEAD"
        try:
            depot.exiger_etiquette(o)
            o.afficher(f"À blanc — construction à l'étiquette {reference}.")
        except Arret as arret:
            o.afficher(
                f"À blanc — refuserait : {arret.motif} (référence lue : {reference})"
            )
        images.construire(o, images.images_de(o, reference), a_blanc=True)
        return Code.OK
    etiquette = depot.exiger_etiquette(o)
    faites = images.construire(
        o, images.images_de(o, etiquette.nom), reconstruire=reconstruire
    )
    images.noter_construction(o, etiquette.nom, faites)
    o.afficher(
        f"Construction à l'étiquette {etiquette.nom} ({etiquette.commit[:12]}) :"
    )
    o.afficher("| Image | Empreinte | Statut |")
    o.afficher("|---|---|---|")
    for f in faites:
        o.afficher(f"| `{f.image.ref}` | `{f.empreinte}` | {f.statut} |")
    return Code.OK


def principal(arguments: list[str], outils: Outils | None = None) -> int:
    args = analyseur().parse_args(arguments)
    o = outils or Outils(lanceur=lancer_reel, http=http_reel)
    try:
        if args.commande == "construire":
            return int(construire(o, args.reconstruire, args.a_blanc))
        if args.commande == "deployer":
            return int(deployer.deployer(o, args.cible, a_blanc=args.a_blanc))
        if args.commande == "sante":
            return int(deployer.verifier_sante(o, args.cible, args.etiquette))
        if args.commande == "retour":
            return int(retour.retour(o, args.cible, args.vers, args.motif))
        if args.commande == "arreter":
            return int(deployer.arreter(o, args.cible))
        return int(deployer.etat(o))
    except Arret as arret:
        o.afficher(
            f"Refus — code {int(arret.code)} ({arret.code.name}) : {arret.motif}"
        )
        return int(arret.code)


def main() -> None:  # pragma: no cover — le point d'entrée
    sys.exit(principal(sys.argv[1:]))


if __name__ == "__main__":  # pragma: no cover
    main()
