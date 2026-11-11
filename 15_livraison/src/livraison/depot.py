"""Le dépôt : l'étiquette, l'arbre, les fichiers à une étiquette.

Construire et déployer exigent un arbre propre, sur `main`, exactement à une étiquette
annotée (décision 4 du cadre) : c'est elle qui désigne ce qui part en production.
Les journaux de déploiement (`15_livraison/journal/`) sont exceptés : `livrer` les
écrit dans l'arbre, et ils ne participent à aucune image ni à aucun Compose (0.1.1).
"""

from __future__ import annotations

from dataclasses import dataclass

from livraison.codes import Arret, Code
from livraison.outils import Commande, Outils, Sortie

BRANCHE_LIVRAISON = "main"
#: Le seul dossier où l'arbre peut différer de l'étiquette : les journaux de `livrer`.
CHEMIN_JOURNAL = "15_livraison/journal/"


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


def chemins_du_statut(ligne: str) -> list[str]:
    """Les chemins d'une ligne de `git status --porcelain` (`XY chemin`, ou `XY ancien
    -> nouveau` pour un renommage), guillemets de git retirés."""
    return [c.strip().strip('"') for c in ligne[3:].split(" -> ")]


def hors_journal(sortie: str) -> str | None:
    """Le premier chemin modifié hors de `15_livraison/journal/`, ou None : les journaux
    sont ce que `livrer` écrit, ils ne participent à aucune image ni à aucun Compose."""
    for ligne in sortie.splitlines():
        if not ligne.strip():
            continue
        for chemin in chemins_du_statut(ligne):
            if not chemin.startswith(CHEMIN_JOURNAL):
                return chemin
    return None


def exiger_arbre_propre(o: Outils) -> None:
    """L'arbre propre, les journaux exceptés ; sinon refus qui nomme le premier
    fichier en cause."""
    s = git(o, "status", "status", "--porcelain")
    if s.code != 0:
        raise Arret(
            Code.PREREQUIS,
            "arbre non propre : une livraison part d'un arbre propre, à l'étiquette"
            " (git status a échoué)",
            s.stderr,
        )
    en_cause = hors_journal(s.stdout)
    if en_cause is not None:
        raise Arret(
            Code.PREREQUIS,
            "arbre non propre : une livraison part d'un arbre propre, à l'étiquette"
            f" (premier fichier en cause : {en_cause} ; seuls les journaux de"
            f" {CHEMIN_JOURNAL} sont admis)",
        )


def exiger_etiquette(o: Outils) -> Etiquette:
    """L'arbre propre (journaux exceptés), sur `main`, à une étiquette annotée ; rend
    l'étiquette."""
    exiger_arbre_propre(o)
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
