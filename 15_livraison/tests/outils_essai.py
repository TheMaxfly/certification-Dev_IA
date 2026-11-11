"""La relecture réelle par le hook, dans un dépôt jetable : ni les `.env` du poste, ni
son `~/.pgpass`, ni ses variables ne sont lus (HOME et la racine sont jetables)."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 — essai, commandes fixes
from pathlib import Path

from livraison import journal
from livraison.outils import Commande, Outils, Sortie, lancer_reel

DEPOT = Path(__file__).resolve().parents[2]


def depot_jetable(tmp: Path) -> Path:
    r = tmp / "hook"
    (r / ".githooks").mkdir(parents=True)
    for f in ("cles-fournisseurs", "controler-texte", "pre-push"):
        shutil.copy(DEPOT / ".githooks" / f, r / ".githooks" / f)
    subprocess.run(["git", "init", "-q", str(r)], check=True)  # noqa: S603,S607
    return r


def relecture_reelle(tmp: Path) -> journal.Relecture:
    r = depot_jetable(tmp)
    maison = tmp / "maison"
    maison.mkdir()

    def lanceur(c: Commande) -> Sortie:
        return lancer_reel(
            Commande(c.nom, c.argv, r, {**c.env, "HOME": str(maison)}, c.entree)
        )

    return journal.relecture_du_hook(
        Outils(lanceur=lanceur, http=lambda _u: (0, ""), racine=r)
    )
