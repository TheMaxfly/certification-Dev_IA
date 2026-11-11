"""La commande du premier administrateur (E4, lot 1, spec 03, § 4) :

    APPLICATION_ENVIRONNEMENT=preproduction \\
    APPLICATION_BASE_DSN='postgresql://app_conseiller@localhost/apimanga_preprod' \\
      make app-admin-creer          # soit : python -m application.admin creer-admin

Elle lit l'environnement et le DSN (sans mot de passe : `~/.pgpass`), applique la garde
d'environnement, demande le pseudo, le nom affiché, puis le mot de passe **deux fois,
sans écho** (`getpass`), aux règles de l'inscription. Elle crée le compte `admin` — sans
mot de passe à changer : c'est son créateur qui l'a choisi — et sa ligne de journal
`compte_cree`, sans auteur. Jamais de mot de passe en argument, en variable
d'environnement ni dans une sortie : sans terminal, elle refuse (code 2) plutôt que de
lire un mot de passe en écho.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import warnings
from collections.abc import Callable, Mapping, Sequence

import psycopg

from application import comptes
from application.base import Base, BaseIndisponible
from application.comptes import CompteInvalide, PseudoPris
from application.config import ConfigurationInvalide, charger_comptes
from application.metriques import Metriques
from application.motdepasse import Hacheur


def creer_admin(
    environ: Mapping[str, str],
    lire: Callable[[str], str],
    lire_secret: Callable[[str], str],
    ecrire: Callable[[str], None],
    hacheur: Hacheur | None = None,
) -> int:
    """0 : créé ; 1 : refusé (règle, pseudo pris, base) ; 2 : configuration."""
    try:
        environnement, dsn, regles = charger_comptes(environ)
    except ConfigurationInvalide as exc:
        ecrire(str(exc))
        return 2
    ecrire(f"Premier administrateur, environnement {environnement}.")
    pseudo = comptes.normaliser(lire("Pseudo : "))
    if not (
        comptes.FORME_PSEUDO.fullmatch(pseudo)
        and regles.pseudo_min <= len(pseudo) <= regles.pseudo_max
    ):
        ecrire(
            f"Pseudo refusé : de {regles.pseudo_min} à {regles.pseudo_max} caractères,"
            " lettres minuscules sans accent, chiffres, tiret ou souligné."
        )
        return 1
    nom = lire("Nom affiché : ").strip()
    if not comptes.nom_valide(nom, regles.nom_affiche_max):
        ecrire(
            f"Nom affiché refusé : de 1 à {regles.nom_affiche_max} caractères"
            " imprimables."
        )
        return 1
    mot_de_passe = lire_secret("Mot de passe (sans écho) : ")
    if not regles.mot_de_passe_min <= len(mot_de_passe) <= regles.mot_de_passe_max:
        ecrire(
            f"Mot de passe refusé : de {regles.mot_de_passe_min}"
            f" à {regles.mot_de_passe_max} caractères."
        )
        return 1
    if lire_secret("Le même, une seconde fois : ") != mot_de_passe:
        ecrire("Les deux mots de passe ne correspondent pas : rien n'est créé.")
        return 1
    base = Base(dsn, regles.delai_base_s, Metriques())
    try:
        compte = asyncio.run(
            comptes.creer(
                base,
                hacheur if hacheur is not None else Hacheur(),
                pseudo=pseudo,
                nom_affiche=nom,
                role="admin",
                mot_de_passe=mot_de_passe,
                doit_changer_mdp=False,
                par_compte_id=None,
            )
        )
    except PseudoPris:
        ecrire("Ce pseudo est déjà pris : rien n'est créé.")
        return 1
    except BaseIndisponible:
        ecrire("La base ne répond pas : rien n'est créé.")
        return 1
    except CompteInvalide:
        ecrire("La base refuse ce compte (une contrainte) : rien n'est créé.")
        return 1
    except psycopg.Error as exc:
        # Le nom de l'erreur seul : son message peut citer des valeurs.
        ecrire(f"La base refuse l'écriture ({type(exc).__name__}) : rien n'est créé.")
        return 1
    ecrire(f"Compte administrateur créé (identifiant interne {compte.id}).")
    return 0


def principal(argv: Sequence[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        prog="python -m application.admin",
        description="Les gestes d'administration en ligne de commande.",
    )
    sous = parseur.add_subparsers(dest="commande", required=True)
    sous.add_parser("creer-admin", help="créer le premier compte administrateur")
    parseur.parse_args(argv)
    if not sys.stdin.isatty():
        print(
            "creer-admin exige un terminal : le mot de passe se tape sans écho.",
            file=sys.stderr,
        )
        return 2
    try:
        return creer_admin(os.environ, input, lire_sans_echo, print)
    except getpass.GetPassWarning:
        print("Le terminal ne coupe pas l'écho : rien n'est créé.", file=sys.stderr)
        return 2


def lire_sans_echo(invite: str) -> str:
    """`getpass`, sans son repli : là où il afficherait la saisie, une erreur."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass(invite)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(principal())
