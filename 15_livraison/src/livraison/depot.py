"""Le dépôt : l'étiquette, l'arbre, les fichiers à une étiquette.

Construire et déployer exigent un arbre propre, sur `main`, exactement à une étiquette
annotée (décision 4 du cadre) : c'est elle qui désigne ce qui part en production.
"""

from __future__ import annotations

from dataclasses import dataclass

from livraison.codes import Arret, Code
from livraison.outils import Commande, Outils, Sortie

BRANCHE_LIVRAISON = "main"


@dataclass(frozen=True)
class Etiquette:
    nom: str
    commit: str


def git(o: Outils, nom: str, *arguments: str) -> Sortie:
    return o.lanceur(Commande(f"git.{nom}", ("git", *arguments), dossier=o.racine))


def annotee(o: Outils, nom: str) -> bool:
    s = git(o, "type", "cat-file", "-t", f"refs/tags/{nom}")
    return s.code == 0 and s.stdout.strip() == "tag"


def etiquette_connue(o: Outils, nom: str) -> Etiquette:
    """Une étiquette annotée existante, et son commit ; sinon refus."""
    if not nom or not annotee(o, nom):
        raise Arret(
            Code.PREREQUIS,
            f"étiquette {nom!r} inconnue, ou légère : une livraison se fait depuis une"
            " étiquette annotée",
        )
    commit = git(o, "commit", "rev-parse", f"refs/tags/{nom}^{{commit}}")
    return Etiquette(nom, commit.stdout.strip())


def exiger_etiquette(o: Outils) -> Etiquette:
    """L'arbre propre, sur `main`, à une étiquette annotée ; rend l'étiquette."""
    s = git(o, "status", "status", "--porcelain")
    if s.code != 0 or s.stdout.strip():
        raise Arret(
            Code.PREREQUIS,
            "arbre non propre : une livraison part d'un arbre propre, à l'étiquette",
        )
    branche = git(o, "branche", "branch", "--show-current").stdout.strip()
    if branche != BRANCHE_LIVRAISON:
        raise Arret(
            Code.PREREQUIS,
            f"branche {branche or '(détachée)'} : une livraison part de"
            f" {BRANCHE_LIVRAISON}, à son étiquette",
        )
    d = git(o, "etiquette", "describe", "--exact-match", "--tags", "HEAD")
    nom = d.stdout.strip()
    if d.code != 0 or not nom:
        raise Arret(
            Code.PREREQUIS,
            "HEAD n'est à aucune étiquette : poser l'étiquette annotée d'abord"
            " (CONTRIBUTING.md, la livraison vers main)",
        )
    return etiquette_connue(o, nom)


def etiquette_de_head(o: Outils) -> str | None:
    """L'étiquette exacte de HEAD, s'il y en a une (pour l'affichage à blanc)."""
    d = git(o, "etiquette", "describe", "--exact-match", "--tags", "HEAD")
    return d.stdout.strip() or None if d.code == 0 else None


def fichier_a(o: Outils, reference: str, chemin: str) -> str | None:
    """Le contenu d'un fichier suivi à une référence (étiquette, `HEAD`) ; `None` s'il
    n'y existe pas."""
    s = git(o, "show", "show", f"{reference}:{chemin}")
    return s.stdout if s.code == 0 else None
