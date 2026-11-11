"""`livrer retour --cible <nom> --vers <étiquette> --motif "…"` : le retour arrière.

CE QUI REVIENT : les images et les fichiers Compose **de l'étiquette visée** (lus par
`git show`, écrits dans un dossier temporaire ; les chemins relatifs restent ceux du
dépôt — secrets, configurations hors dépôt — par `--project-directory`). Une image
absente du poste est construite depuis un arbre de travail temporaire à l'étiquette
(`git worktree`, retiré après), et c'est journalisé.

CE QUI NE REVIENT PAS : les migrations de la base. Il n'y a pas de `down` (règle de la
base) : une migration se corrige en avant, ou la base se restaure depuis une sauvegarde,
sur décision humaine. `retour` ne lance jamais `migrate.py` que pour `status`.

La santé est jugée **aux versions de l'étiquette visée** (pour `chaine-livraison` :
l'API 1.4.0, l'application 0.1.0, sans catalogue ni comptes).
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from livraison import cibles, depot, images, sante
from livraison.codes import Arret, Code
from livraison.deployer import (
    compteur,
    exiger_autre_arretee,
    exiger_fichiers,
    lever_et_verifier,
    nouvelle_entree,
    servies_texte,
    texte_migrations,
)
from livraison.images import Construite, Image
from livraison.journal import Relecture
from livraison.outils import Commande, Outils

PHRASE_MIGRATIONS = (
    "Les migrations de la base ne sont pas revenues ({n}) ; correction en avant, ou"
    " restauration d'une sauvegarde sur décision humaine"
)
DossierTemporaire = Callable[[], tempfile.TemporaryDirectory[str]]


def dossier_temporaire() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="livrer_retour_")


def extraire_compose(
    o: Outils, etiquette: str, fichiers: tuple[str, ...], dossier: Path
) -> tuple[Path, ...]:
    """Les fichiers Compose de l'étiquette, écrits sous `dossier` à leur chemin."""
    sortis: list[Path] = []
    for f in fichiers:
        texte = depot.fichier_a(o, etiquette, f)
        if texte is None:
            raise Arret(Code.PREREQUIS, f"{f} absent à l'étiquette {etiquette}")
        chemin = dossier / f
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(texte, encoding="utf-8")
        sortis.append(chemin)
    return tuple(sortis)


def images_du_retour(
    o: Outils, etiquette: str, liste: list[Image], dossier: Path
) -> tuple[list[Construite], list[str]]:
    """Les images présentes ; les absentes construites depuis un arbre de travail
    temporaire à l'étiquette, retiré ensuite."""
    absentes = [i for i in liste if images.empreinte(o, i.ref) is None]
    notes: list[str] = []
    faites: list[Construite] = []
    if absentes:
        arbre = dossier / "arbre"
        ajout = o.lanceur(
            Commande(
                "git.worktree.add",
                ("git", "worktree", "add", "--detach", str(arbre), etiquette),
                dossier=o.racine,
            )
        )
        if ajout.code != 0:
            raise Arret(
                Code.CONSTRUCTION,
                "arbre de travail de l'étiquette impossible",
                ajout.stderr,
            )
        try:
            faites = images.construire(o, absentes, arbre=arbre)
        finally:
            o.lanceur(
                Commande(
                    "git.worktree.remove",
                    ("git", "worktree", "remove", "--force", str(arbre)),
                    dossier=o.racine,
                )
            )
        notes.append(
            "Images absentes du poste, construites depuis l'étiquette : "
            + ", ".join(i.ref for i in absentes)
            + "."
        )
    deja = images.presentes(o, [i for i in liste if i not in absentes])
    return deja + faites, notes


def retour(
    o: Outils,
    nom: str,
    vers: str,
    motif: str,
    *,
    relire: Relecture | None = None,
    temporaire: DossierTemporaire = dossier_temporaire,
) -> Code:
    if not motif.strip():
        raise Arret(Code.PREREQUIS, 'le motif est obligatoire (--motif "…")')
    cible = cibles.lire(nom)
    visee = depot.etiquette_connue(o, vers)
    liste = images.images_de(o, visee.nom)
    exiger_autre_arretee(o, cible)
    exiger_fichiers(o, cible)
    at = sante.attendus(o, visee.nom, cible)
    entree = nouvelle_entree(o, cible, visee, "retour arrière")
    entree.motif = motif.strip()
    entree.quittee = (
        f"arbre à `{depot.etiquette_de_head(o) or 'HEAD'}` ; servies :"
        f" {servies_texte(o, cible)}"
    )
    entree.migrations_avant = texte_migrations(o, cible.base)
    entree.compteur_avant = compteur(o, cible)
    entree.notes.append(PHRASE_MIGRATIONS.format(n=entree.migrations_avant) + ".")
    with temporaire() as tmp:
        dossier = Path(tmp)
        entree.images, notes = images_du_retour(o, visee.nom, liste, dossier)
        entree.notes += notes
        fichiers = extraire_compose(o, visee.nom, cible.compose, dossier / "compose")
        down = cibles.commande_compose(
            cible, o, "down", "--remove-orphans", fichiers_compose=fichiers
        )
        up = cibles.commande_compose(
            cible, o, "up", "-d", "--no-build", fichiers_compose=fichiers
        )
        o.afficher(f"Retour arrière de {cible.nom} vers {visee.nom} : {entree.motif}.")
        return lever_et_verifier(o, entree, cible, at, [down, up], relire)
